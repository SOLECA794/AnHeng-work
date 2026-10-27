"""T25 routes accept request identity only; verdict and target come from trusted runner."""
from typing import Any
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from src.api.finding_verification import VerificationRoute
from src.services.finding_verification import VerificationError, require_principal
from src.services.retesting import RetestError


def _number(value):
    return int(value) if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 19 else value


def create_retest_router(service, *, principal_dependency=None):
    def no_identity():
        return None
    dependency = principal_dependency or no_identity
    router = APIRouter(tags=["retesting"], route_class=VerificationRoute)

    def invoke(operation):
        try:
            return dict(code=0, message="ok", data=operation())
        except VerificationError as error:
            status = 401 if error.code == 40100 else 403 if error.code in {40300, 40310} else 404 if error.code == 40400 else 409 if error.code in {40902, 40903} else 400 if error.code == 40001 else 500
            return JSONResponse(status_code=status, content=dict(code=error.code, message=error.message, data=None))
        except Exception:
            return JSONResponse(status_code=500, content=dict(code=50000, message="复测接口处理失败", data=None))

    @router.post("/api/findings/{finding_id}/retest")
    def retest(finding_id: str, payload: Any = Body(default=None), principal=Depends(dependency)):
        def execute():
            require_principal(principal)
            body = {} if payload is None else payload
            if not isinstance(body, dict) or set(body) - {"requestId"}:
                raise RetestError(40001, "仅接受复测请求编号")
            return service.retest(_number(finding_id), principal, request_id=body.get("requestId"))
        return invoke(execute)

    @router.get("/api/findings/{finding_id}/retests")
    def history(finding_id: str, limit: str = "50", principal=Depends(dependency)):
        return invoke(lambda: service.history(_number(finding_id), principal, limit=_number(limit)))

    @router.post("/api/auth-scopes/{scope_id}/retest")
    def batch(scope_id: str, payload: Any = Body(default=None), principal=Depends(dependency)):
        def execute():
            require_principal(principal)
            body = {} if payload is None else payload
            if not isinstance(body, dict) or set(body) - {"requestId", "limit", "afterId"}:
                raise RetestError(40001, "批量复测参数不合法")
            return service.retest_scope(_number(scope_id), principal, request_id=body.get("requestId"),
                                        limit=body.get("limit", 20), after_id=body.get("afterId", 0))
        return invoke(execute)

    return router
