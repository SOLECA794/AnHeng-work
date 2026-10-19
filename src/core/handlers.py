"""SDD-03 error envelopes; validation responses never echo request inputs."""
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from src.agent.provider import AgentError
from src.core.exceptions import ServiceError


def error_status(code):
    return (401 if code == 40100 else 403 if code in {40300, 40310} else
            404 if code == 40400 else 409 if 40900 <= code < 41000 else
            400 if 40000 <= code < 40100 else 502 if code in {50001, 50002} else 500)


def envelope(code, message, *, status=None, headers=None):
    return JSONResponse(status_code=status or error_status(code), headers=headers,
                        content={"code": code, "message": message, "data": None})


def install_handlers(app):
    @app.exception_handler(HTTPException)
    async def http_error(_request, error):
        code = {400: 40001, 401: 40100, 403: 40300, 404: 40400, 405: 40001,
                409: 40902, 429: 50002}.get(error.status_code, 50000)
        message = {401: "未登录或 Token 失效", 403: "无权限", 404: "资源不存在"}.get(
            error.status_code, "请求未通过校验")
        return envelope(code, message, status=error.status_code,
                        headers={"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _error):
        return envelope(40001, "请求参数不合法")

    @app.exception_handler(ServiceError)
    async def service_error(_request, error):
        return envelope(error.code, error.message)

    @app.exception_handler(AgentError)
    async def agent_error(_request, error):
        return envelope(error.project_code, error.message)

    @app.exception_handler(Exception)
    async def system_error(_request, _error):
        return envelope(50000, "系统异常")
