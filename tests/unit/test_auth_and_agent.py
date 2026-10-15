import json
import unittest
from unittest.mock import Mock

from src.agent.hengan import HenganAgentProvider, HenganSigner
from src.agent.provider import AgentError, aggregate_events, parse_sse_line
from src.auth.service import AuthError, AuthService, InMemoryUserStore, JwtService


class AuthTests(unittest.TestCase):
    def setUp(self):
        store = InMemoryUserStore()
        store.add("admin", "password-123", "ADMIN", "Admin")
        self.service = AuthService(store, JwtService("a" * 32, ttl_seconds=60))

    def test_login_verify_logout(self):
        token, user = self.service.login("admin", "password-123")
        self.assertEqual(user.role, "ADMIN")
        self.assertEqual(self.service.current_user(token).username, "admin")
        self.service.logout(token)
        with self.assertRaises(AuthError):
            self.service.current_user(token)

    def test_bad_password(self):
        with self.assertRaises(AuthError):
            self.service.login("admin", "wrong-password")


class AgentTests(unittest.TestCase):
    def test_signature_and_sse_aggregation(self):
        headers = HenganSigner("key", "secret").headers()
        self.assertTrue(headers["sign"].startswith("".join(c for c in headers["sign"][:13])))
        one = parse_sse_line('data:' + json.dumps({"code": 0, "data": {"message_id": "m", "from": "basic_llm", "content": "a"}}))
        two = parse_sse_line('data:' + json.dumps({"code": 0, "data": {"message_id": "m", "from": "basic_llm", "content": "b", "done": True}}))
        self.assertEqual(aggregate_events([one, two])[0]["text"], "ab")

    def test_status_payload_and_error_mapping(self):
        provider = HenganAgentProvider.__new__(HenganAgentProvider)
        provider._post = lambda name, payload: 2
        self.assertEqual(provider.async_status("task"), 2)
        self.assertEqual(HenganAgentProvider.map_error(-301).project_code, 50002)
        self.assertTrue(HenganAgentProvider.map_error(-301).retryable)
        self.assertEqual(HenganAgentProvider.map_error(-402).project_code, 40300)
        self.assertEqual(HenganAgentProvider.map_error(-409).project_code, 40001)


if __name__ == "__main__":
    unittest.main()
