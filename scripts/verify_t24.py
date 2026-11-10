"""T24 acceptance using private SQLite fixtures and F's fixed local sensitive samples."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/support"))

from sqlalchemy import func, select
from src.models import Alert, AgentLog, Finding
from src.repositories.alerts import AlertTransaction
from src.services.alerts import AlertError, AlertPolicy, AlertService, load_alert_policy
from src.services.finding_verification import Principal, VerificationError
from src.services.findings import normalize_finding, to_orm_values
from src.tools.sensitive import AuthorizedScope, ScanDocument, SensitiveScanner, load_rules
from alert_db import ACTOR, AT, LATER, LATEST, alert_database, change


def evaluate():
    checks = []

    def scenario(name, run):
        try:
            with alert_database() as (repo, sessions):
                passed, evidence = run(AlertService(repo), sessions)
            checks.append(dict(name=name, passed=bool(passed), evidence=evidence))
        except Exception as error:
            checks.append(dict(name=name, passed=False, evidence=dict(error_type=type(error).__name__)))

    def process(service, identifier=101):
        return service.process(identifier, ACTOR, observed_at=AT)

    def create(service, sessions):
        result = process(service)
        inbox = service.list(ACTOR)
        return result["outcome"] == "ALERT_CREATED" and inbox["total"] == 1 and inbox["items"][0]["findingId"] == 101, dict(receipt=result, inbox=inbox)
    scenario("first hit persists one linked in-app notification", create)

    def retry(service, sessions):
        first, second = process(service), process(service)
        return first["alertId"] == second["alertId"] and second["outcome"] == "DUPLICATE", [first, second]
    scenario("same finding retry does not duplicate", retry)

    def cross_risk(service, sessions):
        results = [process(service, identifier) for identifier in (101, 102, 103)]
        return len({item["alertId"] for item in results}) == 3, results
    scenario("distinct keys and assets remain independent", cross_risk)

    def below(service, sessions):
        change(sessions, finding_type="VULN", severity="MEDIUM")
        low = process(service)
        change(sessions, severity="HIGH")
        high = process(service)
        return low["outcome"] == "BELOW_THRESHOLD" and high["outcome"] == "ALERT_CREATED", [low, high]
    scenario("threshold and later high severity upgrade", below)

    def types(service, sessions):
        results = []
        change(sessions, finding_type="TAMPER", severity="LOW")
        results.append(process(service))
        change(sessions, identifier=102, severity="LOW")
        results.append(process(service, 102))
        return all(item["outcome"] == "ALERT_CREATED" for item in results), results
    scenario("tamper and sensitive types trigger below severity threshold", types)

    def suppressed(service, sessions):
        change(sessions, status="FALSE_POSITIVE")
        false = process(service)
        change(sessions, status="OPEN", severity="INFO")
        info = process(service)
        return false["outcome"] == "SUPPRESSED" and info["outcome"] == "BELOW_THRESHOLD" and service.list(ACTOR)["total"] == 0, [false, info]
    scenario("false positives and informational findings never notify", suppressed)

    def disabled(service, sessions):
        service.policy = AlertPolicy(enabled=False)
        result = process(service)
        return result["outcome"] == "DISABLED", result
    scenario("disabled policy produces no in-app notification", disabled)

    def close(service, sessions):
        alert_id = process(service)["alertId"]
        service.mark_read(alert_id, ACTOR, observed_at=AT)
        service.set_status(alert_id, "HANDLED", ACTOR, observed_at=AT)
        service.set_status(alert_id, "CLOSED", ACTOR, observed_at=AT)
        change(sessions, last_seen=LATER)
        result = process(service)
        return result["outcome"] == "DUPLICATE" and service.list(ACTOR, status="CLOSED")["total"] == 1, dict(receipt=result, inbox=service.list(ACTOR))
    scenario("read handled and closed do not reopen notification cycle", close)

    def recurrence(service, sessions):
        first = process(service)
        change(sessions, status="FIXED", updated_at=LATER)
        repaired = process(service)
        change(sessions, status="OPEN", last_seen=LATEST)
        fresh = process(service)
        return repaired["outcome"] == "RESOLVED" and fresh["cycle"] == 2 and fresh["alertId"] != first["alertId"], [first, repaired, fresh]
    scenario("persisted repair followed by fresh detection creates new cycle", recurrence)

    def stale(service, sessions):
        process(service)
        change(sessions, status="RETESTED", updated_at=LATER)
        process(service)
        change(sessions, status="OPEN", last_seen=AT)
        result = process(service)
        return result["outcome"] == "STALE" and service.list(ACTOR)["total"] == 1, result
    scenario("old observations cannot reopen repaired risk", stale)

    def rollback(service, sessions):
        rejected = False
        with patch.object(AlertTransaction, "audit", side_effect=RuntimeError("fixture-only-token")):
            try:
                process(service)
            except AlertError as error:
                rejected = error.code == 50000 and "fixture-only-token" not in str(error)
        with sessions() as session:
            count = session.scalar(select(func.count()).select_from(Alert))
            has_state = "alerting" in session.get(Finding, 101).raw_data
        return rejected and count == 0 and not has_state, dict(sanitized_error=rejected, alert_count=count, state_present=has_state)
    scenario("audit failure rolls back both inbox and lifecycle state", rollback)

    def roles(service, sessions):
        alert_id = process(service)["alertId"]
        reader = Principal(2, "RULE_ADMIN", frozenset({1}))
        inbox = service.list(reader)
        denied = False
        try:
            service.set_status(alert_id, "CLOSED", reader)
        except VerificationError as error:
            denied = error.code == 40300
        other = service.list(Principal(1, "SEC_OPS", frozenset({2})))
        return denied and inbox["total"] == 1 and other["total"] == 0, dict(rule_admin_read=inbox["total"], rule_admin_write_denied=denied, other_scope_count=other["total"])
    scenario("explicit scope and role permissions protect inbox", roles)

    def idempotent(service, sessions):
        alert_id = process(service)["alertId"]
        first = service.mark_read(alert_id, ACTOR, observed_at=AT)
        second = service.mark_read(alert_id, ACTOR, observed_at=AT)
        service.set_status(alert_id, "CLOSED", ACTOR, observed_at=AT)
        denied = False
        try:
            service.set_status(alert_id, "HANDLED", ACTOR)
        except VerificationError as error:
            denied = error.code == 40902
        return first == second and denied, dict(idempotent_read=first == second, reopen_rejected=denied)
    scenario("read is idempotent and closed alerts cannot reopen", idempotent)

    def legacy(service, sessions):
        with sessions.begin() as session:
            session.add(Alert(finding_id=101, severity="HIGH", title="fixture-only-token", content="fixture-only-token", status="CLOSED", read_status=1, created_at=datetime.fromisoformat(AT)))
        result = process(service)
        inbox = service.list(ACTOR)
        return result["outcome"] == "DUPLICATE" and "fixture-only-token" not in json.dumps(inbox), dict(receipt=result, inbox=inbox)
    scenario("legacy closed alerts bootstrap dedup and redact display text", legacy)

    def samples(service, sessions):
        dataset = json.loads((ROOT / "tests/dataset/v1.0.0.json").read_text(encoding="utf-8"))
        scanner = SensitiveScanner(load_rules(ROOT / "src/tools/rules/sensitive-defaults.json"))
        files = {"D-01": "d/index.html", "D-02": "d/synthetic-env.txt", "D-03": "d/synthetic-backup.txt", "E-04": "e/index.html"}
        results, identifier = [], 104
        for case in dataset["cases"]:
            if case["id"] not in files:
                continue
            origin = dataset["sites"][case["site"]]
            text = (ROOT / "tests/fixtures/sites" / files[case["id"]]).read_text(encoding="utf-8")
            findings = scanner.scan(ScanDocument(origin.rstrip("/") + case["path"], text), [AuthorizedScope(case["site"], origin)])
            alerted = 0
            for finding in findings:
                normalized = normalize_finding(finding, task_id=1, scope_id=1, tool_name=finding["tool_name"], seen_at=AT)
                with sessions.begin() as session:
                    session.add(Finding(id=identifier, **to_orm_values(normalized), updated_at=datetime.fromisoformat(AT)))
                alerted += process(service, identifier)["outcome"] == "ALERT_CREATED"
                identifier += 1
            results.append(dict(case_id=case["id"], expected_positive=case["positive"], finding_count=len(findings), alert_count=alerted,
                                passed=bool(alerted) == case["positive"]))
        return len(results) == 4 and all(item["passed"] for item in results), results
    scenario("F positive and negative samples pass through T20 T21 persistence and T24", samples)

    cache = ROOT / "tests/.cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as directory:
        with alert_database(sqlite_file=Path(directory) / "alerts.db") as (repo, sessions):
            barrier = threading.Barrier(2)
            def concurrent(_):
                barrier.wait(timeout=5)
                return AlertService(repo).process(101, ACTOR, observed_at=AT)
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(concurrent, range(2)))
            outcomes = sorted(item["outcome"] for item in results)
            with sessions() as session:
                count = session.scalar(select(func.count()).select_from(Alert))
            checks.append(dict(name="independent SQLite connections serialize simultaneous creation",
                               passed=outcomes == ["ALERT_CREATED", "DUPLICATE"] and count == 1,
                               evidence=dict(outcomes=outcomes, alert_count=count)))

    encoded = json.dumps(checks, ensure_ascii=False)
    checks.append(dict(name="report exports no raw synthetic secret", passed="fixture-only-token" not in encoded,
                       evidence=dict(raw_secret_absent="fixture-only-token" not in encoded)))
    policy = load_alert_policy()
    return dict(task="T24", fixture_version="t24-sqlite-v1", fixed_time=AT, policy_version=policy.version, policy_sha256=policy.sha256,
                mode="private SQLite transactions and local samples; in-app inbox persistence only",
                checks=checks, passed_checks=sum(check["passed"] for check in checks), total_checks=len(checks),
                passed=all(check["passed"] for check in checks),
                limitations="No live PostgreSQL, real JWT, mounted production API, email delivery or external target scan was performed.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"T24 {'PASS' if report['passed'] else 'FAIL'}: {report['passed_checks']}/{report['total_checks']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
