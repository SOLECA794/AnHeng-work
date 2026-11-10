"""Opt-in host exercises a real signed user and stored grants through D routes."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from src.auth.d_grants import SqlAlchemyDGrants
from src.auth.persistence import SqlAlchemyUserStore
from src.core.config import Settings
from src.main import create_app
from src.models import AgentLog, Alert, AuthScope, Base, DetectionTask, Finding, SitePage
from src.services.discovery import content_hash
from src.services.finding_verification import Principal, _timestamp
from src.services.retesting import RetestDispatcher
from src.tools.page_retest import LINK_TOOL, TAMPER_TOOL, PageRetestBinding, PageRetestRunner, link_policy_sha256, tamper_policy_sha256
from src.tools.sensitive import ScanDocument
from src.tools.sensitive_retest import FreshDocument


class DRuntimeCases:
    def test_page_to_storage_to_retest_and_report_under_persisted_grants(self):
        url = "https://fixture.invalid/approved/page"
        external = "https://blocked.invalid/ref"
        clean = "<p>approved baseline</p>"
        body = f'<a href="{external}">Link</a><script src="{external}"></script> DEMO_ACCESS_TOKEN'
        baseline = content_hash(clean)
        blacklist = ["blocked.invalid"]
        bindings = {}
        page_runner = PageRetestRunner(
            bindings.get, lambda binding: FreshDocument(ScanDocument(binding.page_url, clean), _timestamp()),
            baseline_provider=lambda _: baseline, blacklist_provider=lambda _: blacklist,
        )
        dispatch = RetestDispatcher({("TAMPER", TAMPER_TOOL): page_runner,
                                     ("MALICIOUS_LINK", LINK_TOOL): page_runner,
                                     ("MALICIOUS_SCRIPT", LINK_TOOL): page_runner})
        with TemporaryDirectory() as folder:
            settings = Settings(_env_file=None, app_env="test", hengan_app_key="", hengan_app_secret="",
                                database_url=self.database_url(folder),
                                d_module_enabled=True, d_report_directory=str(Path(folder, "reports")))
            app = create_app(settings, d_retest_runner=dispatch)
            with TestClient(app) as client:
                sessions = app.state.sessions
                user = SqlAlchemyUserStore(sessions).add("fixture-operator", "fixture-test-password", "SEC_OPS")
                utc = datetime.now(timezone.utc)
                scan_at = _timestamp(utc - timedelta(days=1))
                with sessions.begin() as session:
                    scope = AuthScope(domain="fixture.invalid", scope_type="PATH", scope_value="/approved", status="ENABLED")
                    other = AuthScope(domain="other.invalid", scope_type="WHOLE", status="ENABLED")
                    session.add_all([scope, other])
                    session.flush()
                    task = DetectionTask(scope_id=scope.id, trigger_type="MANUAL", status="DONE",
                                         created_at=(utc - timedelta(days=1)).replace(tzinfo=None))
                    page = SitePage(scope_id=scope.id, url=url, normalized_url=url, status="ACTIVE")
                    session.add_all([task, page])
                login = client.post("/api/auth/login", json={"username": user.username, "password": "fixture-test-password"})
                headers = {"Authorization": "Bearer " + login.json()["data"]["token"]}
                grants = SqlAlchemyDGrants(sessions)
                grants.replace(user.id, frozenset({scope.id}))
                actor = Principal(user.id, "SEC_OPS", grants.resolve(user.id))
                fresh = FreshDocument(ScanDocument(url, body), scan_at)
                pipeline = app.state.d_page_detection
                results = pipeline.ingest_document(scope.id, task.id, page.id, fresh,
                    blacklist=blacklist, baseline_hash=baseline, alert_principal=actor)
                retry = pipeline.ingest_document(scope.id, task.id, page.id, fresh,
                    blacklist=blacklist, baseline_hash=baseline, alert_principal=actor)
                self.assertEqual(len(results), 4)
                self.assertEqual([item.change for item in retry], ["UNCHANGED"] * 4)
                with sessions() as session:
                    self.assertEqual(len(session.scalars(select(Alert)).all()), 4)
                    for row in session.scalars(select(Finding)):
                        kind = row.finding_type
                        if kind == "SENSITIVE":
                            self.assertNotIn("DEMO_ACCESS_TOKEN", str(row.raw_data) + row.evidence)
                            continue
                        target = url if kind == "TAMPER" else external
                        rule = {"TAMPER": "CONTENT_HASH", "MALICIOUS_LINK": "BLACKLISTED_LINK",
                                "MALICIOUS_SCRIPT": "BLACKLISTED_SCRIPT"}[kind]
                        digest = tamper_policy_sha256(baseline) if kind == "TAMPER" else link_policy_sha256(blacklist)
                        bindings[row.id] = PageRetestBinding(scope.id, row.dedup_key, url, kind, target, rule, digest)
                first = results[0].finding_id
                grants.replace(user.id, frozenset())
                self.assertEqual(client.get(f"/api/findings/{first}/verification", headers=headers).status_code, 403)
                grants.replace(user.id, frozenset({scope.id}))
                self.assertEqual(client.get(f"/api/findings/{first}/verification", headers=headers).status_code, 200)
                self.assertEqual(client.get(f"/api/alerts?scopeId={other.id}", headers=headers).status_code, 403)
                for identifier in bindings:
                    with sessions.begin() as session:
                        row = session.get(Finding, identifier)
                        # Fixtures represent an already committed scan from the previous day.
                        row.updated_at = datetime.fromisoformat(scan_at)
                    response = client.post(f"/api/findings/{identifier}/retest", headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["data"]["status"], "FIXED")
                generated = client.post("/api/reports/generate", headers=headers, json={
                    "scopeId": scope.id, "periodBegin": scan_at[:10], "periodEnd": scan_at[:10],
                })
                self.assertEqual(generated.status_code, 200, generated.text)
                pdf = client.get(generated.json()["data"]["pdfPath"], headers=headers)
                self.assertTrue(pdf.content.startswith(b"%PDF-"))
                with sessions() as session:
                    actors = session.scalars(select(AgentLog.user_id).where(AgentLog.action.like("finding.retest:%"))).all()
                    self.assertEqual(set(actors), {user.id})
                grants.replace(user.id, frozenset())
                self.assertEqual(client.get(f"/api/findings/{first}/verification", headers=headers).status_code, 403)


class SQLiteDRuntimeTest(DRuntimeCases, unittest.TestCase):
    def database_url(self, folder):
        return "sqlite+pysqlite:///" + Path(folder, "d.db").as_posix()


@unittest.skipUnless(os.getenv("ANHENG_D_PRIVATE_PG_URL"), "explicit private D PostgreSQL fixture not configured")
class PostgreSQLDRuntimeTest(DRuntimeCases, unittest.TestCase):
    def database_url(self, _folder):
        url = make_url(os.environ["ANHENG_D_PRIVATE_PG_URL"])
        admin = create_engine(url)
        self.addCleanup(admin.dispose)
        if admin.dialect.name != "postgresql":
            raise ValueError("D fixture requires PostgreSQL")
        schema = "d_runtime_" + uuid4().hex
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))

        def remove_owned_schema():
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))

        self.addCleanup(remove_owned_schema)
        app_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
        engine = create_engine(app_url)
        try:
            Base.metadata.create_all(engine)
        finally:
            engine.dispose()
        return app_url.render_as_string(hide_password=False)
