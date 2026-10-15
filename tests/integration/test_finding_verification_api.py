"""T22 opt-in API with trusted dependency injection, never simulated JWT headers."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "tests/support"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.finding_verification import create_verification_router
from src.models.entities import Finding
from src.services.finding_verification import Principal, VerificationService
from verification_db import verification_database


AT = "2026-10-14 09:00:00"
ACTOR = Principal(1, "SEC_OPS", frozenset({1}))


class FindingVerificationAPITests(unittest.TestCase):
    def setUp(self):
        database = verification_database()
        self.repository, self.sessions = database.__enter__()
        self.addCleanup(database.__exit__, None, None, None)
        self.service = VerificationService(self.repository)

    def client(self, principal=ACTOR, *, default_dependency=False):
        app = FastAPI()
        options = {} if default_dependency else {"principal_dependency": lambda: principal}
        app.include_router(create_verification_router(self.service, clock=lambda: AT, **options))
        return TestClient(app)

    def body(self, **changes):
        result = {"status": "CONFIRMED", "reasonCode": "EVIDENCE_REVIEWED", "expectedStatus": "OPEN"}
        result.update(changes)
        return result

    def status(self):
        with self.sessions() as session:
            return session.get(Finding, 101).status

    def test_patch_and_get_use_unified_envelope_and_persist_history(self):
        with self.client() as client:
            response = client.patch("/api/findings/101/status", json=self.body(note="fixture-only-token"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["code"], 0)
            receipt = response.json()["data"]
            self.assertEqual(receipt["status"], "CONFIRMED")
            self.assertEqual(receipt["verification"]["at"], AT)
            self.assertNotIn("fixture-only-token", response.text)
            fetched = client.get("/api/findings/101/verification")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["code"], 0)
        self.assertEqual(fetched.json()["data"]["status"], "CONFIRMED")
        self.assertTrue(fetched.json()["data"]["trail"])
        self.assertEqual(self.status(), "CONFIRMED")

    def test_default_dependency_denies_fake_role_and_authorization_headers(self):
        with self.client(default_dependency=True) as client:
            response = client.patch("/api/findings/101/status", json=self.body(),
                                    headers={"X-Role": "SEC_OPS", "X-Scope-Ids": "1", "Authorization": "Bearer fake"})
        self.assertEqual(response.json()["code"], 40100)
        self.assertEqual(self.status(), "OPEN")

    def test_body_cannot_override_trusted_role_scope_or_user(self):
        with self.client(Principal(3, "ADMIN", frozenset({1}))) as client:
            response = client.patch("/api/findings/101/status", json=self.body(
                role="SEC_OPS", scope_ids=[1], user_id=1))
        self.assertNotEqual(response.json()["code"], 0)
        self.assertEqual(self.status(), "OPEN")

    def test_read_and_write_enforce_separate_role_permissions(self):
        with self.client(Principal(2, "RULE_ADMIN", frozenset({1}))) as client:
            denied = client.patch("/api/findings/101/status", json=self.body())
            allowed = client.get("/api/findings/101/verification")
        self.assertEqual(denied.json()["code"], 40300)
        self.assertEqual(allowed.json()["code"], 0)
        self.assertEqual(self.status(), "OPEN")

    def test_stale_expected_status_is_rejected(self):
        with self.client() as client:
            response = client.patch("/api/findings/101/status", json=self.body(expectedStatus="CONFIRMED"))
        self.assertEqual(response.json()["code"], 40902)
        self.assertEqual(self.status(), "OPEN")

    def test_invalid_requests_and_missing_finding_return_envelopes(self):
        with self.client() as client:
            invalid = client.patch("/api/findings/101/status", json={"status": "FIXED"})
            absent = client.patch("/api/findings/999/status", json=self.body())
        self.assertEqual(invalid.json()["code"], 40001)
        self.assertEqual(absent.json()["code"], 40400)
        self.assertEqual(self.status(), "OPEN")

    def test_malformed_wire_json_uses_sanitized_error_envelope(self):
        with self.client() as client:
            response = client.patch("/api/findings/101/status", content=b'{"note":"fixture-only-token"',
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], 40001)
        self.assertIsNone(response.json()["data"])
        self.assertNotIn("fixture-only-token", response.text)
        self.assertEqual(self.status(), "OPEN")

    def test_get_scope_denial_and_limit_validation(self):
        with self.client() as client:
            denied = client.get("/api/findings/103/verification")
            invalid = client.get("/api/findings/101/verification?limit=0")
        self.assertEqual(denied.json()["code"], 40310)
        self.assertEqual(invalid.json()["code"], 40001)

    def test_factory_routes_are_opt_in_and_do_not_mutate_application_mounts(self):
        from src.main import app
        self.assertFalse(any(getattr(route, "path", "").startswith("/api/findings") for route in app.routes))


if __name__ == "__main__":
    unittest.main(verbosity=2)
