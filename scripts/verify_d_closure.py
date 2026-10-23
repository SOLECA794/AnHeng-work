"""T20–T26 integrated offline acceptance, using real services and one private DB.

This is an integration candidate, not a production collector or authentication
adapter. F v1.0.0 contents and a server-controlled principal are fixed inputs.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "tests/support"):
    sys.path.insert(0, str(directory))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import delete, func, select
from src.api.alerts import create_alert_router
from src.api.finding_verification import create_verification_router
from src.api.reporting import create_report_router
from src.api.retesting import create_retest_router
from src.models import AgentLog, Alert, AuthScope, DetectionTask, Finding, Report
from src.repositories.alerts import SqlAlchemyAlertRepository
from src.repositories.reporting import SqlAlchemyReportRepository
from src.repositories.retesting import SqlAlchemyRetestRepository
from src.services.alerts import AlertService
from src.services.finding_verification import Principal, VerificationService
from src.services.findings import deduplicate_findings, normalize_finding
from src.services.reporting import ReportService
from src.services.report_files import ReportStorage
from src.services.retesting import RetestService
from src.services.risk_grading import GradingContext, graded_orm_values, load_grading_policy
from src.tools.sensitive import AuthorizedScope, ScanDocument, SensitiveScanner, load_rules
from src.tools.sensitive_retest import FreshDocument, SensitiveRetestRunner, SensitiveRetestTarget, rules_fingerprint
from verification_db import verification_database

DAY = "2026-10-23"
BASE = DAY + " 09:00:00"
ACTOR = Principal(1, "SEC_OPS", frozenset({1}))
NAMESPACE = UUID("a431a9d1-6ef7-4c9f-92b5-5d4ebd2db9d0")
FIXTURES = {"D-01": "d/index.html", "D-02": "d/synthetic-env.txt", "D-03": "d/synthetic-backup.txt", "E-04": "e/index.html"}


class Clock:
    def __init__(self, *values):
        self.values = list(values)
        self.last = self.values[-1]

    def __call__(self):
        if self.values:
            self.last = self.values.pop(0)
        return self.last


def evaluate(artifact_dir):
    checks = []
    def add(name, condition, evidence):
        checks.append({"name": name, "passed": bool(condition), "evidence": evidence})
    def api_data(response):
        if response.status_code != 200 or response.json()["code"] != 0:
            raise AssertionError("integrated API step did not succeed")
        return response.json()["data"]

    dataset = json.loads((ROOT / "tests/dataset/v1.0.0.json").read_text(encoding="utf-8"))
    scanner = SensitiveScanner(load_rules(ROOT / "src/tools/rules/sensitive-defaults.json"))
    policy = load_grading_policy()
    normalized, source_by_key, per_case = [], {}, {}
    for case in (c for c in dataset["cases"] if c["id"] in FIXTURES):
        origin = dataset["sites"][case["site"]]
        url = origin + case["path"]
        text = (ROOT / "tests/fixtures/sites" / FIXTURES[case["id"]]).read_text(encoding="utf-8")
        scope_id = 1 if case["site"] == "D" else 2
        source = scanner.scan(ScanDocument(url, text), [AuthorizedScope(str(scope_id), origin)])
        per_case[case["id"]] = len(source)
        for value in source:
            record = normalize_finding(value, task_id=1, scope_id=scope_id, tool_name="sensitive-scanner", seen_at=BASE)
            normalized.append(record)
            source_by_key[record["dedup_key"]] = (case["id"], url, value["rule_id"])
    add("F 正负样例真实 T20 检测", per_case == {"D-01": 4, "D-02": 4, "D-03": 1, "E-04": 0}, per_case)
    canonical = deduplicate_findings(normalized + normalized)
    add("T21 重复输入去重", len(canonical) == 9 and all(row["occurrence"] == 1 for row in canonical),
        {"inputRows": len(normalized) * 2, "distinctRisks": len(canonical)})
    artifact_dir.mkdir(parents=True, exist_ok=True)
    cache = ROOT / "tests/.cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="d-closure-", dir=cache) as directory, verification_database() as (verification_repo, sessions):
        ids, contexts = {}, {}
        with sessions.begin() as session:
            session.execute(delete(Finding))
            scope = session.get(AuthScope, 1)
            scope.domain, scope.updated_at = "127.0.0.1", datetime.fromisoformat(BASE)
            for identifier in (1, 2):
                # Task.created_at follows C's naive UTC storage, eight hours behind Shanghai.
                session.get(DetectionTask, identifier).created_at = datetime.fromisoformat(DAY + " 01:00:00")
            for index, record in enumerate(canonical, 1001):
                case_id, url, rule = source_by_key[record["dedup_key"]]
                context = GradingContext(1, record["raw"]["identity"]["target_sha256"],
                                         page_importance="HOME" if case_id == "D-01" else "NORMAL",
                                         exposure="INTERNAL", rule_id=rule)
                values = graded_orm_values(record, context, policy)
                session.add(Finding(id=index, **values, created_at=datetime.fromisoformat(BASE), updated_at=datetime.fromisoformat(BASE)))
                ids[(case_id, rule)] = index
                contexts[index] = values["raw_data"]["grading"]
        selected = ids[("D-01", "fixture-token")]
        selected_record = next(r for r in canonical if source_by_key[r["dedup_key"]][0::2] == ("D-01", "fixture-token"))
        selected_url = source_by_key[selected_record["dedup_key"]][1]
        with sessions() as session:
            saved = session.get(Finding, selected)
            add("T23 分级结果真实落库", saved.severity == "HIGH" and saved.raw_data["grading"]["policy_sha256"] == policy.sha256,
                {"findingId": selected, "severity": saved.severity, "policyVersion": policy.version})

        fresh = {"text": "public safe content", "observed": DAY + " 09:11:00", "complete": True, "calls": 0}
        def provider(binding):
            fresh["calls"] += 1
            return FreshDocument(ScanDocument(binding.url, fresh["text"]), fresh["observed"], coverage_complete=fresh["complete"])
        binding = SensitiveRetestTarget(1, selected_record["dedup_key"], selected_url, "fixture-token", rules_fingerprint(scanner))
        runner = SensitiveRetestRunner(scanner, {selected: binding}, provider)
        alerts = AlertService(SqlAlchemyAlertRepository(sessions))
        retesting = RetestService(SqlAlchemyRetestRepository(sessions), runner,
                                 clock=Clock(DAY + " 09:10:00", DAY + " 09:12:00"))
        reports = ReportService(SqlAlchemyReportRepository(sessions), ReportStorage(Path(directory) / "reports"))
        app = FastAPI()
        actor = {"value": ACTOR}
        identity = lambda: actor["value"]
        app.include_router(create_verification_router(VerificationService(verification_repo), principal_dependency=identity,
                                                      clock=lambda: DAY + " 09:05:00"))
        app.include_router(create_alert_router(alerts, principal_dependency=identity))
        app.include_router(create_retest_router(retesting, principal_dependency=identity))
        app.include_router(create_report_router(reports, principal_dependency=identity, clock=lambda: DAY + " 10:00:00"))
        with TestClient(app) as client:
            verified = api_data(client.patch(f"/api/findings/{selected}/status", json={"status": "CONFIRMED", "reasonCode": "EVIDENCE_REVIEWED"}))
            add("T22 人工确认同库风险", verified["status"] == "CONFIRMED", {"findingId": selected, "status": verified["status"]})
            created = [alerts.process(identifier, ACTOR, observed_at=DAY + " 09:06:00") for identifier in sorted(contexts)]
            duplicate = alerts.process(selected, ACTOR, observed_at=DAY + " 09:06:00")
            inbox = api_data(client.get("/api/alerts"))
            add("T24 全部风险告警且重复不新增", len(created) == 9 and all(v["outcome"] == "ALERT_CREATED" for v in created)
                and duplicate["outcome"] == "DUPLICATE" and inbox["total"] == 9,
                {"initialAlerts": inbox["total"], "repeatOutcome": duplicate["outcome"]})
            before = api_data(client.post("/api/reports/generate", json={"scopeId": 1, "periodBegin": DAY, "periodEnd": DAY}))
            old_html = client.get(before["filePath"]).content
            def report_snapshot(identifier):
                with sessions() as session:
                    token = session.get(Report, identifier).file_path
                return json.loads((reports.storage.root / token / "snapshot.json").read_bytes())
            snapshot_before = report_snapshot(before["reportId"])
            add("T26 修复前报告与检测结果一致", snapshot_before["summary"]["distinctRisks"] == 9 and snapshot_before["summary"]["activeRisks"] == 9,
                snapshot_before["summary"])
            def execute(label, start, observed, end, text, complete=True):
                fresh.update(text=text, observed=DAY + " " + observed, complete=complete)
                retesting.clock = Clock(DAY + " " + start, DAY + " " + end)
                key = str(uuid5(NAMESPACE, label))
                response = client.post(f"/api/findings/{selected}/retest", json={"requestId": key})
                return response, key
            response, clean_key = execute("clean", "09:10:00", "09:11:00", "09:12:00", "public safe content")
            clean = api_data(response)
            add("T25 真实规则 CLEAN 修复", clean["outcome"] == "CLEAN" and clean["status"] == "FIXED"
                and clean["alert_outcome"] == "RESOLVED", {"outcome": clean["outcome"], "status": clean["status"], "alert": clean["alert_outcome"]})
            calls = fresh["calls"]
            replay = api_data(client.post(f"/api/findings/{selected}/retest", json={"requestId": clean_key}))
            add("复测重放不再次检测", replay["replayed"] and fresh["calls"] == calls, {"replayed": replay["replayed"], "providerCalls": fresh["calls"]})
            fixed_report = api_data(client.post("/api/reports/generate", json={"scopeId": 1, "periodBegin": DAY, "periodEnd": DAY}))
            fixed_snapshot = report_snapshot(fixed_report["reportId"])
            add("报告状态随已提交修复变化", fixed_snapshot["summary"]["byStatus"]["FIXED"] == 1 and fixed_snapshot["summary"]["activeRisks"] == 8,
                fixed_snapshot["summary"])
            response, _ = execute("again-clean", "09:20:00", "09:21:00", "09:22:00", "public safe content")
            again = api_data(response)
            add("再次 CLEAN 变为 RETESTED", again["status"] == "RETESTED", {"status": again["status"]})
            dirty = (ROOT / "tests/fixtures/sites/d/index.html").read_text(encoding="utf-8")
            response, _ = execute("recurrence", "09:30:00", "09:31:00", "09:32:00", dirty)
            recurrence = api_data(response)
            with sessions() as session:
                row = session.get(Finding, selected)
                cycle = row.raw_data["alerting"]["cycle"]
                alert_count = session.scalar(select(func.count()).select_from(Alert))
            add("重新命中开启下一告警周期", recurrence["outcome"] == "MATCH" and recurrence["status"] == "CONFIRMED"
                and recurrence["alert_outcome"] == "ALERT_CREATED" and cycle == 2 and alert_count == 10,
                {"status": recurrence["status"], "cycle": cycle, "totalAlerts": alert_count})
            response, _ = execute("incomplete", "09:40:00", "09:41:00", "09:42:00", "public safe content", complete=False)
            with sessions() as session:
                status = session.get(Finding, selected).status
            add("不完整内容不能宣称修复", response.status_code == 409 and response.json()["code"] == 40903 and status == "CONFIRMED",
                {"code": response.json()["code"], "currentStatus": status})
            history = api_data(client.get(f"/api/findings/{selected}/retests"))
            terminal = [r for r in history["items"] if r["outcome"] != "RUNNING"]
            add("历史结论与当前状态区分", len(terminal) == 4 and {r["outcome"] for r in terminal} == {"CLEAN", "MATCH", "INCONCLUSIVE"}
                and all(r["currentStatus"] == "CONFIRMED" for r in terminal),
                {"terminalExecutions": len(terminal), "outcomes": [r["outcome"] for r in terminal]})
            final = api_data(client.post("/api/reports/generate", json={"scopeId": 1, "periodBegin": DAY, "periodEnd": DAY}))
            html_response = client.get(final["filePath"])
            pdf_response = client.get(final["pdfPath"])
            final_snapshot = report_snapshot(final["reportId"])
            pdf = PdfReader(BytesIO(pdf_response.content))
            pdf_text = "".join(page.extract_text() for page in pdf.pages)
            add("最终 HTML/PDF 真正下载且计数一致", html_response.status_code == pdf_response.status_code == 200
                and pdf_response.content.startswith(b"%PDF-") and "安全巡检报告" in pdf_text
                and final_snapshot["summary"]["distinctRisks"] == final_snapshot["summary"]["activeRisks"] == 9,
                {"reportId": final["reportId"], "pages": len(pdf.pages), "summary": final_snapshot["summary"]})
            html_path = artifact_dir / "d-closure-20261023.html"
            pdf_path = artifact_dir / "d-closure-20261023.pdf"
            html_path.write_bytes(html_response.content)
            pdf_path.write_bytes(pdf_response.content)
            add("已生成报告保持冻结", client.get(before["filePath"]).content == old_html,
                {"oldReportId": before["reportId"], "unchanged": client.get(before["filePath"]).content == old_html})
            actor["value"] = Principal(2, "RULE_ADMIN", frozenset({2}))
            denied = [client.get(final["filePath"]), client.get("/api/alerts?scopeId=1"),
                      client.get(f"/api/findings/{selected}/retests")]
            add("跨资产接口一致拒绝", all(r.status_code == 403 and r.json()["code"] == 40310 for r in denied),
                {"codes": [r.json()["code"] for r in denied]})
            actor["value"] = None
            anonymous = client.get(final["filePath"])
            add("缺少可信身份拒绝下载", anonymous.status_code == 401 and anonymous.json()["code"] == 40100,
                {"code": anonymous.json()["code"]})
            with sessions() as session:
                row = session.get(Finding, selected)
                preserved = row.raw_data.get("grading") == contexts[selected] and "retest_pending" not in row.raw_data
                audit = list(session.scalars(select(AgentLog)))
            add("分级元数据与审计跨服务保留", preserved and any(r.action == "REPORT_DONE" for r in audit),
                {"gradingPreserved": preserved, "auditRows": len(audit)})
            public = json.dumps(final_snapshot, ensure_ascii=False) + html_response.text + pdf_text
            audit_public = "\n".join((r.params_summary or "") for r in audit)
            secrets_absent = all(s not in public + audit_public for s in ("fixture-only-token", "DEMO_ACCESS_TOKEN="))
            add("报告与审计不泄露合成敏感原文", secrets_absent, {"rawSecretExported": not secrets_absent})
    return {"task": "D_MODULE_CLOSURE", "date": DAY, "dataset": "F-v1.0.0", "passed": all(c["passed"] for c in checks),
            "passedChecks": sum(c["passed"] for c in checks), "totalChecks": len(checks), "checks": checks,
            "boundaries": ["固定合成输入、私有 SQLite 和可信测试 Principal；未访问业务数据库或真实外部站点。",
                           "跨模块使用真实检测/规则/服务/事务/ASGI；数据导入是测试夹具，不是上线采集/upsert 服务。",
                           "未修改 src.main 默认路由，未验证真实 JWT 到资产授权、生产 PostgreSQL、前端或恒脑全链路。",
                           "T21 标准快照与分级 ORM 元数据分开保留，不能将后者重新传给严格历史接口。",
                           "报告时间为本次固定合成场景，不能作为真实站点的巡检或效果指标。"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/d-closure-20261023.json")
    parser.add_argument("--artifact-dir", type=Path, default=ROOT / "output/pdf")
    args = parser.parse_args()
    report = evaluate(args.artifact_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"D closure: {report['passedChecks']}/{report['totalChecks']} passed")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
