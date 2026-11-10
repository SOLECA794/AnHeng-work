"""T25 API: trusted runner only, no client verdicts or forged identity."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/support"))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.retesting import create_retest_router
from src.services.finding_verification import Principal
from src.services.retesting import RetestService
from retest_db import ACTOR, KEY, OTHER_KEY, Clock, Runner, retest_database


class RetestApiTests(unittest.TestCase):
    def setUp(self):
        database = retest_database()
        self.repo, self.sessions = database.__enter__()
        self.addCleanup(database.__exit__, None, None, None)
        self.runner = Runner()
        self.service = RetestService(self.repo, self.runner, clock=Clock())

    def client(self, principal=ACTOR, *, default=False):
        app = FastAPI()
        app.include_router(create_retest_router(self.service, principal_dependency=None if default else lambda: principal))
        return TestClient(app)

    def test_single_retest_history_and_idempotent_replay(self):
        with self.client() as client:
            first = client.post("/api/findings/101/retest", json={"requestId": KEY})
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["data"]["status"], "FIXED")
            second = client.post("/api/findings/101/retest", json={"requestId": KEY})
            self.assertTrue(second.json()["data"]["replayed"])
            self.assertEqual(self.runner.calls, 1)
            self.assertEqual(client.get("/api/findings/101/retests").json()["data"]["items"][0]["outcome"], "CLEAN")

    def test_default_identity_and_forged_headers_are_denied(self):
        with self.client(default=True) as client:
            response = client.post("/api/findings/101/retest", json={"requestId": KEY}, headers={"X-Role": "SEC_OPS", "Authorization": "Bearer fixture-only-token"})
            self.assertEqual(response.status_code, 401)
            self.assertNotIn("fixture-only-token", response.text)
            self.assertEqual(self.runner.calls, 0)

    def test_rule_admin_reads_history_but_cannot_execute(self):
        with self.client(Principal(2, "RULE_ADMIN", frozenset({1}))) as client:
            self.assertEqual(client.get("/api/findings/101/retests").status_code, 200)
            self.assertEqual(client.post("/api/findings/101/retest").status_code, 403)

    def test_scope_grants_protect_single_and_batch(self):
        with self.client(Principal(1, "SEC_OPS", frozenset({2}))) as client:
            self.assertEqual(client.post("/api/findings/101/retest").json()["code"], 40310)
            self.assertEqual(client.post("/api/auth-scopes/1/retest").json()["code"], 40310)
            self.assertEqual(self.runner.calls, 0)

    def test_client_cannot_supply_clean_verdict_target_or_role(self):
        with self.client() as client:
            for body in ({"outcome": "CLEAN"}, {"url": "http://fixture.invalid/", "role": "SEC_OPS"},
                         {"requestId": "fixture-only-token"}, []):
                response = client.post("/api/findings/101/retest", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("fixture-only-token", response.text)
            self.assertEqual(self.runner.calls, 0)

    def test_scope_batch_limit_and_request_id(self):
        with self.client() as client:
            response = client.post("/api/auth-scopes/1/retest", json={"requestId": KEY, "limit": 1})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()["data"]["items"]), 1)
            self.assertEqual(client.post("/api/auth-scopes/1/retest", json={"limit": 21}).status_code, 400)

    def test_missing_adapter_returns_uncertain_not_fixed(self):
        self.service.runner = None
        with self.client() as client:
            response = client.post("/api/findings/101/retest", json={"requestId": KEY})
            self.assertEqual(response.json()["code"], 40903)
            history = client.get("/api/findings/101/retests").json()["data"]["items"]
            self.assertEqual(history[0]["status"], "CONFIRMED")

    def test_malformed_json_and_history_limit_are_sanitized(self):
        with self.client() as client:
            response = client.post("/api/findings/101/retest", content='{"requestId": fixture-only-token', headers={"Content-Type": "application/json"})
            self.assertEqual(response.json()["code"], 40001)
            self.assertNotIn("fixture-only-token", response.text)
            self.assertEqual(client.get("/api/findings/101/retests?limit=101").status_code, 400)
            self.assertEqual(client.post("/api/findings/999/retest").status_code, 404)

    def test_default_application_does_not_mount_unwired_retest(self):
        from src.main import app
        self.assertFalse(any(getattr(route, "path", "").endswith("/retest") for route in app.routes))


if __name__ == "__main__":
    unittest.main()
