from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from src.auth.dependencies import current_user, get_auth_service
from src.auth.service import AuthError, AuthService, User
from src.core.response import ApiResponse, success

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


def user_data(user: User) -> dict[str, str]:
    return {"username": user.username, "nickname": user.nickname, "role": user.role}


@router.post("/login", response_model=ApiResponse)
def login(body: LoginRequest, auth: AuthService = Depends(get_auth_service)) -> ApiResponse:
    try:
        token, user = auth.login(body.username, body.password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return success({"token": token, "tokenType": "Bearer", "user": user_data(user)})


@router.get("/me", response_model=ApiResponse)
def me(user: User = Depends(current_user)) -> ApiResponse:
    return success(user_data(user))


@router.post("/logout", response_model=ApiResponse)
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    auth: AuthService = Depends(get_auth_service),
) -> ApiResponse:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    try:
        auth.logout(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return success(message="logged out")
