"""Real B JWT identity and opt-in D routes on one private SQLite fixture."""
from contextlib import ExitStack
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT, ROOT / "tests/support"):
    sys.path.insert(0, str(directory))

from fastapi.testclient import TestClient
from sqlalchemy import select
from src.api.d_module import DModuleServices, create_d_module_router
from src.auth.persistence import SqlAlchemyUserStore
from src.auth.service import AuthService, JwtService, hash_password
from src.core.config import Settings
from src.main import create_app
from src.models import AgentLog, AuthScope, Finding, SysUser
from src.repositories.finding_verification import SqlAlchemyVerificationRepository
from src.repositories.reporting import SqlAlchemyReportRepository
from src.repositories.retesting import SqlAlchemyRetestRepository
from src.services.alerts import AlertService
from src.services.finding_verification import VerificationService
from src.services.reporting import ReportService
from src.services.report_files import ReportStorage
from src.services.retesting import RetestService
from alert_db import AT, alert_database


PASSWORD = "private-d-jwt-fixture-password"
READ_PATHS = (
    "/api/findings/101/verification",
    "/api/alerts?scopeId=1",
    "/api/findings/101/retests",
    "/api/reports?scopeId=1",
)
CONFIRM = {"status": "CONFIRMED", "reasonCode": "EVIDENCE_REVIEWED"}


class DModuleJwtIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        alert_repository, self.sessions = self.stack.enter_context(alert_database())
        cache = ROOT / "tests/.cache"
        cache.mkdir(exist_ok=True)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="d-jwt-", dir=cache))
        encoded = hash_password(PASSWORD)
        with self.sessions.begin() as session:
            for identifier in (1, 2, 3):
                session.get(SysUser, identifier).password = encoded
        self.store = SqlAlchemyUserStore(self.sessions)
        self.auth = AuthService(self.store, JwtService("isolated-d-module-jwt-secret" * 2))
        self.settings = Settings(_env_file=None, app_env="test", database_url="sqlite+pysqlite:///:memory:",
                                 jwt_secret_key="isolated-d-module-settings-secret", hengan_app_key="",
                                 hengan_app_secret="", hengan_agent_id="")
        self.services = DModuleServices(
            verification=VerificationService(SqlAlchemyVerificationRepository(self.sessions)),
            alerts=AlertService(alert_repository),
            retesting=RetestService(SqlAlchemyRetestRepository(self.sessions), clock=lambda: AT),
            reports=ReportService(SqlAlchemyReportRepository(self.sessions), ReportStorage(folder)),
        )
        self.grants = {1: frozenset({1}), 2: frozenset({1}), 3: frozenset({1})}
        self.resolver_calls = []
        self.resolver_error = None

        def resolver(user_id):
            self.resolver_calls.append(user_id)
            if self.resolver_error is not None:
                raise self.resolver_error
            return self.grants.get(user_id, frozenset())

        self.resolver = resolver
        self.client = self.make_client(resolver)

    def make_client(self, resolver):
        app = create_app(self.settings, auth_service=self.auth)
        app.include_router(create_d_module_router(self.services, grant_resolver=resolver,
                                                  verification_clock=lambda: AT,
                                                  alert_clock=lambda: AT, report_clock=lambda: AT))
        return self.stack.enter_context(TestClient(app))

    def login(self, username="fixture-secops", password=PASSWORD):
        response = self.client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["token"]

    @staticmethod
    def headers(token):
        return {"Authorization": "Bearer " + token}

    def check_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        data = response.json()
        self.assertEqual(set(data), {"code", "message", "data"})
        self.assertEqual(data["code"], code)
        self.assertIsNone(data["data"])
        if status == 401:
            self.assertEqual(response.headers.get("www-authenticate"), "Bearer")

    def test_all_four_route_groups_accept_one_real_login(self):
        token = self.login()
        for path in READ_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path, headers=self.headers(token))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["code"], 0)
        self.assertEqual(self.resolver_calls, [1, 1, 1, 1])

    def test_secops_confirmation_uses_verified_actor_and_commits(self):
        token = self.login()
        response = self.client.patch("/api/findings/101/status", headers=self.headers(token), json=CONFIRM)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["status"], "CONFIRMED")
        with self.sessions() as session:
            self.assertEqual(session.get(Finding, 101).status, "CONFIRMED")
            event = session.scalar(select(AgentLog).where(AgentLog.action == "finding.verify:101"))
            self.assertEqual(event.user_id, 1)

    def test_missing_token_and_spoofed_headers_cannot_grant_access(self):
        for path in READ_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path, headers={"X-Role": "SEC_OPS", "X-Scope-Ids": "1", "X-User-Id": "1"})
                self.check_error(response, 401, 40100)
        self.assertEqual(self.resolver_calls, [])

    def test_forged_token_is_rejected_before_grant_resolution(self):
        token = self.login()
        head, body, signature = token.split(".")
        signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        forged = ".".join((head, body, signature))
        self.check_error(self.client.get(READ_PATHS[0], headers=self.headers(forged)), 401, 40100)
        self.assertEqual(self.resolver_calls, [])

    def test_jwt_role_claim_tampering_is_rejected(self):
        import base64
        token = self.login("fixture-rule-admin")
        head, body, signature = token.split(".")
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        claims["role"] = "SEC_OPS"
        body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        forged = ".".join((head, body, signature))
        self.check_error(self.client.patch("/api/findings/101/status", headers=self.headers(forged), json=CONFIRM), 401, 40100)
        self.assertEqual(self.resolver_calls, [])

    def test_logout_token_cannot_read_d_routes(self):
        token = self.login()
        self.assertEqual(self.client.post("/api/auth/logout", headers=self.headers(token)).status_code, 200)
        for path in READ_PATHS:
            self.check_error(self.client.get(path, headers=self.headers(token)), 401, 40100)
        self.assertEqual(self.resolver_calls, [])

    def test_password_change_invalidates_old_d_access(self):
        token = self.login()
        response = self.client.post("/api/auth/password", headers=self.headers(token),
                                    json={"oldPassword": PASSWORD, "newPassword": "new-d-jwt-fixture-password"})
        self.assertEqual(response.status_code, 200, response.text)
        self.check_error(self.client.get(READ_PATHS[0], headers=self.headers(token)), 401, 40100)
        new_token = self.login(password="new-d-jwt-fixture-password")
        self.assertEqual(self.client.get(READ_PATHS[0], headers=self.headers(new_token)).status_code, 200)

    def test_disabled_user_cannot_use_previously_valid_token(self):
        token = self.login()
        with self.sessions.begin() as session:
            session.get(SysUser, 1).status = 0
        self.check_error(self.client.get(READ_PATHS[0], headers=self.headers(token)), 401, 40100)
        self.assertEqual(self.resolver_calls, [])

    def test_deleted_user_cannot_use_previously_valid_token(self):
        token = self.login()
        with self.sessions.begin() as session:
            session.get(SysUser, 1).deleted = 1
        self.check_error(self.client.get(READ_PATHS[0], headers=self.headers(token)), 401, 40100)
        self.assertEqual(self.resolver_calls, [])

    def test_current_role_change_invalidates_existing_token(self):
        token = self.login()
        with self.sessions.begin() as session:
            session.get(SysUser, 1).role = "RULE_ADMIN"
        self.check_error(self.client.get(READ_PATHS[0], headers=self.headers(token)), 401, 40100)
        changed = self.login()
        self.assertEqual(self.client.get(READ_PATHS[0], headers=self.headers(changed)).status_code, 200)
        self.check_error(self.client.patch("/api/findings/101/status", headers=self.headers(changed), json=CONFIRM), 403, 40300)

    def test_admin_has_no_implicit_d_business_access(self):
        token = self.login("fixture-admin")
        for path in READ_PATHS:
            self.check_error(self.client.get(path, headers=self.headers(token)), 403, 40300)
        self.assertEqual(self.resolver_calls, [])

    def test_rule_admin_can_read_but_cannot_confirm(self):
        token = self.login("fixture-rule-admin")
        for path in READ_PATHS:
            self.assertEqual(self.client.get(path, headers=self.headers(token)).status_code, 200)
        self.check_error(self.client.patch("/api/findings/101/status", headers=self.headers(token), json=CONFIRM), 403, 40300)
        with self.sessions() as session:
            self.assertEqual(session.get(Finding, 101).status, "OPEN")

    def test_cross_asset_grants_do_not_authorize_any_four_groups(self):
        self.grants[1] = frozenset({2})
        token = self.login()
        for path in READ_PATHS:
            self.check_error(self.client.get(path, headers=self.headers(token)), 403, 40310)
        self.check_error(self.client.patch("/api/findings/101/status", headers=self.headers(token), json=CONFIRM), 403, 40310)

    def test_dynamic_grant_revocation_applies_to_same_token(self):
        token = self.login()
        self.assertEqual(self.client.get(READ_PATHS[0], headers=self.headers(token)).status_code, 200)
        self.grants[1] = frozenset()
        self.check_error(self.client.get(READ_PATHS[0], headers=self.headers(token)), 403, 40310)
        self.assertEqual(self.resolver_calls, [1, 1])

    def test_missing_resolver_gives_empty_grants(self):
        client = self.make_client(None)
        token = self.login()
        for path in READ_PATHS:
            self.check_error(client.get(path, headers=self.headers(token)), 403, 40310)
        self.assertEqual(client.get("/api/alerts", headers=self.headers(token)).json()["data"]["total"], 0)
        self.assertEqual(client.get("/api/reports", headers=self.headers(token)).json()["data"]["total"], 0)

    def test_empty_grants_never_expand_to_all_assets(self):
        self.grants[1] = frozenset()
        token = self.login()
        self.check_error(self.client.get(READ_PATHS[0], headers=self.headers(token)), 403, 40310)
        for path in ("/api/alerts", "/api/reports"):
            data = self.client.get(path, headers=self.headers(token)).json()["data"]
            self.assertEqual(data["total"], 0)
            self.assertEqual(data["items"], [])

    def test_invalid_resolver_returns_safe_system_error(self):
        token = self.login()
        dirty = [None, {1}, [1], (1,), "fixture-private-secret", frozenset({True}), frozenset({0}),
                 frozenset({-1}), frozenset({2**63}), frozenset({"fixture-private-secret"})]
        for value in dirty:
            self.grants[1] = value
            for path in READ_PATHS:
                with self.subTest(value_type=type(value).__name__, path=path):
                    response = self.client.get(path, headers=self.headers(token))
                    self.check_error(response, 500, 50000)
                    self.assertNotIn("fixture-private-secret", response.text)

    def test_resolver_exception_is_sanitized_for_all_groups(self):
        token = self.login()
        self.resolver_error = RuntimeError("fixture-private-secret password=" + PASSWORD)
        for path in READ_PATHS:
            response = self.client.get(path, headers=self.headers(token))
            self.check_error(response, 500, 50000)
            for secret in ("fixture-private-secret", PASSWORD, token, "RuntimeError"):
                self.assertNotIn(secret, response.text)

    def test_invalid_verified_user_id_is_rejected(self):
        token = self.login()
        user = self.store.get("fixture-secops")
        for value in (None, True, 0, -1, 2**63):
            with self.subTest(value=value), patch.object(self.store, "get", return_value=replace(user, id=value)):
                self.check_error(self.client.get(READ_PATHS[0], headers=self.headers(token)), 401, 40100)
        self.assertEqual(self.resolver_calls, [])

    def test_user_or_scope_headers_cannot_override_server_grants(self):
        token = self.login()
        self.grants[1] = frozenset({2})
        headers = {**self.headers(token), "X-Role": "SEC_OPS", "X-Scope-Ids": "1", "X-User-Id": "2"}
        self.check_error(self.client.get(READ_PATHS[0], headers=headers), 403, 40310)
        self.assertEqual(self.resolver_calls, [1])

    def test_disabled_asset_is_still_denied_with_valid_jwt_and_grant(self):
        token = self.login()
        with self.sessions.begin() as session:
            session.get(AuthScope, 1).status = "DISABLED"
        for path in READ_PATHS:
            response = self.client.get(path, headers=self.headers(token))
            if path.startswith("/api/alerts"):
                # Existing inbox semantics hide disabled assets in a successful empty list.
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["data"]["total"], 0)
                self.assertEqual(response.json()["data"]["items"], [])
            else:
                self.check_error(response, 403, 40310)

    def test_route_metadata_does_not_expose_password_hash_or_bearer(self):
        token = self.login()
        with self.sessions() as session:
            encoded = session.get(SysUser, 1).password
        for path in READ_PATHS:
            response = self.client.get(path, headers=self.headers(token))
            self.assertEqual(response.status_code, 200)
            for secret in (PASSWORD, encoded, token, "fixture-only-token"):
                self.assertNotIn(secret, response.text)

    def test_default_app_stays_unmounted_without_explicit_opt_in(self):
        app = create_app(self.settings, auth_service=self.auth)
        paths = set(app.openapi()["paths"])
        for path in ("/api/reports", "/api/alerts", "/api/findings/{finding_id}/retest"):
            self.assertNotIn(path, paths)


if __name__ == "__main__":
    unittest.main()
