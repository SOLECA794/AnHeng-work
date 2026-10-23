from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT, ROOT / "tests/support"):
    sys.path.insert(0, str(directory))

from pypdf import PdfReader
from sqlalchemy import func, select
from src.models.entities import AgentLog, AuthScope, DetectionTask, Finding, Report
from src.services.finding_verification import Principal, VerificationError
from src.services.reporting import ReportService
from report_db import ACTOR, AT, BEGIN, END, report_database


class ReportIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.context = report_database()
        self.service, self.sessions, self.storage = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def generate(self, actor=ACTOR, begin=BEGIN, end=END):
        return self.service.generate(1, begin, end, actor, at=AT)

    def snapshot(self, identifier=1):
        with self.sessions() as session:
            token = session.get(Report, identifier).file_path
        return json.loads((self.storage.root / token / "snapshot.json").read_bytes())

    def error(self, code, operation):
        with self.assertRaises(VerificationError) as error:
            operation()
        self.assertEqual(error.exception.code, code)

    def test_exact_counts_dedup_and_latest_status(self):
        self.generate()
        data = self.snapshot()
        self.assertEqual(data["summary"], {"tasks": 2, "findingRows": 3, "distinctRisks": 2, "activeRisks": 1,
            "bySeverity": {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 0, "LOW": 1, "INFO": 0},
            "byStatus": {"OPEN": 0, "CONFIRMED": 1, "FALSE_POSITIVE": 1, "FIXED": 0, "RETESTED": 0}})
        self.assertEqual(data["asset"]["pageCount"], 1)
        self.assertEqual(data["findings"][0]["findingIds"], [101, 104])
        self.assertEqual(data["findings"][0]["taskIds"], [1, 3])

    def test_date_boundaries_and_non_additive_trend(self):
        self.generate()
        data = self.snapshot()
        self.assertEqual([t["taskId"] for t in data["tasks"]], [1, 3])
        self.assertEqual(data["tasks"][0]["createdAt"], "2026-10-18 00:00:00")
        self.assertEqual(data["tasks"][1]["createdAt"], "2026-10-19 23:59:59")
        self.assertEqual(data["trend"], [{"date": BEGIN, "tasks": 1, "distinctRisks": 2},
                                        {"date": END, "tasks": 1, "distinctRisks": 1}])

    def test_explicit_shanghai_task_writer_configuration(self):
        from datetime import timedelta
        from src.repositories.reporting import SqlAlchemyReportRepository
        with self.sessions.begin() as session:
            for identifier in (1, 3, 4):
                task = session.get(DetectionTask, identifier)
                task.created_at += timedelta(hours=8)
        self.service.repository = SqlAlchemyReportRepository(self.sessions, task_timestamp_zone="Asia/Shanghai")
        self.generate()
        self.assertEqual([t["taskId"] for t in self.snapshot()["tasks"]], [1, 3])

    def test_real_chinese_pdf_matches_html(self):
        receipt = self.generate()
        pdf = self.service.download(receipt["reportId"], "pdf", ACTOR)
        html = self.service.download(receipt["reportId"], "html", ACTOR).decode()
        reader = PdfReader(BytesIO(pdf))
        text = "".join(page.extract_text() for page in reader.pages)
        self.assertGreaterEqual(len(reader.pages), 2)
        for phrase in ["安全巡检报告", "修复建议", "敏感信息暴露", "HIGH", "FALSE_POSITIVE", "b" * 64]:
            self.assertIn(phrase, text)
            self.assertIn(phrase, html)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        for content in (html, text, json.dumps(self.snapshot(), ensure_ascii=False)):
            self.assertNotIn("fixture-only-token", content)
            self.assertNotIn("<script>", content)

    def test_frozen_exports_survive_source_changes(self):
        self.generate()
        original = self.service.download(1, "html", ACTOR)
        with self.sessions.begin() as session:
            session.get(Finding, 104).status = "FIXED"
            session.get(AuthScope, 1).domain = "changed.invalid"
        self.assertEqual(original, self.service.download(1, "html", ACTOR))

    def test_new_report_reflects_changes(self):
        self.generate()
        with self.sessions.begin() as session:
            session.get(Finding, 104).status = "FIXED"
        self.generate()
        self.assertEqual(self.snapshot(2)["summary"]["activeRisks"], 0)
        self.assertEqual(self.snapshot()["summary"]["activeRisks"], 1)

    def test_empty_period_does_not_claim_safety(self):
        self.generate(begin="2026-10-01", end="2026-10-02")
        self.assertEqual(self.snapshot()["summary"]["distinctRisks"], 0)
        self.assertIn("不能证明全站安全", self.service.download(1, "html", ACTOR).decode())

    def test_role_rule_admin_can_generate_and_read(self):
        actor = Principal(2, "RULE_ADMIN", frozenset({1}))
        self.assertEqual(self.generate(actor)["status"], "DONE")
        self.assertEqual(self.service.get(1, actor)["status"], "DONE")

    def test_missing_admin_inactive_and_cross_asset_denied(self):
        for actor, code in [(None, 40100), (Principal(3, "ADMIN", frozenset({1})), 40300),
                            (Principal(1, "SEC_OPS", frozenset({2})), 40310),
                            (Principal(1, "SEC_OPS", frozenset({1}), active=False), 40100)]:
            self.error(code, lambda: self.generate(actor))
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Report)), 0)

    def test_disabled_deleted_scope_denies_old_download(self):
        self.generate()
        for change in ("status", "deleted"):
            with self.sessions.begin() as session:
                row = session.get(AuthScope, 1)
                row.status, row.deleted = "ENABLED", 0
                setattr(row, change, "DISABLED" if change == "status" else 1)
            self.error(40310, lambda: self.service.download(1, "pdf", ACTOR))
            self.assertEqual(self.service.list(ACTOR)["total"], 0)

    def test_cross_asset_report_and_list(self):
        self.generate()
        actor = Principal(2, "RULE_ADMIN", frozenset({2}))
        self.error(40310, lambda: self.service.get(1, actor))
        self.error(40310, lambda: self.service.download(1, "html", actor))
        self.assertEqual(self.service.list(actor)["items"], [])

    def test_list_pagination_and_safe_metadata(self):
        self.generate()
        self.generate()
        result = self.service.list(ACTOR, page=2, page_size=1)
        self.assertEqual((result["total"], result["items"][0]["reportId"]), (2, 1))
        self.assertNotIn(str(self.storage.root), json.dumps(result))
        self.assertNotIn("snapshot.json", json.dumps(self.service.get(1, ACTOR)))

    def test_invalid_input_and_unknown_report(self):
        self.error(40001, lambda: self.service.generate(True, BEGIN, END, ACTOR))
        self.error(40001, lambda: self.service.download(1, "../pdf", ACTOR))
        self.error(40400, lambda: self.service.get(999, ACTOR))
        self.error(40001, lambda: self.service.list(ACTOR, page_size=101))

    def test_render_failure_sets_failed_and_blocks_download(self):
        with patch("src.services.report_files.render_pdf", side_effect=RuntimeError("fixture-only-token")):
            self.error(50000, self.generate)
        self.assertEqual(self.service.get(1, ACTOR)["status"], "FAILED")
        self.error(40902, lambda: self.service.download(1, "pdf", ACTOR))
        self.assertEqual(list(self.storage.root.iterdir()), [])

    def test_success_audit_failure_rolls_back_completion(self):
        original = self.service.repository.audit
        def failing(session, row, principal, action, *args, **kwargs):
            if action == "REPORT_DONE":
                raise RuntimeError("audit failure")
            return original(session, row, principal, action, *args, **kwargs)
        with patch.object(self.service.repository, "audit", side_effect=failing):
            self.error(50000, self.generate)
        self.assertEqual(self.service.get(1, ACTOR)["status"], "FAILED")
        with self.sessions() as session:
            self.assertEqual(list(session.scalars(select(AgentLog.action).order_by(AgentLog.id))), ["REPORT_BEGIN", "REPORT_FAILED"])

    def test_scope_disabled_during_render_cannot_complete(self):
        original = self.storage.publish
        def publish(snapshot):
            token = original(snapshot)
            with self.sessions.begin() as session:
                session.get(AuthScope, 1).status = "DISABLED"
            return token
        with patch.object(self.storage, "publish", side_effect=publish):
            self.error(40310, self.generate)
        with self.sessions() as session:
            self.assertEqual(session.get(Report, 1).status, "FAILED")

    def test_corrupt_file_manifest_and_bad_token_rejected(self):
        for target in ("report.pdf", "manifest.json"):
            receipt = self.generate()
            with self.sessions() as session:
                token = session.get(Report, receipt["reportId"]).file_path
            (self.storage.root / token / target).write_bytes(b"corrupt")
            self.error(50000, lambda: self.service.download(receipt["reportId"], "pdf", ACTOR))
        with self.sessions.begin() as session:
            session.get(Report, 1).file_path = "../report.pdf"
        self.error(50000, lambda: self.service.download(1, "pdf", ACTOR))

    def test_missing_file_rejected(self):
        self.generate()
        with self.sessions() as session:
            token = session.get(Report, 1).file_path
        (self.storage.root / token / "report.html").unlink()
        self.error(50000, lambda: self.service.download(1, "html", ACTOR))

    def test_integrity_failure_has_no_success_download_audit(self):
        self.generate()
        with self.sessions() as session:
            token = session.get(Report, 1).file_path
        (self.storage.root / token / "report.html").write_bytes(b"bad")
        self.error(50000, lambda: self.service.download(1, "html", ACTOR))
        with self.sessions() as session:
            self.assertNotIn("REPORT_DOWNLOAD", list(session.scalars(select(AgentLog.action))))

    def test_audit_contains_only_ids_and_format(self):
        self.generate()
        self.service.download(1, "pdf", ACTOR)
        with self.sessions() as session:
            logs = list(session.scalars(select(AgentLog).order_by(AgentLog.id)))
            self.assertTrue(all(r.log_type == "OPERATION" for r in logs))
            self.assertEqual([r.action for r in logs], ["REPORT_BEGIN", "REPORT_DONE", "REPORT_DOWNLOAD"])
            self.assertEqual(json.loads(logs[-1].params_summary), {"reportId": 1, "scopeId": 1, "format": "pdf"})

    def test_unknown_task_status_fails_before_persisting(self):
        with self.sessions.begin() as session:
            session.get(DetectionTask, 1).status = "fixture-only-token"
        self.error(40903, self.generate)
        self.assertEqual(self.service.list(ACTOR)["total"], 0)

    def test_inconsistent_scope_finding_rejected(self):
        with self.sessions.begin() as session:
            session.get(Finding, 101).scope_id = 2
        self.error(40903, self.generate)

    def test_findings_cap_explicit_no_truncation(self):
        with patch("src.services.reporting.MAX_FINDINGS", 2):
            self.error(40903, self.generate)
        self.assertEqual(self.service.list(ACTOR)["total"], 0)

    def test_tasks_cap_explicit_no_truncation(self):
        with patch("src.services.reporting.MAX_TASKS", 1):
            self.error(40903, self.generate)

    def test_no_source_hash_falls_back_to_evidence_digest(self):
        import hashlib
        with self.sessions.begin() as session:
            session.get(Finding, 104).raw_data = {"source_sha256": "fixture-only-token"}
        self.generate()
        self.assertEqual(self.snapshot()["findings"][0]["evidenceSha256"], hashlib.sha256(b"fixture-only-token").hexdigest())

    def test_download_audit_failure_returns_no_bytes(self):
        self.generate()
        with patch.object(self.service.repository, "audit", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                self.service.download(1, "pdf", ACTOR)
        self.assertEqual(self.service.get(1, ACTOR)["status"], "DONE")

    def test_size_limit_fails_report_and_cleans_staging(self):
        with patch("src.services.report_files.MAX_FILE_BYTES", 10):
            self.error(50000, self.generate)
        self.assertEqual(self.service.get(1, ACTOR)["status"], "FAILED")
        self.assertEqual(list(self.storage.root.iterdir()), [])

    def test_unknown_risk_severity_or_type_fails_closed(self):
        for column in ["severity", "finding_type", "status"]:
            with self.sessions.begin() as session:
                row = session.get(Finding, 104)
                row.severity, row.finding_type, row.status = "HIGH", "SENSITIVE", "CONFIRMED"
                setattr(row, column, "fixture-only-token")
            self.error(40903, self.generate)


if __name__ == "__main__":
    unittest.main()
