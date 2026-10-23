"""Opt-in SDD-03 report endpoints. Identity and asset grants must be trusted."""
from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, StrictInt, ValidationError

from src.api.finding_verification import VerificationRoute, _failure, _number
from src.services.finding_verification import VerificationError


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scopeId: StrictInt
    periodBegin: str
    periodEnd: str


def create_report_router(service, *, principal_dependency=None, clock=None):
    router = APIRouter(prefix="/api/reports", tags=["reports"], route_class=VerificationRoute)
    dependency = principal_dependency or (lambda: None)

    def invoke(call):
        try:
            return {"code": 0, "message": "ok", "data": call()}
        except VerificationError as error:
            return _failure(error)
        except Exception:
            return _failure(VerificationError(50000, "报告处理失败"))

    @router.post("/generate")
    def generate(payload: Any = Body(default=None), principal=Depends(dependency)):
        def action():
            from src.services.finding_verification import require_principal
            require_principal(principal, write=False)
            try:
                request = GenerateRequest.model_validate(payload)
            except ValidationError:
                raise VerificationError(40001, "请求参数不合法") from None
            return service.generate(request.scopeId, request.periodBegin, request.periodEnd, principal,
                                    at=clock() if clock else None)
        return invoke(action)

    @router.get("")
    def list_reports(scopeId: str | None = None, page: str = "1", pageSize: str = "20", principal=Depends(dependency)):
        return invoke(lambda: service.list(principal, scope_id=_number(scopeId), page=_number(page), page_size=_number(pageSize)))

    @router.get("/{report_id}")
    def detail(report_id: str, principal=Depends(dependency)):
        return invoke(lambda: service.get(_number(report_id), principal))

    @router.get("/{report_id}/file")
    def file(report_id: str, format: str = "html", principal=Depends(dependency)):
        try:
            content = service.download(_number(report_id), format, principal)
            return Response(content, media_type="text/html" if format == "html" else "application/pdf", headers={
                "Content-Disposition": f'attachment; filename="inspection-{int(report_id)}.{format}"',
                "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"})
        except VerificationError as error:
            return _failure(error)
        except Exception:
            return _failure(VerificationError(50000, "报告下载失败"))

    return router
