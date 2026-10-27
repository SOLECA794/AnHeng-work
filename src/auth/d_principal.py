"""Bridge B's verified current user to D's server-authorized Principal."""
from __future__ import annotations

from collections.abc import Callable
from inspect import iscoroutinefunction

from fastapi import Depends, HTTPException

from src.auth.dependencies import current_user
from src.auth.service import User
from src.services.finding_verification import Principal, _id

GrantResolver = Callable[[int], frozenset[int]]


def create_d_principal_dependency(grant_resolver: GrantResolver | None = None):
    """Resolve grants on each request; the resolver must be trusted server code.

    No resolver means an empty grant set. This function never derives grants
    from request payloads, headers, JWT custom claims, or the set of all assets.
    """
    if grant_resolver is not None and (
        not callable(grant_resolver) or iscoroutinefunction(grant_resolver)
        or iscoroutinefunction(getattr(grant_resolver, "__call__", None))
    ):
        raise ValueError("grant resolver must be a synchronous server callable")

    def principal(user: User = Depends(current_user)) -> Principal:
        if not isinstance(user, User) or not _id(user.id) or user.enabled is not True:
            raise HTTPException(status_code=401, detail="身份无效")
        if user.role not in {"SEC_OPS", "RULE_ADMIN"}:
            raise HTTPException(status_code=403, detail="无此业务权限")
        try:
            grants = frozenset() if grant_resolver is None else grant_resolver(user.id)
            if type(grants) is not frozenset or any(not _id(value) for value in grants):
                raise ValueError("invalid server grants")
        except Exception:
            # HTTPException is deliberately used inside Depends: existing D
            # safe routes convert it to a sanitized standard response envelope.
            raise HTTPException(status_code=500, detail="资产授权解析失败") from None
        return Principal(user.id, user.role, grants, active=True)

    return principal
