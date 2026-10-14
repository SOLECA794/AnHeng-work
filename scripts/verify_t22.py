"""Run deterministic T22 acceptance against isolated SQLite-compatible ORM fixtures."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "tests/support"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from src.api.finding_verification import create_verification_router
from src.models.entities import AgentLog, Finding
from src.services.finding_verification import Principal, RuleReview, VerificationError, VerificationService
from verification_db import SOURCE_HASH, verification_database


AT = "2026-10-14 09:00:00"
ACTOR = Principal(1, "SEC_OPS", frozenset({1}))


def snapshot(sessions, finding_id=101):
    with sessions() as session:
        finding = session.get(Finding, finding_id)
        audits = list(session.scalars(select(AgentLog).order_by(AgentLog.id)))
        return {"findingId": finding_id, "status": finding.status,
                "auditResults": [row.result_status for row in audits]}


def denied_code(operation):
    try:
        operation()
    except VerificationError as error:
        return error.code
    return 0


def evaluate():
    checks = []

    def add(name, passed, evidence):
        checks.append({"name": name, "passed": bool(passed), "evidence": evidence})

    with verification_database() as (repository, sessions):
        service = VerificationService(repository)
        receipt = service.verify(101, "CONFIRMED", "EVIDENCE_REVIEWED", ACTOR,
                                 note="fixture-only-token", observed_at=AT)
        state = snapshot(sessions)
        add("manual confirmation persists status and success audit", state["status"] == "CONFIRMED" and
            state["auditResults"] == ["SUCCESS"], {"receipt": receipt, "database": state})
        trail = service.get(101, Principal(2, "RULE_ADMIN", frozenset({1})))
        add("rule administrator can query verification trail", trail["status"] == "CONFIRMED" and
            receipt["verification"] in trail["trail"], trail)
        code = denied_code(lambda: service.verify(101, "FALSE_POSITIVE", "RULE_FALSE_MATCH", ACTOR, observed_at=AT))
        state = snapshot(sessions)
        add("repeat verification rejects stale OPEN", code == 40902 and state["status"] == "CONFIRMED" and
            state["auditResults"][-1] == "BLOCKED", {"code": code, "database": state})
        false_positive = service.verify(102, "FALSE_POSITIVE", "EXPECTED_PUBLIC_CONTENT", ACTOR, observed_at=AT)
        add("manual false positive persists", snapshot(sessions, 102)["status"] == "FALSE_POSITIVE", false_positive)

    for outcome, expected in (("MATCH", "CONFIRMED"), ("FALSE_MATCH", "FALSE_POSITIVE")):
        with verification_database() as (repository, sessions):
            service = VerificationService(repository)
            receipt = service.review(101, RuleReview(outcome, SOURCE_HASH, SOURCE_HASH, "fixture-rules-v1"),
                                     ACTOR, observed_at=AT)
            add("rule review " + outcome, receipt["status"] == expected and
                snapshot(sessions)["status"] == expected, receipt)

    for label, review in (("inconclusive review", RuleReview("INCONCLUSIVE", SOURCE_HASH, SOURCE_HASH, "fixture-v1")),
                          ("source snapshot mismatch", RuleReview("MATCH", "c" * 64, "c" * 64, "fixture-v1")),
                          ("reviewed snapshot mismatch", RuleReview("MATCH", SOURCE_HASH, "b" * 64, "fixture-v1"))):
        with verification_database() as (repository, sessions):
            service = VerificationService(repository)
            code = denied_code(lambda: service.review(101, review, ACTOR, observed_at=AT))
            state = snapshot(sessions)
            add(label + " keeps OPEN and records BLOCKED", code == 40903 and state["status"] == "OPEN" and
                state["auditResults"] == ["BLOCKED"], {"code": code, "database": state})

    for label, principal, code_expected in (
        ("no principal", None, 40100),
        ("administrator has no implicit write permission", Principal(3, "ADMIN", frozenset({1})), 40300),
        ("asset outside granted scopes", Principal(1, "SEC_OPS", frozenset({2})), 40310),
    ):
        with verification_database() as (repository, sessions):
            service = VerificationService(repository)
            code = denied_code(lambda: service.verify(101, "CONFIRMED", "EVIDENCE_REVIEWED", principal, observed_at=AT))
            state = snapshot(sessions)
            add(label, code == code_expected and state["status"] == "OPEN" and
                state["auditResults"] == ["BLOCKED"], {"code": code, "database": state})

    with verification_database() as (repository, sessions):
        class FailAuditRepository:
            @contextmanager
            def transaction(self):
                with repository.transaction() as transaction:
                    class Proxy:
                        def __getattr__(self, name):
                            return getattr(transaction, name)

                        def add_audit(self, event):
                            raise RuntimeError("fixture-only-token")
                    yield Proxy()
        service = VerificationService(FailAuditRepository())
        code = denied_code(lambda: service.verify(101, "CONFIRMED", "EVIDENCE_REVIEWED", ACTOR, observed_at=AT))
        state = snapshot(sessions)
        add("audit failure rolls back state update", code == 50000 and state["status"] == "OPEN" and
            not state["auditResults"], {"code": code, "database": state})

    with verification_database() as (repository, sessions):
        service = VerificationService(repository)
        app = FastAPI()
        app.include_router(create_verification_router(service, principal_dependency=lambda: ACTOR, clock=lambda: AT))
        with TestClient(app) as client:
            response = client.patch("/api/findings/101/status", json={"status": "CONFIRMED", "reasonCode": "EVIDENCE_REVIEWED"})
            trail = client.get("/api/findings/101/verification")
        add("opt-in API updates and reads real fixture state", response.json()["code"] == 0 and
            trail.json()["data"]["status"] == "CONFIRMED" and snapshot(sessions)["status"] == "CONFIRMED",
            {"patch": response.json(), "get": trail.json()})

    with verification_database() as (repository, sessions):
        app = FastAPI()
        app.include_router(create_verification_router(VerificationService(repository), clock=lambda: AT))
        with TestClient(app) as client:
            response = client.patch("/api/findings/101/status", headers={"X-Role": "SEC_OPS", "Authorization": "Bearer fake"},
                                    json={"status": "CONFIRMED", "reasonCode": "EVIDENCE_REVIEWED"})
        add("default principal rejects fabricated authorization headers", response.json()["code"] == 40100 and
            snapshot(sessions)["status"] == "OPEN", {"response": response.json(), "database": snapshot(sessions)})

    safe = "fixture-only-token" not in json.dumps(checks, ensure_ascii=False)
    add("receipts audits and API responses contain no raw note or fixture evidence", safe,
        {"note_sha256": hashlib.sha256(b"fixture-only-token").hexdigest()})
    return {"task": "T22", "fixture_version": "t22-sqlite-v1", "fixed_time": AT,
            "execution": "SQLite-compatible isolated ORM fixture + in-process FastAPI TestClient",
            "checks": checks, "passed_checks": sum(item["passed"] for item in checks),
            "total_checks": len(checks), "passed": all(item["passed"] for item in checks),
            "limitations": "No PostgreSQL, real JWT, deployed HTTP server, external scanner, or production database was used."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write the deterministic sanitized JSON acceptance report")
    arguments = parser.parse_args()
    try:
        report = evaluate()
    except Exception as error:
        print(f"T22 acceptance could not run: {type(error).__name__}", file=sys.stderr)
        return 1
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output is None:
        print(serialized, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(serialized, encoding="utf-8")
        verdict = "PASS" if report["passed"] else "FAIL"
        print(f"T22 {verdict}: {report['passed_checks']}/{report['total_checks']} checks passed")
        print(f"Sanitized report: {arguments.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
