"""T09 JWT/password service with interchangeable memory and SQL user stores."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, replace
from threading import RLock
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
    id: int | None = None


class InMemoryUserStore:
    def __init__(self, users: Iterable[User] = ()) -> None:
        self._users = {user.username: user for user in users}
        self._revoked: set[str] = set()
        self._lock = RLock()

    def add(self, username: str, password: str, role: str, nickname: str = "") -> User:
        if role not in {"ADMIN", "SEC_OPS", "RULE_ADMIN"}:
            raise ValueError("unsupported role")
        user = User(username, hash_password(password), role, nickname, id=len(self._users) + 1)
        self._users[username] = user
        return user

    def get(self, username: str) -> User | None:
        return self._users.get(username)

    def change_password(self, user: User, encoded: str):
        with self._lock:
            if self.get(user.username) != user:
                raise AuthError("user credentials changed; log in again")
            self._users[user.username] = replace(user, password_hash=encoded)

    def is_revoked(self, jti: str) -> bool:
        return jti in self._revoked

    def revoke(self, jti: str, user: User):
        with self._lock:
            self._revoked.add(jti)


class JwtService:
    """HS256 JWT implementation with explicit expiry and issuer claims."""

    def __init__(self, secret: str, issuer: str = "anheng", ttl_seconds: int = 3600) -> None:
        if len(secret) < 16:
            raise ValueError("JWT secret must contain at least 16 characters")
        self._secret = secret.encode()
        self._issuer = issuer
        self._ttl = ttl_seconds
        if ttl_seconds <= 0:
            raise ValueError("JWT TTL must be positive")

    @property
    def ttl_seconds(self):
        return self._ttl

    def credential_tag(self, user: User) -> str:
        return hmac.new(self._secret, user.password_hash.encode(), hashlib.sha256).hexdigest()

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
            "pwd": self.credential_tag(user),
        }
        encoded_header = _b64(json.dumps(header, separators=(",", ":")).encode())
        encoded_payload = _b64(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{encoded_header}.{encoded_payload}".encode()
        signature = _b64(hmac.new(self._secret, signing_input, hashlib.sha256).digest())
        return f"{encoded_header}.{encoded_payload}.{signature}"

    def verify(self, token: str) -> dict:
        try:
            if not isinstance(token, str) or len(token) > 8192:
                raise AuthError("invalid token")
            header, payload, signature = token.split(".")
            signing_input = f"{header}.{payload}".encode()
            expected = _b64(hmac.new(self._secret, signing_input, hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                raise AuthError("invalid token signature")
            decoded_header = json.loads(_unb64(header))
            if not isinstance(decoded_header, dict) or decoded_header.get("alg") != "HS256":
                raise AuthError("unsupported token algorithm")
            claims = json.loads(_unb64(payload))
            if not isinstance(claims, dict):
                raise AuthError("invalid token claims")
            if (claims.get("iss") != self._issuer or type(claims.get("exp")) is not int
                    or claims["exp"] <= int(time.time()) or type(claims.get("iat")) is not int
                    or claims["iat"] > int(time.time()) + 30):
                raise AuthError("token expired or issuer mismatch")
            if (any(not isinstance(claims.get(key), str) or not claims[key]
                    for key in ("sub", "role", "jti", "pwd"))
                    or claims["role"] not in {"ADMIN", "SEC_OPS", "RULE_ADMIN"}):
                raise AuthError("token claims incomplete")
            return claims
        except (ValueError, TypeError, UnicodeDecodeError, OverflowError) as exc:
            raise AuthError("invalid token") from exc


class AuthService:
    def __init__(self, store: InMemoryUserStore, jwt: JwtService) -> None:
        self.store = store
        self.jwt = jwt

    def login(self, username: str, password: str) -> tuple[str, User]:
        user = self.store.get(username)
        if (user is None or not user.enabled or user.role not in {"ADMIN", "SEC_OPS", "RULE_ADMIN"}
                or not verify_password(password, user.password_hash)):
            raise AuthError("username or password incorrect")
        return self.jwt.issue(user), user

    def current_user(self, token: str) -> User:
        claims = self.jwt.verify(token)
        if self.store.is_revoked(claims["jti"]):
            raise AuthError("token revoked")
        user = self.store.get(claims["sub"])
        if (user is None or not user.enabled or user.role != claims["role"]
                or not hmac.compare_digest(self.jwt.credential_tag(user), claims["pwd"])):
            raise AuthError("user unavailable")
        return user

    def logout(self, token: str) -> None:
        user = self.current_user(token)
        claims = self.jwt.verify(token)
        self.store.revoke(claims["jti"], user)

    def change_password(self, user: User, old_password: str, new_password: str):
        if not verify_password(old_password, user.password_hash):
            raise AuthError("username or password incorrect")
        if old_password == new_password:
            raise ValueError("new password must differ from old password")
        self.store.change_password(user, hash_password(new_password))
