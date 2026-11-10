"""Database-backed D grants. Maintenance is a trusted operator operation."""
from sqlalchemy import delete, select

from src.models import AuthScope, DScopeGrant, SysUser
from src.services.finding_verification import _id


class SqlAlchemyDGrants:
    def __init__(self, sessions):
        self.sessions = sessions

    def resolve(self, user_id):
        if not _id(user_id):
            raise ValueError("invalid user identifier")
        with self.sessions() as session:
            return frozenset(session.scalars(select(DScopeGrant.scope_id)
                .join(AuthScope, AuthScope.id == DScopeGrant.scope_id)
                .join(SysUser, SysUser.id == DScopeGrant.user_id)
                .where(DScopeGrant.user_id == user_id, AuthScope.deleted == 0,
                       AuthScope.status == "ENABLED", SysUser.deleted == 0, SysUser.status == 1,
                       SysUser.role.in_(("SEC_OPS", "RULE_ADMIN")))))

    def replace(self, user_id, scope_ids):
        """Atomically replace an explicit grant set; empty revokes all grants."""
        if not _id(user_id) or type(scope_ids) is not frozenset or any(not _id(i) for i in scope_ids):
            raise ValueError("invalid user or asset identifiers")
        with self.sessions.begin() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            user = session.scalar(select(SysUser).where(SysUser.id == user_id).with_for_update())
            if user is None or (scope_ids and (
                user.deleted != 0 or user.status != 1 or user.role not in {"SEC_OPS", "RULE_ADMIN"}
            )):
                raise ValueError("user is not eligible for D grants")
            if scope_ids and set(session.scalars(select(AuthScope.id).where(
                AuthScope.id.in_(scope_ids), AuthScope.deleted == 0, AuthScope.status == "ENABLED"
            ).order_by(AuthScope.id).with_for_update())) != scope_ids:
                raise ValueError("asset is missing or disabled")
            session.execute(delete(DScopeGrant).where(DScopeGrant.user_id == user_id))
            session.add_all(DScopeGrant(user_id=user_id, scope_id=scope_id) for scope_id in sorted(scope_ids))
