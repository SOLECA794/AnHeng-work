"""Repeatable T25 acceptance: private DB, fixed time and actual offline T20 rules."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/support"))
from src.models import Finding
from src.repositories.retesting import RetestTransaction
from src.services.alerts import AlertService
from src.services.finding_verification import Principal, VerificationError
from src.services.retesting import RetestService
from src.tools.sensitive import ScanDocument
from src.tools.sensitive_retest import FreshDocument
from retest_db import ACTOR, BASE, START, OBSERVED, END, KEY, OTHER_KEY, Clock, Runner, retest_database, sensitive_fixture, change


def evaluate():
    checks = []

    def scenario(name, run):
        try:
            with retest_database() as (repo, sessions):
                runner = Runner()
                service = RetestService(repo, runner, clock=Clock())
                passed, evidence = run(service, runner, sessions)
            checks.append(dict(name=name, passed=bool(passed), evidence=evidence))
        except Exception as error:
            checks.append(dict(name=name, passed=False, evidence=dict(error_type=type(error).__name__)))

    def run(service, key=KEY):
        return service.retest(101, ACTOR, request_id=key)

    def state(sessions):
        with sessions() as session:
            return session.get(Finding, 101).status

    def clean(service, runner, sessions):
        actual, _ = sensitive_fixture(sessions)
        service.runner = actual
        result = run(service)
        return result["status"] == "FIXED" and result["outcome"] == "CLEAN", result
    scenario("actual T20 original rule rerun clean marks fixed", clean)

    def match(service, runner, sessions):
        actual, _ = sensitive_fixture(sessions, current_text=(ROOT / "tests/fixtures/sites/d/index.html").read_text(encoding="utf-8"))
        service.runner = actual
        result = run(service)
        return result["status"] == "CONFIRMED" and result["outcome"] == "MATCH", result
    scenario("actual rule on F D-01 content still matching stays confirmed", match)

    def repeated(service, runner, sessions):
        first = run(service)
        runner.observed = "2026-10-17 10:01:00"
        service.clock = Clock("2026-10-17 10:00:00", "2026-10-17 10:02:00")
        second = run(service, OTHER_KEY)
        return first["status"] == "FIXED" and second["status"] == "RETESTED", [first, second]
    scenario("clean recheck after fixed becomes retested", repeated)

    def recurrence(service, runner, sessions):
        initial = AlertService(service.repository).process(101, ACTOR, observed_at=BASE)
        fixed = run(service)
        runner.outcome, runner.observed = "MATCH", "2026-10-17 10:01:00"
        service.clock = Clock("2026-10-17 10:00:00", "2026-10-17 10:02:00")
        appeared = run(service, OTHER_KEY)
        return fixed["alert_outcome"] == "RESOLVED" and appeared["alert_outcome"] == "ALERT_CREATED" and appeared["alert_id"] != initial["alertId"], [initial, fixed, appeared]
    scenario("repair resolves alert cycle and recurrence opens a new one", recurrence)

    def replay(service, runner, sessions):
        first, second = run(service), run(service)
        return runner.calls == 1 and second["replayed"], dict(first=first, replay=second, detector_calls=runner.calls)
    scenario("same request UUID replays without a second detector execution", replay)

    def uncertain(service, runner, sessions):
        runner.outcome = "INCONCLUSIVE"
        code = None
        try:
            run(service)
        except VerificationError as error:
            code = error.code
        return code == 40903 and state(sessions) == "CONFIRMED", dict(error_code=code, state=state(sessions))
    scenario("uncertain result never claims repair", uncertain)

    def timeout(service, runner, sessions):
        code = None
        with patch.object(runner, "run", side_effect=TimeoutError("fixture-only-token")):
            try:
                run(service)
            except VerificationError as error:
                code = error.code
        return code == 50002 and state(sessions) == "CONFIRMED", dict(error_code=code, state=state(sessions), history=service.history(101, ACTOR))
    scenario("timeout preserves state and sanitized trace", timeout)

    def no_adapter(service, runner, sessions):
        service.runner = None
        return uncertain(service, runner, sessions)
    scenario("missing adapter does not fabricate success", no_adapter)

    def drift(service, runner, sessions):
        actual, binding = sensitive_fixture(sessions)
        actual.bindings[101] = replace(binding, rules_sha256="0" * 64)
        service.runner = actual
        return uncertain(service, runner, sessions)
    scenario("changed rule snapshot blocks clean conclusion", drift)

    def partial(service, runner, sessions):
        actual, binding = sensitive_fixture(sessions)
        actual.document_provider = lambda _: FreshDocument(ScanDocument(binding.url, "safe"), OBSERVED, coverage_complete=False)
        service.runner = actual
        return uncertain(service, runner, sessions)
    scenario("incomplete current content is not repair evidence", partial)

    def stale(service, runner, sessions):
        runner.observed = BASE
        return uncertain(service, runner, sessions)
    scenario("old content cannot change repair state", stale)

    def business_change(service, runner, sessions):
        runner.callback = lambda _: change(sessions, status="FALSE_POSITIVE")
        code = None
        try:
            run(service)
        except VerificationError as error:
            code = error.code
        return code == 40902 and state(sessions) == "FALSE_POSITIVE", dict(error_code=code, state=state(sessions))
    scenario("concurrent business update is not overwritten", business_change)

    def rollback(service, runner, sessions):
        original = RetestTransaction.finish_run
        def fail(tx, finding, key, actor, at, data):
            if data["outcome"] != "RUNNING":
                raise RuntimeError("fixture-only-token")
            return original(tx, finding, key, actor, at, data)
        code = None
        with patch.object(RetestTransaction, "finish_run", fail):
            try:
                run(service)
            except VerificationError as error:
                code = error.code
        with sessions() as session:
            raw = session.get(Finding, 101).raw_data
            pending = "retest_pending" in raw
            alert_state = "alerting" in raw
        return code == 50000 and state(sessions) == "CONFIRMED" and pending and not alert_state, dict(error_code=code, state=state(sessions), pending_trace_retained=pending, alert_cycle_written=alert_state)
    scenario("terminal audit failure rolls back finding and alert cycle", rollback)

    def expiry(service, runner, sessions):
        with service.repository.transaction() as tx:
            scope, finding, _ = tx.lock_risk(101)
            tx.reserve(finding, KEY, 1, "2026-10-17 08:00:00", tx.snapshot(scope, finding))
        new = run(service, OTHER_KEY)
        old = run(service, KEY)
        return new["status"] == "FIXED" and old["outcome"] == "EXPIRED", [new, old]
    scenario("expired reservation is recovered with a durable prior outcome", expiry)

    def denied(service, runner, sessions):
        codes = []
        for actor in (None, Principal(2, "RULE_ADMIN", frozenset({1})), Principal(1, "SEC_OPS", frozenset({2}))):
            try:
                service.retest(101, actor, request_id=KEY)
            except VerificationError as error:
                codes.append(error.code)
        return codes == [40100, 40300, 40310] and runner.calls == 0, dict(error_codes=codes, detector_calls=runner.calls)
    scenario("identity role and asset boundaries precede detector execution", denied)

    def batch(service, runner, sessions):
        result = service.retest_scope(1, ACTOR, request_id=KEY, limit=1)
        return len(result["items"]) == 1 and result["nextAfterId"] == 101, result
    scenario("bounded site retest exposes individual results", batch)

    encoded = json.dumps(checks, ensure_ascii=False)
    checks.append(dict(name="report contains no raw synthetic secret or original target URL",
                       passed="fixture-only-token" not in encoded and "http://" not in encoded,
                       evidence=dict(raw_secret_absent="fixture-only-token" not in encoded, raw_url_absent="http://" not in encoded)))
    return dict(task="T25", fixture_version="t25-sqlite-v1", fixed_start=START,
                mode="private SQLite transactions, trusted result fixtures and actual offline T20 rules; no external scan",
                checks=checks, passed_checks=sum(check["passed"] for check in checks), total_checks=len(checks),
                passed=all(check["passed"] for check in checks),
                limitations="No live PostgreSQL, production authentication, mounted frontend, external scanner or platform integration was performed.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"T25 {'PASS' if report['passed'] else 'FAIL'}: {report['passed_checks']}/{report['total_checks']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
