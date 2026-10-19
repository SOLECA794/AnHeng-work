"""T09 persistent users and logout revocation, using existing SDD-02 tables."""
import hashlib

from sqlalchemy import select, update

from src.auth.service import AuthError, User, hash_password
from src.models import AgentLog, SysUser


class SqlAlchemyUserStore:
    def __init__(self, sessions):
        self.sessions = sessions

    def get(self, username: str) -> User | None:
        with self.sessions() as session:
            row = session.scalar(select(SysUser).where(SysUser.username == username))
            if row is None:
                return None
            return User(row.username, row.password, row.role, row.nickname or "",
                        row.status == 1 and row.deleted == 0, row.id)

    def add(self, username: str, password: str, role: str, nickname: str = "") -> User:
        if role not in {"ADMIN", "SEC_OPS", "RULE_ADMIN"}:
            raise ValueError("unsupported role")
        encoded = hash_password(password)
        with self.sessions.begin() as session:
            row = SysUser(username=username, password=encoded, role=role, nickname=nickname)
            session.add(row)
            session.flush()
            user_id = row.id
        return User(username, encoded, role, nickname, True, user_id)

    def change_password(self, user: User, encoded: str):
        with self.sessions.begin() as session:
            result = session.execute(update(SysUser).where(
                SysUser.id == user.id, SysUser.password == user.password_hash,
                SysUser.status == 1, SysUser.deleted == 0,
            ).values(password=encoded))
            if result.rowcount != 1:
                raise AuthError("user credentials changed; log in again")
            session.add(AgentLog(user_id=user.id, log_type="AUTH", action="password",
                                 result_status="SUCCESS"))

    @staticmethod
    def _key(jti: str) -> str:
        return hashlib.sha256(jti.encode()).hexdigest()

    def is_revoked(self, jti: str) -> bool:
        with self.sessions() as session:
            return session.scalar(select(AgentLog.id).where(
                AgentLog.log_type == "AUTH", AgentLog.action == "logout",
                AgentLog.params_summary == self._key(jti),
            ).limit(1)) is not None

    def revoke(self, jti: str, user: User):
        with self.sessions.begin() as session:
            session.add(AgentLog(user_id=user.id, log_type="AUTH", action="logout",
                                 params_summary=self._key(jti), result_status="SUCCESS"))
