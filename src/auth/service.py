"""Small, dependency-light authentication service for T09.

The store is intentionally in-memory for the first integration milestone. The
JWT and password interfaces stay stable when the store is replaced by SQLAlchemy.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Iterable


class AuthError(ValueError):
    pass


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if not password or len(password) < 8:
        raise ValueError("password must contain at least 8 characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt, expected = encoded.split("$", 2)
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, _unb64(expected))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class User:
    username: str
    password_hash: str
    role: str
    nickname: str = ""
    enabled: bool = True


class InMemoryUserStore:
    def __init__(self, users: Iterable[User] = ()) -> None:
        self._users = {user.username: user for user in users}

    def add(self, username: str, password: str, role: str, nickname: str = "") -> User:
        user = User(username, hash_password(password), role, nickname)
        self._users[username] = user
        return user

    def get(self, username: str) -> User | None:
        return self._users.get(username)


class JwtService:
    """HS256 JWT implementation with explicit expiry and issuer claims."""

    def __init__(self, secret: str, issuer: str = "anheng", ttl_seconds: int = 3600) -> None:
        if len(secret) < 16:
            raise ValueError("JWT secret must contain at least 16 characters")
        self._secret = secret.encode()
        self._issuer = issuer
        self._ttl = ttl_seconds

    def issue(self, user: User) -> str:
        now = int(time.time())
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "sub": user.username,
            "username": user.username,
            "role": user.role,
            "nickname": user.nickname,
            "iss": self._issuer,
            "iat": now,
            "exp": now + self._ttl,
            "jti": secrets.token_urlsafe(12),
        }
        encoded_header = _b64(json.dumps(header, separators=(",", ":")).encode())
        encoded_payload = _b64(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{encoded_header}.{encoded_payload}".encode()
        signature = _b64(hmac.new(self._secret, signing_input, hashlib.sha256).digest())
        return f"{encoded_header}.{encoded_payload}.{signature}"

    def verify(self, token: str) -> dict:
        try:
            header, payload, signature = token.split(".", 2)
            signing_input = f"{header}.{payload}".encode()
            expected = _b64(hmac.new(self._secret, signing_input, hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                raise AuthError("invalid token signature")
            if json.loads(_unb64(header)).get("alg") != "HS256":
                raise AuthError("unsupported token algorithm")
            claims = json.loads(_unb64(payload))
            if claims.get("iss") != self._issuer or int(claims.get("exp", 0)) <= int(time.time()):
                raise AuthError("token expired or issuer mismatch")
            if not claims.get("sub") or not claims.get("role"):
                raise AuthError("token claims incomplete")
            return claims
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AuthError("invalid token") from exc


class AuthService:
    def __init__(self, store: InMemoryUserStore, jwt: JwtService) -> None:
        self.store = store
        self.jwt = jwt
        self._revoked: set[str] = set()

    def login(self, username: str, password: str) -> tuple[str, User]:
        user = self.store.get(username)
        if user is None or not user.enabled or not verify_password(password, user.password_hash):
            raise AuthError("username or password incorrect")
        return self.jwt.issue(user), user

    def current_user(self, token: str) -> User:
        claims = self.jwt.verify(token)
        if claims.get("jti") in self._revoked:
            raise AuthError("token revoked")
        user = self.store.get(claims["sub"])
        if user is None or not user.enabled:
            raise AuthError("user unavailable")
        return user

    def logout(self, token: str) -> None:
        claims = self.jwt.verify(token)
        self._revoked.add(claims["jti"])
