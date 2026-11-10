"""T21 contracts on SQLite and, when explicitly supplied, private PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from src.auth.d_grants import SqlAlchemyDGrants
from src.core.database import create_database
from src.models import AgentLog, Alert, AuthScope, Base, DetectionTask, Finding, SitePage, SysUser
from src.repositories.alerts import SqlAlchemyAlertRepository
from src.repositories.finding_ingestion import FindingIngestionError, SqlAlchemyFindingIngestion
from src.repositories.reporting import SqlAlchemyReportRepository
from src.services.alerts import AlertService
from src.services.finding_verification import Principal
from src.services.findings import normalize_finding, to_orm_values
from src.services.reporting import period
from src.services.risk_grading import GradingContext, load_grading_policy


DAY1, DAY2, DAY3 = (f"2026-11-{day:02d} 09:00:00" for day in (8, 9, 10))


class IngestionCases:
    def setUp(self):
        self.sessions = self.database()
        self.ingestion = SqlAlchemyFindingIngestion(self.sessions, grading_policy=load_grading_policy())
        with self.sessions.begin() as session:
            self.user = SysUser(username="test", password="fixture", role="SEC_OPS", status=1)
            session.add(self.user)
            self.scopes = [AuthScope(domain=host, scope_type="WHOLE", status="ENABLED")
                           for host in ("site.invalid", "other.invalid")]
            session.add_all(self.scopes)
            session.flush()
            self.scope = self.scopes[0].id
            self.other = self.scopes[1].id
            self.tasks = [DetectionTask(scope_id=scope, trigger_type="MANUAL", status="DONE",
                                        created_at=datetime.fromisoformat(day))
                          for scope, day in ((self.scope, DAY1), (self.scope, DAY2), (self.other, DAY2), (self.scope, DAY3))]
            self.other_page = SitePage(scope_id=self.other, url="https://other.invalid/",
                                       normalized_url="https://other.invalid/", status="ACTIVE")
            session.add_all(self.tasks + [self.other_page])

    def record(self, task=0, *, at=None, target="https://site.invalid/check?key=synthetic", page_id=None):
        item = self.tasks[task]
        return normalize_finding({"type": "SENSITIVE", "url": target, "evidence": "synthetic private value",
                                  "page_id": page_id, "severity": "LOW"},
                                 scope_id=item.scope_id, task_id=item.id, tool_name="sensitive-scanner",
                                 seen_at=at or (DAY1, DAY2, DAY2, DAY3)[task])

    def put(self, task, *records, **kwargs):
        return self.ingestion.ingest(self.tasks[task].scope_id, self.tasks[task].id, list(records), **kwargs)

    def test_stable_anchor_and_exact_retry(self):
        first = self.put(0, self.record(), self.record())[0]
        second = self.put(1, self.record(1))[0]
        retry = self.put(1, self.record(1))[0]
        self.assertEqual((first.change, second.change, retry.change), ("INSERTED", "UPDATED", "UNCHANGED"))
        self.assertEqual({first.finding_id, second.finding_id, retry.finding_id}, {first.finding_id})
        with self.sessions() as session:
            row, = session.scalars(select(Finding)).all()
            self.assertEqual((row.task_id, row.occurrence, str(row.last_seen)), (self.tasks[0].id, 2, DAY2))
            self.assertNotIn("synthetic", str(row.raw_data) + row.url + row.evidence)

    def test_grading_status_alert_retest_and_audit_survive_late_observation(self):
        record = self.record(1)
        grading = GradingContext(self.scope, record["raw"]["identity"]["target_sha256"], "LOGIN", "INTERNET")
        first = self.put(1, record, grading_contexts={record["dedup_key"]: grading})[0]
        with self.sessions.begin() as session:
            row = session.get(Finding, first.finding_id)
            alert = Alert(finding_id=row.id, severity=row.severity, title="fixture", status="NEW")
            session.add(alert)
            session.flush()
            row.raw_data = dict(row.raw_data, alerting={"alert_id": alert.id}, retest_pending={"fixture": True})
            row.status = "CONFIRMED"
            row.updated_at = row.last_seen = datetime.fromisoformat(DAY3)
            session.add(AgentLog(user_id=self.user.id, task_id=row.task_id, log_type="OPERATION",
                                 action=f"finding.verify:{row.id}", result_status="SUCCESS"))
            expected = row.raw_data["grading"], row.severity, row.impact, alert.id
        self.put(0, self.record())
        with self.sessions() as session:
            row = session.get(Finding, first.finding_id)
            self.assertEqual((row.task_id, row.status, str(row.first_seen), str(row.last_seen), str(row.updated_at)),
                             (self.tasks[1].id, "CONFIRMED", DAY1, DAY3, DAY3))
            self.assertEqual((row.raw_data["grading"], row.severity, row.impact, row.raw_data["alerting"]["alert_id"]), expected)
            self.assertEqual(row.raw_data["retest_pending"], {"fixture": True})
            self.assertEqual(session.scalar(select(AgentLog.task_id)), self.tasks[1].id)

    def test_cross_asset_observations_are_separate_and_wrong_context_rejected(self):
        first = self.put(0, self.record())[0]
        other = self.put(2, self.record(2))[0]
        self.assertEqual(first.dedup_key, other.dedup_key)
        self.assertNotEqual(first.finding_id, other.finding_id)
        with self.assertRaises(FindingIngestionError):
            self.ingestion.ingest(self.scope, self.tasks[2].id, [])
        with self.assertRaises(FindingIngestionError):
            self.put(0, self.record(0, page_id=self.other_page.id))
        with self.sessions.begin() as session:
            session.get(AuthScope, self.scope).status = "DISABLED"
        with self.assertRaises(FindingIngestionError):
            self.put(0, self.record())

    def test_legacy_adoption_then_refuse_multiple_historical_anchors(self):
        legacy = self.record()
        with self.sessions.begin() as session:
            row = Finding(**to_orm_values(legacy))
            session.add(row)
            session.flush()
            first = row.id
        self.assertEqual(self.put(1, self.record(1))[0].finding_id, first)
        with self.sessions.begin() as session:
            session.add(Finding(**to_orm_values(self.record(1))))
        with self.assertRaisesRegex(FindingIngestionError, "multiple historical"):
            self.put(3, self.record(3))

    def test_conflicting_history_rolls_back_entire_batch(self):
        r0, r1 = sorted([self.record(target=f"https://site.invalid/{index}") for index in range(2)],
                        key=lambda record: record["dedup_key"])
        with self.sessions.begin() as session:
            row = Finding(**to_orm_values(r1))
            row.raw_data = {"legacy": "not safe"}
            session.add(row)
        with self.assertRaises(FindingIngestionError):
            self.put(0, r0, r1)
        with self.sessions() as session:
            self.assertEqual(session.scalars(select(Finding.dedup_key)).all(), [r1["dedup_key"]])

    def test_reports_follow_observation_task_dates_without_recreating_rows(self):
        anchor = self.put(0, self.record())[0]
        self.put(1, self.record(1))
        self.put(2, self.record(2))
        repo = SqlAlchemyReportRepository(self.sessions)
        principal = Principal(self.user.id, "SEC_OPS", frozenset({self.scope}))
        for begin, expected_count, expected_tasks in (
            ("2026-11-09", 1, [self.tasks[1].id]),
            ("2026-11-08", 2, [self.tasks[0].id, self.tasks[1].id]),
        ):
            with self.subTest(begin=begin):
                _, report = repo.prepare(self.scope, period(begin, "2026-11-09"), principal, DAY3)
                self.assertEqual(report["summary"]["findingRows"], 1)
                self.assertEqual(report["findings"][0]["findingIds"], [anchor.finding_id])
                self.assertEqual(report["findings"][0]["taskIds"], expected_tasks)
                self.assertEqual(report["findings"][0]["lastSeen"], DAY2)
                self.assertEqual([day["distinctRisks"] for day in report["trend"]], [1] * expected_count)

    def test_concurrent_new_rounds_serialize(self):
        barrier = Barrier(2)
        def run(index):
            barrier.wait(timeout=5)
            return self.put(index, self.record(index))[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, (0, 1)))
        self.assertEqual(len({result.finding_id for result in results}), 1)
        self.assertEqual(sorted(result.change for result in results), ["INSERTED", "UPDATED"])
        with self.sessions() as session:
            self.assertEqual(session.scalars(select(Finding.occurrence)).all(), [2])

    def test_grants_default_empty_replacement_rejects_invalid_asset_and_revoke_is_persisted(self):
        resolver = SqlAlchemyDGrants(self.sessions)
        self.assertEqual(resolver.resolve(self.user.id), frozenset())
        resolver.replace(self.user.id, frozenset({self.scope, self.other}))
        with self.assertRaises(ValueError):
            resolver.replace(self.user.id, frozenset({self.scope, 999999}))
        self.assertEqual(resolver.resolve(self.user.id), frozenset({self.scope, self.other}))
        with self.sessions.begin() as session:
            session.get(AuthScope, self.other).status = "DISABLED"
        self.assertEqual(resolver.resolve(self.user.id), frozenset({self.scope}))
        resolver.replace(self.user.id, frozenset())
        self.assertEqual(SqlAlchemyDGrants(self.sessions).resolve(self.user.id), frozenset())

    def test_alert_audit_failure_rolls_back_the_new_finding_and_alert(self):
        ingest = SqlAlchemyFindingIngestion(self.sessions, alert_service=AlertService(SqlAlchemyAlertRepository(self.sessions)))
        missing_user = Principal(99999, "SEC_OPS", frozenset({self.scope}))
        with self.assertRaises(FindingIngestionError):
            ingest.ingest(self.scope, self.tasks[0].id, [self.record()], alert_principal=missing_user)
        with self.sessions() as session:
            self.assertIsNone(session.scalar(select(Finding.id)))
            self.assertIsNone(session.scalar(select(Alert.id)))
            self.assertIsNone(session.scalar(select(AgentLog.id)))


class SQLiteIngestionTest(IngestionCases, unittest.TestCase):
    def database(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        engine, sessions = create_database("sqlite+pysqlite:///" + str(Path(folder.name, "findings.db").as_posix()),
                                           development=True)
        self.addCleanup(engine.dispose)
        return sessions


@unittest.skipUnless(os.getenv("ANHENG_D_PRIVATE_PG_URL"), "explicit private D PostgreSQL fixture not configured")
class PostgreSQLIngestionTest(IngestionCases, unittest.TestCase):
    def database(self):
        url = os.environ["ANHENG_D_PRIVATE_PG_URL"]
        admin = create_engine(url)
        self.addCleanup(admin.dispose)
        if admin.dialect.name != "postgresql":
            raise ValueError("D fixture requires PostgreSQL")
        schema = "d_ingestion_" + uuid4().hex
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))

        def remove_owned_schema():
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))

        self.addCleanup(remove_owned_schema)
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine, expire_on_commit=False)
