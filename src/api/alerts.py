"""SDD-03 alerts inbox API; the default application does not mount these routes."""
from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from src.api.finding_verification import VerificationRoute
from src.services.finding_verification import VerificationError
from src.services.alerts import AlertError


def _number(value):
    return int(value) if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 19 else value


def create_alert_router(service, *, principal_dependency=None, clock=None):
    def no_identity():
        return None
    dependency = principal_dependency or no_identity
    router = APIRouter(prefix="/api/alerts", tags=["alerts"], route_class=VerificationRoute)

    def invoke(operation):
        try:
            return dict(code=0, message="ok", data=operation())
        except VerificationError as error:
            status = 401 if error.code == 40100 else 403 if error.code in {40300, 40310} else 404 if error.code == 40400 else 409 if error.code == 40902 else 400 if error.code == 40001 else 500
            return JSONResponse(status_code=status, content=dict(code=error.code, message=error.message, data=None))
        except Exception:
            return JSONResponse(status_code=500, content=dict(code=50000, message="告警接口处理失败", data=None))

    @router.get("")
    def get_alerts(page: str = "1", pageSize: str = "20", status: str | None = None,
                   severity: str | None = None, scopeId: str | None = None, principal=Depends(dependency)):
        return invoke(lambda: service.list(principal, page=_number(page), page_size=_number(pageSize), status=status,
                                           severity=severity, scope_id=_number(scopeId) if scopeId is not None else None))

    @router.post("/{alert_id}/read")
    def mark_read(alert_id: str, principal=Depends(dependency)):
        return invoke(lambda: service.mark_read(_number(alert_id), principal, observed_at=clock() if clock else None))

    @router.patch("/{alert_id}/status")
    def patch_status(alert_id: str, payload: Any = Body(default=None), principal=Depends(dependency)):
        def apply():
            # Identity still comes solely from the dependency; payload cannot grant access.
            if not isinstance(payload, dict) or set(payload) != {"status"}:
                raise AlertError(40001, "告警状态参数不合法")
            return service.set_status(_number(alert_id), payload["status"], principal, observed_at=clock() if clock else None)
        return invoke(apply)

    return router
