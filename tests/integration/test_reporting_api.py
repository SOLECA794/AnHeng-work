import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT, ROOT / "tests/support"):
    sys.path.insert(0, str(directory))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.reporting import create_report_router
from src.services.finding_verification import Principal
from report_db import ACTOR, AT, BEGIN, END, report_database

PAYLOAD = {"scopeId": 1, "periodBegin": BEGIN, "periodEnd": END}


class ReportApiTests(unittest.TestCase):
    def setUp(self):
        context = report_database()
        self.service, self.sessions, self.storage = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.actor = ACTOR
        app = FastAPI()
        app.include_router(create_report_router(self.service, principal_dependency=lambda: self.actor, clock=lambda: AT))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def generate(self):
        result = self.client.post("/api/reports/generate", json=PAYLOAD)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()["data"]

    def test_four_endpoints_and_two_binary_downloads(self):
        data = self.generate()
        self.assertEqual(data["status"], "DONE")
        self.assertEqual(self.client.get("/api/reports").json()["data"]["total"], 1)
        self.assertEqual(self.client.get("/api/reports/1").json()["data"]["reportId"], 1)
        for format, signature in [("html", b"<!doctype html>"), ("pdf", b"%PDF-")]:
            response = self.client.get(f"/api/reports/1/file?format={format}")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.content.startswith(signature))
            self.assertIn("attachment", response.headers["content-disposition"])
            self.assertIn("no-store", response.headers["cache-control"])
            self.assertIn("sandbox", response.headers["content-security-policy"])
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_default_factory_rejects_spoofed_identity(self):
        app = FastAPI()
        app.include_router(create_report_router(self.service))
        with TestClient(app) as client:
            response = client.post("/api/reports/generate", json=PAYLOAD,
                                   headers={"X-Role": "SEC_OPS", "Authorization": "Bearer fake"})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["code"], 40100)

    def test_cross_asset_read_download_generate_denied(self):
        self.generate()
        self.actor = Principal(2, "RULE_ADMIN", frozenset({2}))
        for path in ["/api/reports/1", "/api/reports/1/file", "/api/reports?scopeId=1"]:
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post("/api/reports/generate", json=PAYLOAD).status_code, 403)

    def test_invalid_payload_no_secret_echo(self):
        for payload in [{**PAYLOAD, "scopeId": True}, {**PAYLOAD, "scopeId": "1"},
                        {**PAYLOAD, "note": "fixture-only-token"}, {**PAYLOAD, "periodBegin": "fixture-only-token"},
                        None, ["fixture-only-token"]]:
            response = self.client.post("/api/reports/generate", json=payload)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("fixture-only-token", response.text)

    def test_invalid_query_and_path_safe_envelope(self):
        for path in ["/api/reports/invalid", "/api/reports/1/file?format=exe", "/api/reports?pageSize=101",
                     "/api/reports?page=0", "/api/reports?scopeId=no"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["code"], 40001)

    def test_failed_report_cannot_download(self):
        with patch("src.services.report_files.render_pdf", side_effect=RuntimeError("fixture-only-token")):
            response = self.client.post("/api/reports/generate", json=PAYLOAD)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("fixture-only-token", response.text)
        self.assertEqual(self.client.get("/api/reports/1").json()["data"]["status"], "FAILED")
        self.assertEqual(self.client.get("/api/reports/1/file").status_code, 409)

    def test_role_admin_cannot_generate(self):
        self.actor = Principal(3, "ADMIN", frozenset({1}))
        self.assertEqual(self.client.post("/api/reports/generate", json=PAYLOAD).status_code, 403)

    def test_no_internal_path_or_free_text_in_responses(self):
        self.generate()
        for path in ["/api/reports", "/api/reports/1", "/api/reports/1/file?format=html"]:
            text = self.client.get(path).text
            self.assertNotIn("fixture-only-token", text)
            self.assertNotIn(str(self.storage.root), text)


if __name__ == "__main__":
    unittest.main()
