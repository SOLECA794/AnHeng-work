"""Real ASGI/JWT + isolated file SQLite; never opens the configured app DB."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from src.auth.dependencies import require_roles
from src.core.config import Settings
from src.main import create_app
from src.models import AgentLog, SysUser
from src.agent.audit import SqlAlchemyAgentAudit
from src.agent.hengan import HenganAgentProvider
from src.agent.provider import AgentError
import httpx


class AuthApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, app_env="test", hengan_app_key="", hengan_app_secret="",
                                 database_url="sqlite+pysqlite:///" + (Path(self.tmp.name) / "auth.db").as_posix(),
                                 jwt_secret_key="isolated-test-secret" * 2, dev_admin_password="fixture-password")
        self.app = create_app(self.settings)
        @self.app.get("/test-secops")
        def secops(user=Depends(require_roles("SEC_OPS"))):
            return {"role": user.role}
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.client.__exit__, None, None, None)
        self.app.state.auth_service.store.add("ops", "fixture-password", "SEC_OPS")
        self.app.state.auth_service.store.add("rules", "fixture-password", "RULE_ADMIN")

    def login(self, username="ops"):
        response = self.client.post("/api/auth/login", json={"username": username, "password": "fixture-password"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["token"]

    def headers(self, token):
        return {"Authorization": "Bearer " + token}

    def test_login_me_and_exact_roles(self):
        for name in ("ops", "rules", "admin"):
            token = self.login(name)
            result = self.client.get("/api/auth/me", headers=self.headers(token))
            self.assertEqual(result.json()["data"]["username"], name)
            self.assertIsInstance(result.json()["data"]["id"], int)
            result = self.client.get("/test-secops", headers=self.headers(token))
            self.assertEqual(result.status_code, 200 if name == "ops" else 403)
            if name != "ops":
                self.assertEqual(result.json()["code"], 40300)

    def test_missing_invalid_and_expired_token_envelope(self):
        for headers in ({}, {"Authorization": "Basic token"}, self.headers("broken.token.value")):
            result = self.client.get("/api/auth/me", headers=headers)
            self.assertEqual(result.status_code, 401)
            self.assertEqual(result.json()["code"], 40100)
            self.assertEqual(result.headers["www-authenticate"], "Bearer")

    def test_validation_does_not_echo_password(self):
        secret = "fixture-private-password"
        result = self.client.post("/api/auth/login", json={"username": [], "password": secret})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.json()["code"], 40001)
        self.assertNotIn(secret, result.text)

    def test_bad_credentials_and_disabled_user(self):
        result = self.client.post("/api/auth/login", json={"username": "ops", "password": "bad"})
        self.assertEqual(result.status_code, 401)
        token = self.login()
        with self.app.state.sessions.begin() as session:
            session.execute(update(SysUser).where(SysUser.username == "ops").values(status=0))
        self.assertEqual(self.client.get("/api/auth/me", headers=self.headers(token)).status_code, 401)

    def test_logout_survives_service_restart_and_does_not_store_token(self):
        token = self.login()
        self.assertEqual(self.client.post("/api/auth/logout", headers=self.headers(token)).status_code, 200)
        with TestClient(create_app(self.settings)) as second:
            self.assertEqual(second.get("/api/auth/me", headers=self.headers(token)).status_code, 401)
            with second.app.state.sessions() as session:
                rows = session.scalars(select(AgentLog).where(AgentLog.action == "logout")).all()
                self.assertEqual(len(rows), 1)
                self.assertEqual(len(rows[0].params_summary), 64)
                self.assertNotIn(token, rows[0].params_summary)

    def test_password_change_persists_and_revokes_other_tokens(self):
        one, two = self.login(), self.login()
        result = self.client.post("/api/auth/password", headers=self.headers(one),
                                  json={"oldPassword": "fixture-password", "newPassword": "changed-password"})
        self.assertEqual(result.status_code, 200, result.text)
        with TestClient(create_app(self.settings)) as second:
            for token in (one, two):
                self.assertEqual(second.get("/api/auth/me", headers=self.headers(token)).status_code, 401)
            self.assertEqual(second.post("/api/auth/login", json={"username": "ops", "password": "changed-password"}).status_code, 200)

    def test_auth_services_do_not_leak_across_apps(self):
        token = self.login()
        with TestClient(create_app(self.settings)) as second:
            self.assertEqual(second.get("/api/auth/me", headers=self.headers(token)).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers=self.headers(token)).status_code, 200)

    def test_agent_traces_persist_and_query_is_admin_only(self):
        secret = "synthetic-secret-input"
        audit = SqlAlchemyAgentAudit(self.app.state.sessions)
        with httpx.Client(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"code": 0, "data": "synthetic-task"}))) as client:
            provider = HenganAgentProvider("https://fixture.invalid", "key", "private-key", client=client, audit=audit)
            provider.async_submit("agent", {"input": secret})
        with httpx.Client(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"code": -504, "msg": secret}))) as client:
            provider = HenganAgentProvider("https://fixture.invalid", "key", "private-key", client=client, audit=audit)
            with self.assertRaises(AgentError):
                provider.execute("sid", "agent", {"input": secret})
        admin_headers = self.headers(self.login("admin"))
        result = self.client.get("/api/agent/logs?size=1", headers=admin_headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["data"]["total"], 2)
        self.assertEqual(len(result.json()["data"]["list"]), 1)
        self.assertNotIn(secret, result.text)
        self.assertNotIn("private-key", result.text)
        result = self.client.get("/api/agent/logs?resultStatus=SUCCESS", headers=admin_headers)
        self.assertEqual(result.json()["data"]["total"], 1)
        with TestClient(create_app(self.settings)) as second:
            result = second.get("/api/agent/logs", headers=admin_headers)
            self.assertEqual(result.json()["data"]["total"], 2)
        self.assertEqual(self.client.get("/api/agent/logs", headers=self.headers(self.login())).status_code, 403)
        self.assertEqual(self.client.get("/api/agent/logs").status_code, 401)


if __name__ == "__main__":
    unittest.main()
