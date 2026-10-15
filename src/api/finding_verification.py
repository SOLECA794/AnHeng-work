"""Opt-in T22 routes; no trusted identity dependency means fail-closed access."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException

from src.services.finding_verification import VerificationError


class StatusPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["CONFIRMED", "FALSE_POSITIVE"]
    reasonCode: Literal["EVIDENCE_REVIEWED", "EXPECTED_PUBLIC_CONTENT", "RULE_FALSE_MATCH", "AUTHORIZED_CHANGE"]
    expectedStatus: str = "OPEN"
    note: str | None = Field(default=None, max_length=1000)


def _number(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 19:
        return int(value)
    return value


def _failure(error):
    code = error.code
    status = (401 if code == 40100 else 403 if code in {40300, 40310} else
              404 if code == 40400 else 409 if code in {40902, 40903} else
              400 if code == 40001 else 500)
    return JSONResponse(status_code=status, content={"code": code, "message": error.message, "data": None})


class VerificationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                return _failure(VerificationError(40001, "请求参数不合法"))
            except HTTPException as error:
                code = {400: 40001, 401: 40100, 403: 40300, 404: 40400}.get(error.status_code, 50000)
                return _failure(VerificationError(code, "请求未通过校验或鉴权"))

        return safe_handler


def create_verification_router(service, *, principal_dependency=None, clock=None) -> APIRouter:
    """The hosting app must inject authenticated, active user/asset grants from T09."""
    def no_identity():
        return None

    principal_dependency = principal_dependency or no_identity
    router = APIRouter(prefix="/api/findings", tags=["finding-verification"], route_class=VerificationRoute)

    @router.patch("/{finding_id}/status")
    def patch_status(finding_id: str, payload: Any = Body(default=None), principal=Depends(principal_dependency)):
        try:
            try:
                request = StatusPatch.model_validate(payload)
            except ValidationError:
                # Feed a non-executable decision through service authorization/audit.
                # Never echo Pydantic's input/error context, which can contain secrets.
                service.verify(_number(finding_id), None, None, principal,
                               observed_at=clock() if clock else None)
                raise VerificationError(40001, "请求参数不合法")
            data = service.verify(_number(finding_id), request.status, request.reasonCode, principal,
                                  note=request.note, expected_status=request.expectedStatus,
                                  observed_at=clock() if clock else None)
            return {"code": 0, "message": "ok", "data": data}
        except VerificationError as error:
            return _failure(error)
        except Exception:
            return _failure(VerificationError(50000, "接口处理失败"))

    @router.get("/{finding_id}/verification")
    def get_verification(finding_id: str, limit: str = "100", principal=Depends(principal_dependency)):
        try:
            data = service.get(_number(finding_id), principal, limit=_number(limit))
            return {"code": 0, "message": "ok", "data": data}
        except VerificationError as error:
            return _failure(error)
        except Exception:
            return _failure(VerificationError(50000, "接口处理失败"))

    return router
