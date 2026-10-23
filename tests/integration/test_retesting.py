"""T25 two-phase execution, real offline rerun, history and atomic alert coupling."""
from dataclasses import replace
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
from src.models import AgentLog, Alert, AuthScope, DetectionTask, Finding
from src.repositories.alerts import AlertTransaction
from src.repositories.retesting import RetestTransaction
from src.services.alerts import AlertService
from src.services.finding_verification import Principal, VerificationError
from src.services.retesting import RetestError, RetestResult, RetestService
from src.tools.sensitive import ScanDocument
from src.tools.sensitive_retest import FreshDocument
from retest_db import ACTOR, BASE, START, OBSERVED, END, KEY, OTHER_KEY, Clock, Runner, retest_database, sensitive_fixture, change


class RetestIntegrationTests(unittest.TestCase):
    def setUp(self):
        database = retest_database()
        self.repo, self.sessions = database.__enter__()
        self.addCleanup(database.__exit__, None, None, None)
        self.runner = Runner()
        self.service = RetestService(self.repo, self.runner, clock=Clock())

    def run_retest(self, key=KEY):
        return self.service.retest(101, ACTOR, request_id=key)

    def status(self):
        with self.sessions() as session:
            return session.get(Finding, 101).status

    def count(self):
        with self.sessions() as session:
            return session.scalar(select(func.count()).select_from(Alert))

    def test_clean_fixes_and_history_has_fingerprints(self):
        result = self.run_retest()
        self.assertEqual(self.status(), "FIXED")
        self.assertEqual(result["outcome"], "CLEAN")
        self.assertEqual(result["source_sha256"], "c" * 64)
        history = self.service.history(101, ACTOR)["items"]
        self.assertEqual([item["outcome"] for item in history], ["CLEAN", "RUNNING"])

    def test_match_remains_confirmed_and_alerts_once(self):
        self.runner.outcome = "MATCH"
        result = self.run_retest()
        self.assertEqual(self.status(), "CONFIRMED")
        self.assertEqual(result["alert_outcome"], "ALERT_CREATED")
        self.assertEqual(self.count(), 1)

    def test_again_clean_becomes_retested_and_keeps_first_last_seen(self):
        self.run_retest()
        self.runner.observed = "2026-10-17 10:01:00"
        self.service.clock = Clock("2026-10-17 10:00:00", "2026-10-17 10:02:00")
        self.assertEqual(self.run_retest(OTHER_KEY)["status"], "RETESTED")
        with self.sessions() as session:
            finding = session.get(Finding, 101)
            self.assertEqual(finding.first_seen, datetime.fromisoformat(BASE))
            self.assertEqual(finding.last_seen, datetime.fromisoformat(BASE))

    def test_recurrence_after_clean_reopens_alert_cycle_atomically(self):
        first = AlertService(self.repo).process(101, ACTOR, observed_at=BASE)
        self.run_retest()
        self.runner.outcome, self.runner.observed = "MATCH", "2026-10-17 10:01:00"
        self.service.clock = Clock("2026-10-17 10:00:00", "2026-10-17 10:02:00")
        result = self.run_retest(OTHER_KEY)
        self.assertEqual(result["status"], "CONFIRMED")
        self.assertNotEqual(result["alert_id"], first["alertId"])
        self.assertEqual(self.count(), 2)
        with self.sessions() as session:
            finding = session.get(Finding, 101)
            self.assertEqual(finding.raw_data["alerting"]["cycle"], 2)
            self.assertNotIn("retest_pending", finding.raw_data)

    def test_idempotency_does_not_rerun_or_double_notify(self):
        first, second = self.run_retest(), self.run_retest()
        self.assertEqual(self.runner.calls, 1)
        self.assertEqual(first["status"], second["status"])
        self.assertTrue(second["replayed"])
        change(self.sessions, status="CONFIRMED")
        replay = self.run_retest()
        self.assertEqual(replay["status"], "FIXED")
        self.assertEqual(replay["currentStatus"], "CONFIRMED")
        self.assertEqual(self.runner.calls, 1)

    def test_false_positive_is_not_repaired(self):
        change(self.sessions, status="FALSE_POSITIVE")
        with self.assertRaises(VerificationError) as error:
            self.run_retest()
        self.assertEqual(error.exception.code, 40902)
        self.assertEqual(self.runner.calls, 0)

    def test_inconclusive_never_marks_fixed(self):
        self.runner.outcome = "INCONCLUSIVE"
        with self.assertRaises(VerificationError) as error:
            self.run_retest()
        self.assertEqual(error.exception.code, 40903)
        self.assertEqual(self.status(), "CONFIRMED")
        self.assertEqual(self.service.history(101, ACTOR)["items"][0]["outcome"], "INCONCLUSIVE")

    def test_timeout_is_sanitized_and_replay_does_not_rerun(self):
        with patch.object(self.runner, "run", side_effect=TimeoutError("fixture-only-token")) as run:
            with self.assertRaises(VerificationError) as error:
                self.run_retest()
            self.assertEqual(error.exception.code, 50002)
            self.assertNotIn("fixture-only-token", str(error.exception))
            replay = self.run_retest()
            self.assertEqual(replay["error_code"], 50002)
            self.assertEqual(run.call_count, 1)
        self.assertEqual(self.status(), "CONFIRMED")

    def test_missing_runner_fails_closed(self):
        self.service.runner = None
        with self.assertRaises(VerificationError) as error:
            self.run_retest()
        self.assertEqual(error.exception.code, 40903)

    def test_stale_or_future_observation_cannot_fix(self):
        self.runner.observed = BASE
        with self.assertRaises(VerificationError):
            self.run_retest()
        self.runner.observed = "2099-01-01 00:00:00"
        with self.assertRaises(VerificationError):
            self.run_retest(OTHER_KEY)
        self.assertEqual(self.status(), "CONFIRMED")

    def test_invalid_digest_and_missing_coverage_cannot_fix(self):
        with patch.object(self.runner, "run", return_value=RetestResult("CLEAN", OBSERVED, "fixture-only-token", "d" * 64, True)):
            with self.assertRaises(VerificationError):
                self.run_retest()
        with patch.object(self.runner, "run", return_value=RetestResult("CLEAN", OBSERVED, "c" * 64, "d" * 64, False)):
            with self.assertRaises(VerificationError):
                self.run_retest(OTHER_KEY)
        self.assertEqual(self.status(), "CONFIRMED")

    def test_business_change_during_execution_is_not_overwritten(self):
        self.runner.callback = lambda _: change(self.sessions, status="FALSE_POSITIVE")
        with self.assertRaises(VerificationError) as error:
            self.run_retest()
        self.assertEqual(error.exception.code, 40902)
        self.assertEqual(self.status(), "FALSE_POSITIVE")

    def test_other_task_observation_of_same_risk_invalidates_old_snapshot(self):
        def new_observation(_):
            with self.sessions.begin() as session:
                session.add(DetectionTask(id=3, scope_id=1, trigger_type="MANUAL", status="DONE"))
                session.flush()
                at = datetime.fromisoformat("2026-10-17 09:00:30")
                session.add(Finding(id=104, task_id=3, scope_id=1, finding_type="SENSITIVE", severity="MEDIUM",
                                    title="synthetic", tool_name="fixture-detector", raw_data={}, dedup_key="a" * 32,
                                    status="OPEN", first_seen=at, last_seen=at, updated_at=at))
        self.runner.callback = new_observation
        with self.assertRaises(VerificationError) as error:
            self.run_retest()
        self.assertEqual(error.exception.code, 40902)
        self.assertEqual(self.status(), "CONFIRMED")

    def test_asset_revoked_during_execution_preserves_state(self):
        def revoke(_):
            with self.sessions.begin() as session:
                session.get(AuthScope, 1).status = "DISABLED"
        self.runner.callback = revoke
        with self.assertRaises(VerificationError) as error:
            self.run_retest()
        self.assertEqual(error.exception.code, 40310)
        self.assertEqual(self.status(), "CONFIRMED")

    def test_terminal_audit_failure_rolls_back_status_and_alert_state(self):
        original = RetestTransaction.finish_run
        def fail(tx, finding, key, actor, at, data):
            if data["outcome"] != "RUNNING":
                raise RuntimeError("fixture-only-token")
            return original(tx, finding, key, actor, at, data)
        with patch.object(RetestTransaction, "finish_run", fail):
            with self.assertRaises(VerificationError) as error:
                self.run_retest()
        self.assertEqual(error.exception.code, 50000)
        self.assertEqual(self.status(), "CONFIRMED")
        with self.sessions() as session:
            raw = session.get(Finding, 101).raw_data
            self.assertIn("retest_pending", raw)
            self.assertNotIn("alerting", raw)

    def test_alert_audit_failure_rolls_back_retest_and_notification(self):
        self.runner.outcome = "MATCH"
        with patch.object(AlertTransaction, "audit", side_effect=RuntimeError("fixture-only-token")):
            with self.assertRaises(VerificationError):
                self.run_retest()
        self.assertEqual(self.status(), "CONFIRMED")
        self.assertEqual(self.count(), 0)

    def test_expired_pending_recovered_and_old_request_replays_expiration(self):
        with self.repo.transaction() as tx:
            scope, finding, _ = tx.lock_risk(101)
            tx.reserve(finding, KEY, 1, "2026-10-17 08:00:00", tx.snapshot(scope, finding))
        result = self.run_retest(OTHER_KEY)
        self.assertEqual(result["status"], "FIXED")
        self.assertEqual(self.run_retest(KEY)["outcome"], "EXPIRED")

    def test_authentication_and_scope_checked_before_runner(self):
        for principal in (None, Principal(2, "RULE_ADMIN", frozenset({1})), Principal(1, "SEC_OPS", frozenset({2}))):
            with self.assertRaises(VerificationError):
                self.service.retest(101, principal, request_id=KEY)
        self.assertEqual(self.runner.calls, 0)

    def test_history_read_permissions_and_no_raw_text(self):
        self.run_retest()
        reader = Principal(2, "RULE_ADMIN", frozenset({1}))
        self.assertEqual(self.service.history(101, reader)["items"][0]["status"], "FIXED")
        self.assertNotIn("fixture-only-token", json.dumps(self.service.history(101, reader)))
        with self.assertRaises(VerificationError):
            self.service.history(101, Principal(2, "RULE_ADMIN", frozenset({2})))

    def test_batch_is_bounded_and_individual_failures_are_visible(self):
        # Two different risk keys in scope 1; bindings are deliberately generic here.
        result = self.service.retest_scope(1, ACTOR, request_id=KEY, limit=1)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["nextAfterId"], 101)
        with self.assertRaises(VerificationError):
            self.service.retest_scope(1, ACTOR, request_id=KEY, limit=21)

    def test_corrupt_history_is_rejected(self):
        self.run_retest()
        with self.sessions.begin() as session:
            row = session.scalar(select(AgentLog).where(AgentLog.action.like("finding.retest:%")).order_by(AgentLog.id.desc()))
            data = json.loads(row.params_summary)
            data["extra"] = "fixture-only-token"
            row.params_summary = json.dumps(data)
        with self.assertRaises(VerificationError):
            self.service.history(101, ACTOR)

    def test_real_sensitive_runner_clean_and_hit(self):
        runner, _ = sensitive_fixture(self.sessions)
        self.service.runner = runner
        self.assertEqual(self.run_retest()["status"], "FIXED")
        runner.document_provider = lambda target: FreshDocument(ScanDocument(target.url, "DEMO_ACCESS_TOKEN=fixture-only-token"), "2026-10-17 10:01:00")
        self.service.clock = Clock("2026-10-17 10:00:00", "2026-10-17 10:02:00")
        self.assertEqual(self.run_retest(OTHER_KEY)["status"], "CONFIRMED")

    def test_rule_drift_blocks_before_document_read(self):
        runner, binding = sensitive_fixture(self.sessions)
        runner.bindings[101] = replace(binding, rules_sha256="0" * 64)
        runner.document_provider = lambda _: self.fail("must not obtain content for mismatched rules")
        self.service.runner = runner
        with self.assertRaises(VerificationError) as error:
            self.run_retest()
        self.assertEqual(error.exception.code, 40903)

    def test_binding_change_during_fetch_is_not_a_clean_result(self):
        runner, binding = sensitive_fixture(self.sessions)
        def obtain(_):
            runner.bindings[101] = replace(binding, rule_id="phone")
            return FreshDocument(ScanDocument(binding.url, "safe"), OBSERVED)
        runner.document_provider = obtain
        self.service.runner = runner
        with self.assertRaises(VerificationError) as error:
            self.run_retest()
        self.assertEqual(error.exception.code, 40903)
        self.assertEqual(self.status(), "CONFIRMED")

    def test_non_success_partial_or_wrong_target_not_treated_as_clean(self):
        runner, binding = sensitive_fixture(self.sessions)
        self.service.runner = runner
        runner.document_provider = lambda _: FreshDocument(ScanDocument(binding.url, "safe"), OBSERVED, status_code=403)
        with self.assertRaises(VerificationError):
            self.run_retest()
        runner.document_provider = lambda _: FreshDocument(ScanDocument(binding.url, "safe"), OBSERVED, coverage_complete=False)
        with self.assertRaises(VerificationError):
            self.run_retest(OTHER_KEY)
        self.assertEqual(self.status(), "CONFIRMED")

    def test_returned_document_for_other_url_is_not_clean(self):
        runner, _ = sensitive_fixture(self.sessions)
        runner.document_provider = lambda _: FreshDocument(ScanDocument("http://fixture.invalid/other", "safe"), OBSERVED)
        self.service.runner = runner
        with self.assertRaises(VerificationError) as error:
            self.run_retest()
        self.assertEqual(error.exception.code, 40903)
        self.assertEqual(self.status(), "CONFIRMED")


