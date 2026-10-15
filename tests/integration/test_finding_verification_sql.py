"""T22 real SQLAlchemy transactions against isolated SQLite-compatible fixtures."""

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "tests/support"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from sqlalchemy import BigInteger, select
from sqlalchemy.dialects.postgresql import JSONB
from src.models.entities import AgentLog, AuthScope, Finding
from src.services.finding_verification import Principal, RuleReview, VerificationError, VerificationService
from verification_db import SOURCE_HASH, verification_database


AT = "2026-10-14 09:00:00"
ACTOR = Principal(1, "SEC_OPS", frozenset({1}))


class AuditFailureRepository:
    def __init__(self, repository):
        self.repository = repository

    @contextmanager
    def transaction(self):
        with self.repository.transaction() as transaction:
            class Proxy:
                def __getattr__(self, name):
                    return getattr(transaction, name)

                def add_audit(self, event):
                    raise RuntimeError("fixture-only-token")
            yield Proxy()


class FindingVerificationSQLTests(unittest.TestCase):
    def setUp(self):
        database = verification_database()
        self.repository, self.sessions = database.__enter__()
        self.addCleanup(database.__exit__, None, None, None)
        self.service = VerificationService(self.repository)

    def status(self, finding_id=101):
        with self.sessions() as session:
            return session.get(Finding, finding_id).status

    def audits(self):
        with self.sessions() as session:
            return list(session.scalars(select(AgentLog).order_by(AgentLog.id)))

    def test_commits_status_and_success_audit_readable_in_new_session(self):
        receipt = self.service.verify(101, "CONFIRMED", "EVIDENCE_REVIEWED", ACTOR,
                                      note="fixture-only-token", observed_at=AT)
        self.assertEqual(self.status(), "CONFIRMED")
        rows = self.audits()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].action, "finding.verify:101")
        self.assertEqual(rows[0].result_status, "SUCCESS")
        self.assertEqual(rows[0].user_id, 1)
        self.assertEqual(rows[0].task_id, 1)
        self.assertNotIn("fixture-only-token", rows[0].params_summary)
        trail = self.service.get(101, ACTOR)
        self.assertEqual(trail["status"], "CONFIRMED")
        self.assertIn(receipt["verification"], trail["trail"])

    def test_false_positive_commit_does_not_change_detection_timestamps(self):
        with self.sessions() as session:
            before = session.get(Finding, 102)
            initial = (before.first_seen, before.last_seen, before.occurrence)
        self.service.verify(102, "FALSE_POSITIVE", "RULE_FALSE_MATCH", ACTOR, observed_at=AT)
        with self.sessions() as session:
            after = session.get(Finding, 102)
            self.assertEqual(after.status, "FALSE_POSITIVE")
            self.assertEqual((after.first_seen, after.last_seen, after.occurrence), initial)

    def test_success_audit_failure_rolls_back_the_database_update(self):
        service = VerificationService(AuditFailureRepository(self.repository))
        with self.assertRaises(VerificationError) as raised:
            service.verify(101, "CONFIRMED", "EVIDENCE_REVIEWED", ACTOR, observed_at=AT)
        self.assertEqual(raised.exception.code, 50000)
        self.assertNotIn("fixture-only-token", str(raised.exception))
        self.assertEqual(self.status(), "OPEN")
        self.assertEqual(self.audits(), [])

    def test_denied_scope_persists_blocked_audit_without_evidence_or_url(self):
        with self.assertRaises(VerificationError) as raised:
            self.service.verify(103, "CONFIRMED", "EVIDENCE_REVIEWED", ACTOR, observed_at=AT)
        self.assertEqual(raised.exception.code, 40310)
        self.assertEqual(self.status(103), "OPEN")
        row = self.audits()[-1]
        self.assertEqual(row.result_status, "BLOCKED")
        self.assertEqual(json.loads(row.params_summary)["error_code"], 40310)
        self.assertNotIn("fixture-only-token", row.params_summary)
        self.assertNotIn("http", row.params_summary)

    def test_disabled_and_deleted_authorization_scopes_are_rechecked_from_database(self):
        for status, deleted in (("DISABLED", 0), ("ENABLED", 1)):
            with self.subTest(status=status, deleted=deleted):
                with self.sessions.begin() as session:
                    scope = session.get(AuthScope, 1)
                    scope.status, scope.deleted = status, deleted
                with self.assertRaises(VerificationError) as raised:
                    self.service.verify(101, "CONFIRMED", "EVIDENCE_REVIEWED", ACTOR, observed_at=AT)
                self.assertEqual(raised.exception.code, 40310)
                self.assertEqual(self.status(), "OPEN")

    def test_review_binds_to_persisted_source_snapshot(self):
        review = RuleReview("MATCH", "b" * 64, "c" * 64, "fixture-rules-v1")
        with self.assertRaises(VerificationError) as raised:
            self.service.review(101, review, ACTOR, observed_at=AT)
        self.assertEqual(raised.exception.code, 40903)
        self.assertEqual(self.status(), "OPEN")
        self.assertEqual(self.audits()[-1].result_status, "BLOCKED")

    def test_repository_compare_and_set_rejects_stale_open_status(self):
        with self.repository.transaction() as transaction:
            self.assertTrue(transaction.compare_and_set(101, 1, "OPEN", "CONFIRMED", AT))
        with self.repository.transaction() as transaction:
            self.assertFalse(transaction.compare_and_set(101, 1, "OPEN", "FALSE_POSITIVE", AT))
        self.assertEqual(self.status(), "CONFIRMED")

    def test_repository_compare_and_set_rejects_changed_source_snapshot(self):
        with self.repository.transaction() as transaction:
            original = transaction.get_finding(101)
        with self.sessions.begin() as session:
            session.get(Finding, 101).raw_data = {"source_sha256": "b" * 64}
        with self.repository.transaction() as transaction:
            changed = transaction.compare_and_set(101, 1, "OPEN", "CONFIRMED", AT,
                                                   source_sha256=original.source_sha256)
        self.assertFalse(changed)
        with self.sessions() as session:
            persisted = session.get(Finding, 101)
            self.assertEqual(persisted.status, "OPEN")
            self.assertEqual(persisted.raw_data["source_sha256"], "b" * 64)
        self.assertEqual(self.audits(), [])

    def test_corrupted_audit_known_values_and_extra_metadata_are_not_exported(self):
        self.service.verify(101, "CONFIRMED", "EVIDENCE_REVIEWED", ACTOR, observed_at=AT)
        original = self.audits()[0]
        baseline = json.loads(original.params_summary)
        for field in ("note_sha256", "reason_code", "extra_payload"):
            with self.subTest(field=field):
                corrupted = dict(baseline)
                corrupted[field] = "fixture-only-token"
                with self.sessions.begin() as session:
                    session.get(AgentLog, original.id).params_summary = json.dumps(corrupted)
                with self.assertRaises(VerificationError) as raised:
                    self.service.get(101, ACTOR)
                self.assertEqual(raised.exception.code, 50000)
                self.assertNotIn("fixture-only-token", str(raised.exception))
        self.assertEqual(self.status(), "CONFIRMED")

    def test_invalid_audit_note_and_review_fingerprints_roll_back_pending_update(self):
        receipt = self.service.verify(101, "CONFIRMED", "EVIDENCE_REVIEWED", ACTOR, observed_at=AT)
        for field in ("note_sha256", "review_sha256"):
            with self.subTest(field=field):
                event = deepcopy(receipt["verification"])
                event["finding_id"] = 102
                event[field] = "fixture-only-token"
                with self.assertRaises(ValueError):
                    with self.repository.transaction() as transaction:
                        self.assertTrue(transaction.compare_and_set(102, 1, "OPEN", "CONFIRMED", AT))
                        transaction.add_audit(event)
                self.assertEqual(self.status(102), "OPEN")
        self.assertEqual(len(self.audits()), 1)

    def test_fixture_cloning_does_not_change_production_types_and_enforces_foreign_keys(self):
        self.assertIsInstance(Finding.__table__.c.raw_data.type, JSONB)
        self.assertIsInstance(Finding.__table__.c.id.type, BigInteger)
        with self.sessions() as session:
            self.assertEqual(session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
