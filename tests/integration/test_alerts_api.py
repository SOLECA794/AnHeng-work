"""SDD-03 inbox endpoints with trusted identity injection and default-deny access."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/support"))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.alerts import create_alert_router
from src.repositories.alerts import AlertTransaction
from src.services.alerts import AlertService
from src.services.finding_verification import Principal
from alert_db import ACTOR, AT, alert_database


class AlertApiTests(unittest.TestCase):
    def setUp(self):
        database = alert_database()
        self.repo, self.sessions = database.__enter__()
        self.addCleanup(database.__exit__, None, None, None)
        self.service = AlertService(self.repo)
        self.alert_id = self.service.process(101, ACTOR, observed_at=AT)["alertId"]

    def client(self, principal=ACTOR, *, default=False):
        app = FastAPI()
        app.include_router(create_alert_router(self.service, principal_dependency=None if default else lambda: principal, clock=lambda: AT))
        return TestClient(app)

    def test_inbox_read_handle_close_flow(self):
        with self.client() as client:
            response = client.get("/api/alerts?page=1&pageSize=5&status=NEW&severity=MEDIUM&scopeId=1")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["total"], 1)
            self.assertEqual(client.post(f"/api/alerts/{self.alert_id}/read").json()["data"]["readStatus"], 1)
            response = client.patch(f"/api/alerts/{self.alert_id}/status", json={"status": "HANDLED"})
            self.assertEqual(response.json()["data"]["status"], "HANDLED")
            response = client.patch(f"/api/alerts/{self.alert_id}/status", json={"status": "CLOSED"})
            self.assertEqual(response.json()["data"]["status"], "CLOSED")

    def test_no_trusted_authentication_even_with_spoofed_headers(self):
        with self.client(default=True) as client:
            for method, url, options in (("get", "/api/alerts", {}), ("post", f"/api/alerts/{self.alert_id}/read", {}),
                                         ("patch", f"/api/alerts/{self.alert_id}/status", {"json": {"status": "CLOSED"}})):
                response = getattr(client, method)(url, headers={"X-Role": "SEC_OPS", "Authorization": "Bearer fixture-only-token"}, **options)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("fixture-only-token", response.text)

    def test_rule_admin_can_list_and_read_but_not_close(self):
        with self.client(Principal(2, "RULE_ADMIN", frozenset({1}))) as client:
            self.assertEqual(client.get("/api/alerts").status_code, 200)
            self.assertEqual(client.post(f"/api/alerts/{self.alert_id}/read").status_code, 200)
            self.assertEqual(client.patch(f"/api/alerts/{self.alert_id}/status", json={"status": "CLOSED"}).status_code, 403)

    def test_admin_read_scope_grants_still_enforced(self):
        with self.client(Principal(3, "ADMIN", frozenset({1}))) as client:
            self.assertEqual(client.get("/api/alerts").status_code, 403)
            self.assertEqual(client.post(f"/api/alerts/{self.alert_id}/read").status_code, 200)
        with self.client(Principal(3, "ADMIN", frozenset({2}))) as client:
            self.assertEqual(client.post(f"/api/alerts/{self.alert_id}/read").json()["code"], 40310)

    def test_query_scope_denial_and_empty_inbox(self):
        with self.client(Principal(1, "SEC_OPS", frozenset({2}))) as client:
            self.assertEqual(client.get("/api/alerts").json()["data"]["total"], 0)
            self.assertEqual(client.get("/api/alerts?scopeId=1").json()["code"], 40310)

    def test_malformed_values_and_extra_identity_fields_are_sanitized(self):
        with self.client() as client:
            for query in ("page=0", "pageSize=999", "status=fixture-only-token", "scopeId=true"):
                response = client.get("/api/alerts?" + query)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("fixture-only-token", response.text)
            for payload in ({"status": "NEW"}, {"status": "CLOSED", "role": "SEC_OPS", "note": "fixture-only-token"}, {"status": []}):
                response = client.patch(f"/api/alerts/{self.alert_id}/status", json=payload)
                self.assertEqual(response.json()["code"], 40001)
                self.assertNotIn("fixture-only-token", response.text)
            response = client.patch(f"/api/alerts/{self.alert_id}/status", content='{"status": fixture-only-token', headers={"Content-Type": "application/json"})
            self.assertEqual(response.json()["code"], 40001)
            self.assertNotIn("fixture-only-token", response.text)

    def test_not_found_and_closed_state_conflict(self):
        with self.client() as client:
            self.assertEqual(client.post("/api/alerts/999/read").status_code, 404)
            client.patch(f"/api/alerts/{self.alert_id}/status", json={"status": "CLOSED"})
            response = client.patch(f"/api/alerts/{self.alert_id}/status", json={"status": "HANDLED"})
            self.assertEqual(response.json()["code"], 40902)

    def test_transaction_errors_never_echo_driver_details(self):
        with self.client() as client:
            with patch.object(AlertTransaction, "audit", side_effect=RuntimeError("fixture-only-token")):
                response = client.patch(f"/api/alerts/{self.alert_id}/status", json={"status": "CLOSED"})
            self.assertEqual(response.json()["code"], 50000)
            self.assertNotIn("fixture-only-token", response.text)
            self.assertEqual(client.get("/api/alerts").json()["data"]["items"][0]["status"], "NEW")

    def test_default_application_does_not_expose_unwired_routes(self):
        from src.main import app
        self.assertFalse(any(getattr(route, "path", "").startswith("/api/alerts") for route in app.routes))


if __name__ == "__main__":
    unittest.main()
