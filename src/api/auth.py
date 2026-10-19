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


def user_data(user: User) -> dict:
    return {"id": user.id, "username": user.username, "nickname": user.nickname, "role": user.role}


@router.post("/login", response_model=ApiResponse)
def login(body: LoginRequest, auth: AuthService = Depends(get_auth_service)) -> ApiResponse:
    try:
        token, user = auth.login(body.username, body.password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return success({"token": token, "tokenType": "Bearer", "expiresIn": auth.jwt.ttl_seconds, "user": user_data(user)})


class PasswordRequest(BaseModel):
    oldPassword: str = Field(min_length=1, max_length=128)
    newPassword: str = Field(min_length=8, max_length=128)


@router.post("/password", response_model=ApiResponse)
def password(body: PasswordRequest, user: User = Depends(current_user),
             auth: AuthService = Depends(get_auth_service)) -> ApiResponse:
    try:
        auth.change_password(user, body.oldPassword, body.newPassword)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail="原密码错误或凭证已变更") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="新密码不合法或与旧密码相同") from exc
    return success(message="密码已更新，请重新登录")


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
