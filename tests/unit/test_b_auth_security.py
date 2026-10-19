"""T09 negative credentials and runtime configuration, without network access."""
import base64
from dataclasses import replace
import hashlib
import hmac
import json
import time
import unittest

from src.auth.service import AuthError, AuthService, InMemoryUserStore, JwtService, verify_password
from src.core.config import Settings


class AuthSecurityTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryUserStore()
        self.user = self.store.add("ops", "old-password-123", "SEC_OPS")
        self.jwt = JwtService("test-secret-value" * 2, ttl_seconds=60)
        self.auth = AuthService(self.store, self.jwt)

    def signed(self, header, claims):
        def enc(value):
            return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
        data = enc(header) + "." + enc(claims)
        signature = base64.urlsafe_b64encode(hmac.new(self.jwt._secret, data.encode(), hashlib.sha256).digest())
        return data + "." + signature.rstrip(b"=").decode()

    def test_malformed_signed_payloads_are_rejected(self):
        valid = self.jwt.verify(self.jwt.issue(self.user))
        for payload in ([], None, "text", {**valid, "exp": "tomorrow"},
                        {**valid, "exp": True}, {**valid, "exp": int(time.time()) - 1},
                        {**valid, "iat": int(time.time()) + 1000}, {**valid, "jti": []},
                        {**valid, "role": "ROOT"}, {**valid, "iss": "other"}):
            with self.subTest(payload=payload), self.assertRaises(AuthError):
                self.jwt.verify(self.signed({"alg": "HS256"}, payload))

    def test_wrong_alg_and_malformed_headers_are_rejected(self):
        claims = self.jwt.verify(self.jwt.issue(self.user))
        for header in ([], None, {"alg": "none"}):
            with self.subTest(header=header), self.assertRaises(AuthError):
                self.jwt.verify(self.signed(header, claims))

    def test_corrupted_tokens(self):
        token = self.jwt.issue(self.user)
        for value in ("", "a.b.c", token + ".extra", token[:-5] + "wrong", "x" * 8193):
            with self.subTest(value=value[:10]), self.assertRaises(AuthError):
                self.auth.current_user(value)

    def test_password_change_invalidates_all_sessions(self):
        one, _ = self.auth.login("ops", "old-password-123")
        two, _ = self.auth.login("ops", "old-password-123")
        self.auth.change_password(self.user, "old-password-123", "new-password-456")
        for token in (one, two):
            with self.assertRaises(AuthError):
                self.auth.current_user(token)
        with self.assertRaises(AuthError):
            self.auth.login("ops", "old-password-123")
        self.assertEqual(self.auth.login("ops", "new-password-456")[1].role, "SEC_OPS")

    def test_wrong_old_password_and_same_password(self):
        with self.assertRaises(AuthError):
            self.auth.change_password(self.user, "wrong-password", "new-password-456")
        with self.assertRaises(ValueError):
            self.auth.change_password(self.user, "old-password-123", "old-password-123")

    def test_disabled_and_changed_role_revoke_access(self):
        token = self.jwt.issue(self.user)
        for changed in (replace(self.user, enabled=False), replace(self.user, role="ADMIN")):
            self.store._users["ops"] = changed
            with self.assertRaises(AuthError):
                self.auth.current_user(token)

    def test_password_hash_is_salted_and_malformed_hash_fails(self):
        another = self.store.add("ops2", "old-password-123", "SEC_OPS")
        self.assertNotEqual(another.password_hash, self.user.password_hash)
        for value in ("plaintext", "scrypt$bad$bad", "unknown$a$b"):
            self.assertFalse(verify_password("old-password-123", value))

    def test_production_rejects_default_secret_or_sqlite(self):
        for data in ({"app_env": "prod"},
                     {"app_env": "prod", "jwt_secret_key": "x" * 32},
                     {"jwt_secret_key": "short"}):
            with self.assertRaises(ValueError):
                Settings(_env_file=None, **data)


if __name__ == "__main__":
    unittest.main()
