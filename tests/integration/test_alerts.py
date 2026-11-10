"""T24 transactional risk cycles, in-app notification, roles, race and rollback."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/support"))
from sqlalchemy import select, func
from src.models import Alert, AgentLog, AuthScope, DetectionTask, Finding
from src.repositories.alerts import AlertTransaction
from src.services.alerts import AlertError, AlertPolicy, AlertService
from src.services.finding_verification import Principal, VerificationError
from alert_db import ACTOR, AT, LATER, LATEST, alert_database, change


class AlertServiceTests(unittest.TestCase):
    def setUp(self):
        database = alert_database()
        self.repo, self.sessions = database.__enter__()
        self.addCleanup(database.__exit__, None, None, None)
        self.service = AlertService(self.repo)

    def process(self, identifier=101):
        return self.service.process(identifier, ACTOR, observed_at=AT)

    def count(self, model=Alert):
        with self.sessions() as session:
            return session.scalar(select(func.count()).select_from(model))

    def clone(self, *, status="OPEN", last_seen=LATER):
        with self.sessions.begin() as session:
            session.add(DetectionTask(id=3, scope_id=1, trigger_type="MANUAL", status="DONE"))
            session.flush()
            session.add(Finding(id=104, task_id=3, scope_id=1, finding_type="SENSITIVE", severity="MEDIUM",
                                title="fixture-only-token", evidence="fixture-only-token", tool_name="fixture-scanner",
                                raw_data={}, dedup_key="a" * 32, status=status, occurrence=1,
                                first_seen=datetime.fromisoformat(last_seen), last_seen=datetime.fromisoformat(last_seen),
                                updated_at=datetime.fromisoformat(last_seen)))

    def test_first_hit_creates_linked_safe_inbox_notification(self):
        result = self.process()
        self.assertEqual(result["outcome"], "ALERT_CREATED")
        self.assertEqual(result["notification"], "IN_APP")
        with self.sessions() as session:
            alert = session.get(Alert, result["alertId"])
            self.assertEqual(alert.finding_id, 101)
            self.assertNotIn("fixture-only-token", alert.title + alert.content)
            self.assertEqual(alert.status, "NEW")
            self.assertEqual(alert.read_status, 0)
            self.assertEqual(session.get(Finding, 101).updated_at, datetime.fromisoformat(AT))

    def test_retry_does_not_duplicate(self):
        first, second = self.process(), self.process()
        self.assertEqual(second["outcome"], "DUPLICATE")
        self.assertEqual(first["alertId"], second["alertId"])
        self.assertEqual(self.count(), 1)

    def test_same_risk_new_finding_and_task_does_not_duplicate(self):
        first = self.process()
        self.clone()
        second = self.process(104)
        self.assertEqual(second["alertId"], first["alertId"])
        self.assertEqual(self.count(), 1)
        with self.sessions() as session:
            self.assertEqual(session.get(Finding, 101).raw_data["alerting"]["watermark"], LATER)

    def test_distinct_scope_or_key_have_independent_alerts(self):
        results = [self.process(identifier) for identifier in (101, 102, 103)]
        self.assertEqual(len({result["alertId"] for result in results}), 3)

    def test_low_vulnerability_skips_but_later_grade_upgrade_can_alert(self):
        change(self.sessions, finding_type="VULN", severity="LOW")
        self.assertEqual(self.process()["outcome"], "BELOW_THRESHOLD")
        change(self.sessions, severity="HIGH")
        self.assertEqual(self.process()["outcome"], "ALERT_CREATED")

    def test_false_positive_and_info_do_not_alert(self):
        change(self.sessions, status="FALSE_POSITIVE")
        self.assertEqual(self.process()["outcome"], "SUPPRESSED")
        change(self.sessions, status="OPEN", severity="INFO")
        self.assertEqual(self.process()["outcome"], "BELOW_THRESHOLD")
        self.assertEqual(self.count(), 0)

    def test_disabled_policy_keeps_no_notification(self):
        self.service = AlertService(self.repo, AlertPolicy(enabled=False))
        self.assertEqual(self.process()["outcome"], "DISABLED")
        self.assertEqual(self.count(), 0)

    def test_handled_closed_and_read_never_imply_risk_repaired(self):
        alert_id = self.process()["alertId"]
        self.service.mark_read(alert_id, ACTOR, observed_at=AT)
        self.service.set_status(alert_id, "HANDLED", ACTOR, observed_at=AT)
        self.service.set_status(alert_id, "CLOSED", ACTOR, observed_at=AT)
        change(self.sessions, last_seen=LATER)
        self.assertEqual(self.process()["outcome"], "DUPLICATE")
        self.assertEqual(self.count(), 1)

    def test_repair_then_fresh_detection_creates_new_cycle(self):
        first = self.process()
        change(self.sessions, status="FIXED", updated_at=LATER)
        self.assertEqual(self.process()["outcome"], "RESOLVED")
        change(self.sessions, status="OPEN", last_seen=LATEST)
        new = self.process()
        self.assertEqual(new["cycle"], 2)
        self.assertNotEqual(first["alertId"], new["alertId"])
        self.assertEqual(self.count(), 2)

    def test_retested_status_establishes_resolution_without_mutating_finding(self):
        self.process()
        change(self.sessions, status="RETESTED", updated_at=LATER)
        self.process()
        with self.sessions() as session:
            finding = session.get(Finding, 101)
            self.assertEqual(finding.status, "RETESTED")
            self.assertEqual(finding.updated_at, datetime.fromisoformat(LATER))
        self.process()
        with self.sessions() as session:
            self.assertEqual(session.get(Finding, 101).raw_data["alerting"]["watermark"], LATER)

    def test_old_or_equal_observation_cannot_reopen_resolved_risk(self):
        self.process()
        change(self.sessions, status="FIXED", updated_at=LATER)
        self.process()
        for seen in (AT, LATER):
            change(self.sessions, status="OPEN", last_seen=seen)
            self.assertEqual(self.process()["outcome"], "STALE")
        self.assertEqual(self.count(), 1)

    def test_out_of_order_fixed_event_cannot_resolve_newer_detection(self):
        change(self.sessions, last_seen=LATEST)
        self.process()
        self.clone(status="FIXED", last_seen=LATER)
        self.assertEqual(self.process(104)["outcome"], "STALE")
        self.assertEqual(self.process()["outcome"], "DUPLICATE")

    def test_new_finding_id_can_recur_after_resolution(self):
        self.process()
        change(self.sessions, status="FIXED", updated_at=LATER)
        self.process()
        self.clone(last_seen=LATEST)
        result = self.process(104)
        self.assertEqual(result["cycle"], 2)
        with self.sessions() as session:
            self.assertEqual(session.get(Alert, result["alertId"]).finding_id, 104)

    def test_legacy_closed_alert_is_bootstrapped_without_reemitting(self):
        with self.sessions.begin() as session:
            session.add(Alert(finding_id=101, severity="HIGH", title="fixture-only-token", content="fixture-only-token", status="CLOSED", read_status=1))
        result = self.process()
        self.assertEqual(result["outcome"], "DUPLICATE")
        self.assertEqual(self.count(), 1)

    def test_audit_failure_rolls_back_inbox_and_risk_state(self):
        with patch.object(AlertTransaction, "audit", side_effect=RuntimeError("fixture-only-token")):
            with self.assertRaisesRegex(AlertError, "告警事务失败") as error:
                self.process()
        self.assertNotIn("fixture-only-token", str(error.exception))
        self.assertEqual(self.count(), 0)
        with self.sessions() as session:
            self.assertNotIn("alerting", session.get(Finding, 101).raw_data)
        self.assertEqual(self.process()["outcome"], "ALERT_CREATED")

    def test_management_failure_rolls_back_status(self):
        alert_id = self.process()["alertId"]
        with patch.object(AlertTransaction, "audit", side_effect=RuntimeError("fixture-only-token")):
            with self.assertRaises(AlertError):
                self.service.set_status(alert_id, "CLOSED", ACTOR)
        with self.sessions() as session:
            self.assertEqual(session.get(Alert, alert_id).status, "NEW")

    def test_corrupt_cycle_state_fails_without_recreation(self):
        for state in (None, {"raw": "fixture-only-token"}):
            change(self.sessions, raw_data={"alerting": state})
            with self.assertRaises(AlertError):
                self.process()
        self.assertEqual(self.count(), 0)

    def test_wrong_alert_pointer_and_type_collision_rejected(self):
        other = self.process(103)["alertId"]
        self.process()
        with self.sessions.begin() as session:
            finding = session.get(Finding, 101)
            raw = dict(finding.raw_data)
            raw["alerting"] = dict(raw["alerting"], alert_id=other)
            finding.raw_data = raw
        with self.assertRaises(AlertError):
            self.process()
        self.clone()
        change(self.sessions, identifier=104, finding_type="TAMPER")
        with self.assertRaisesRegex(AlertError, "类型冲突"):
            self.process(104)

    def test_authorization_disabled_assets_and_absent_records(self):
        for actor in (None, Principal(2, "RULE_ADMIN", frozenset({1})), Principal(1, "SEC_OPS", frozenset()),
                      Principal(1, "SEC_OPS", frozenset({1}), active=False)):
            with self.assertRaises(VerificationError):
                self.service.process(101, actor)
        with self.sessions.begin() as session:
            session.get(AuthScope, 1).status = "DISABLED"
        with self.assertRaises(VerificationError):
            self.process()
        with self.assertRaises(VerificationError) as error:
            self.process(999)
        self.assertEqual(error.exception.code, 40400)
        self.assertEqual(self.count(), 0)

    def test_read_is_idempotent_and_all_roles_still_need_scope_grants(self):
        alert_id = self.process()["alertId"]
        before = self.count(AgentLog)
        admin = Principal(3, "ADMIN", frozenset({1}))
        self.assertEqual(self.service.mark_read(alert_id, admin)["readStatus"], 1)
        self.service.mark_read(alert_id, admin)
        self.assertEqual(self.count(AgentLog), before + 1)
        with self.assertRaises(VerificationError):
            self.service.mark_read(alert_id, Principal(3, "ADMIN", frozenset()))

    def test_status_management_permissions_and_no_reopen(self):
        alert_id = self.process()["alertId"]
        with self.assertRaises(VerificationError):
            self.service.set_status(alert_id, "CLOSED", Principal(2, "RULE_ADMIN", frozenset({1})))
        self.service.set_status(alert_id, "CLOSED", ACTOR)
        self.service.set_status(alert_id, "CLOSED", ACTOR)
        with self.assertRaises(VerificationError) as error:
            self.service.set_status(alert_id, "HANDLED", ACTOR)
        self.assertEqual(error.exception.code, 40902)

    def test_list_pagination_filters_scopes_and_redacts_legacy_text(self):
        for identifier in (101, 102, 103):
            self.process(identifier)
        reader = Principal(2, "RULE_ADMIN", frozenset({1}))
        page = self.service.list(reader, page_size=1)
        self.assertEqual(page["total"], 2)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual({item["scopeId"] for item in page["items"]}, {1})
        self.assertNotIn("fixture-only-token", json.dumps(page))
        self.assertEqual(self.service.list(reader, severity="CRITICAL")["total"], 0)
        with self.assertRaises(VerificationError):
            self.service.list(reader, scope_id=2)
        with self.sessions.begin() as session:
            session.get(AuthScope, 1).deleted = 1
        self.assertEqual(self.service.list(reader)["total"], 0)

    def test_invalid_pagination_ids_and_filter_enums(self):
        for options in (dict(page=True), dict(page_size=101), dict(status=[]), dict(severity="high")):
            with self.assertRaises(VerificationError):
                self.service.list(ACTOR, **options)
        with self.assertRaises(VerificationError):
            self.process(True)


class AlertConcurrencyTests(unittest.TestCase):
    def test_independent_db_connections_only_create_one_alert(self):
        cache = ROOT / "tests/.cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            with alert_database(sqlite_file=Path(directory) / "alerts.db") as (repo, sessions):
                barrier = threading.Barrier(2)
                def process(_):
                    barrier.wait(timeout=5)
                    return AlertService(repo).process(101, ACTOR, observed_at=AT)
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(process, range(2)))
                self.assertEqual(sorted(item["outcome"] for item in results), ["ALERT_CREATED", "DUPLICATE"])
                with sessions() as session:
                    self.assertEqual(session.scalar(select(func.count()).select_from(Alert)), 1)


if __name__ == "__main__":
    unittest.main()
