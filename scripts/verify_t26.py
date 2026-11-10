"""Deterministic T26 acceptance and real downloadable HTML/PDF sample exports."""
from io import BytesIO
import argparse
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "tests/support"):
    sys.path.insert(0, str(directory))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select
from src.api.reporting import create_report_router
from src.models.entities import AgentLog, Finding, Report
from src.services.finding_verification import Principal, VerificationError
from report_db import ACTOR, AT, BEGIN, END, report_database


def evaluate(artifact_dir):
    checks = []
    def add(name, condition, evidence):
        checks.append({"name": name, "passed": bool(condition), "evidence": evidence})
    def code(operation):
        try:
            operation()
            return 0
        except VerificationError as error:
            return error.code
    artifact_dir.mkdir(parents=True, exist_ok=True)
    with report_database() as (service, sessions, storage):
        app = FastAPI()
        app.include_router(create_report_router(service, principal_dependency=lambda: ACTOR, clock=lambda: AT))
        with TestClient(app) as client:
            response = client.post("/api/reports/generate", json={"scopeId": 1, "periodBegin": BEGIN, "periodEnd": END})
            receipt = response.json()["data"]
            add("生成真实报告", response.status_code == 200 and receipt["status"] == "DONE", receipt)
            pdf_response = client.get("/api/reports/1/file?format=pdf")
            html_response = client.get("/api/reports/1/file?format=html")
            pdf, html = pdf_response.content, html_response.content
            (artifact_dir / "report-t26-20261019.pdf").write_bytes(pdf)
            (artifact_dir / "report-t26-20261019.html").write_bytes(html)
            add("附件下载及禁止缓存", pdf_response.status_code == html_response.status_code == 200 and
                "attachment" in pdf_response.headers["content-disposition"] and "no-store" in pdf_response.headers["cache-control"],
                {"pdf": pdf.startswith(b"%PDF-"), "html": html.startswith(b"<!doctype html>")})
        with sessions() as session:
            token = session.get(Report, 1).file_path
        snapshot = json.loads((storage.root / token / "snapshot.json").read_bytes())
        add("统计与原数据一致", snapshot["summary"]["tasks"] == 2 and snapshot["summary"]["findingRows"] == 3 and
            snapshot["summary"]["distinctRisks"] == 2 and snapshot["summary"]["activeRisks"] == 1, snapshot["summary"])
        add("跨任务去重与来源追踪", snapshot["findings"][0]["findingIds"] == [101, 104] and
            snapshot["findings"][0]["taskIds"] == [1, 3], {"findingIds": snapshot["findings"][0]["findingIds"]})
        add("首尾日及次日零点边界", [t["taskId"] for t in snapshot["tasks"]] == [1, 3], snapshot["tasks"])
        add("每日趋势与非加和口径", [t["distinctRisks"] for t in snapshot["trend"]] == [2, 1], snapshot["trend"])
        reader = PdfReader(BytesIO(pdf))
        text = "".join(page.extract_text() for page in reader.pages)
        add("中文 PDF 与 HTML 核对", all(p in text and p in html.decode() for p in
            ("安全巡检报告", "敏感信息暴露", "修复建议", "b" * 64)), {"pages": len(reader.pages), "chineseExtractable": "敏感信息暴露" in text})
        add("敏感内容不出报告", all("fixture-only-token" not in value and "<script>" not in value for value in
            (text, html.decode(), json.dumps(snapshot, ensure_ascii=False))), {"rawContentExported": False})
        with sessions.begin() as session:
            session.get(Finding, 104).status = "FIXED"
        add("冻结报告不随源数据变化", service.download(1, "html", ACTOR) == html, {"immutable": True})
        service.generate(1, BEGIN, END, Principal(2, "RULE_ADMIN", frozenset({1})), at=AT)
        add("规则管理员与授权分页", service.list(ACTOR, page_size=1)["total"] == 2 and
            service.list(ACTOR, page=2, page_size=1)["items"][0]["reportId"] == 1, {"total": 2})
        denied = [code(lambda: service.download(1, "html", None)),
                  code(lambda: service.download(1, "pdf", Principal(2, "RULE_ADMIN", frozenset({2})))),
                  code(lambda: service.generate(1, BEGIN, END, Principal(3, "ADMIN", frozenset({1}))))]
        add("身份角色资产边界", denied == [40100, 40310, 40300], {"codes": denied})
        (storage.root / token / "report.pdf").write_bytes(b"broken")
        add("损坏文件拒绝下载", code(lambda: service.download(1, "pdf", ACTOR)) == 50000, {"code": 50000})
        with patch("src.services.report_files.render_pdf", side_effect=RuntimeError("synthetic")):
            failed_code = code(lambda: service.generate(1, BEGIN, END, ACTOR, at=AT))
        add("生成失败状态与下载拒绝", failed_code == 50000 and service.get(3, ACTOR)["status"] == "FAILED" and
            code(lambda: service.download(3, "pdf", ACTOR)) == 40902, {"status": service.get(3, ACTOR)["status"]})
        with sessions() as session:
            actions = list(session.scalars(select(AgentLog.action).order_by(AgentLog.id)))
        add("生成下载失败审计", all(action in actions for action in ("REPORT_BEGIN", "REPORT_DONE", "REPORT_DOWNLOAD", "REPORT_FAILED")),
            {"actions": actions})
    return {"task": "T26", "date": "2026-10-19", "dataset": "private-synthetic-report-v1",
            "boundary": "SQLite/ASGI/真实 HTML 与 PDF 离线验收；不等同生产 PostgreSQL、真实资产授权或公网全链路。",
            "total": len(checks), "passed": sum(c["passed"] for c in checks), "checks": checks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "reports/t26-acceptance.json")
    parser.add_argument("--artifact-dir", type=Path, default=ROOT / "output/pdf")
    args = parser.parse_args()
    result = evaluate(args.artifact_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"T26: {result['passed']}/{result['total']} passed")
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
