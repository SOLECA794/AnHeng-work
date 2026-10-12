from typing import Any
from pydantic import BaseModel
from src.core.errors import ErrorCode


class ApiResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: Any = None


def success(data: Any = None, message: str = "ok") -> ApiResponse:
    return ApiResponse(code=ErrorCode.OK, message=message, data=data)


def failure(code: ErrorCode | int, message: str, data: Any = None) -> ApiResponse:
    return ApiResponse(code=int(code), message=message, data=data)
