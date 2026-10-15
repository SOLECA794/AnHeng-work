from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.auth.service import AuthError, AuthService, User

bearer = HTTPBearer(auto_error=False)
_auth_service: AuthService | None = None


def set_auth_service(auth: AuthService) -> None:
    global _auth_service
    _auth_service = auth


def get_auth_service() -> AuthService:
    if _auth_service is None:
        raise RuntimeError("authentication service is not initialized")
    return _auth_service


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    auth: AuthService = Depends(get_auth_service),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    try:
        return auth.current_user(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


def require_roles(*roles: str):
    """Create a FastAPI dependency enforcing one of the supplied roles."""
    allowed = frozenset(roles)

    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")
        return user

    return dependency