class RetestConcurrencyTests(unittest.TestCase):
    def test_second_request_cannot_run_while_first_detector_is_running(self):
        cache = ROOT / "tests/.cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            with retest_database(sqlite_file=Path(directory) / "retest.db") as (repo, sessions):
                entered, release = threading.Event(), threading.Event()
                runner = Runner(callback=lambda _: (entered.set(), release.wait(timeout=5)))
                first = RetestService(repo, runner, clock=Clock())
                results = []
                thread = threading.Thread(target=lambda: results.append(first.retest(101, ACTOR, request_id=KEY)))
                thread.start()
                try:
                    self.assertTrue(entered.wait(timeout=5))
                    with self.assertRaises(VerificationError) as error:
                        RetestService(repo, Runner(), clock=Clock()).retest(101, ACTOR, request_id=OTHER_KEY)
                    self.assertEqual(error.exception.code, 40902)
                finally:
                    release.set()
                    thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(results[0]["status"], "FIXED")

    def test_expired_old_clean_result_cannot_overwrite_new_match(self):
        cache = ROOT / "tests/.cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            with retest_database(sqlite_file=Path(directory) / "expired.db") as (repo, sessions):
                entered, release = threading.Event(), threading.Event()
                runner = Runner(callback=lambda _: (entered.set(), release.wait(timeout=5)))
                old = RetestService(repo, runner, clock=Clock())
                failures = []
                def execute_old():
                    try:
                        old.retest(101, ACTOR, request_id=KEY)
                    except VerificationError as error:
                        failures.append(error.code)
                thread = threading.Thread(target=execute_old)
                thread.start()
                try:
                    self.assertTrue(entered.wait(timeout=5))
                    newer = RetestService(repo, Runner("MATCH", "2026-10-17 09:11:00"),
                                          clock=Clock("2026-10-17 09:10:00", "2026-10-17 09:12:00"))
                    result = newer.retest(101, ACTOR, request_id=OTHER_KEY)
                    self.assertEqual(result["status"], "CONFIRMED")
                finally:
                    release.set()
                    thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(failures, [40902])
                with sessions() as session:
                    self.assertEqual(session.get(Finding, 101).status, "CONFIRMED")


if __name__ == "__main__":
    unittest.main()
