from fastapi import FastAPI

from src.api.auth import router as auth_router
from src.api.health import router as health_router
from src.auth.dependencies import set_auth_service
from src.auth.service import AuthService, InMemoryUserStore, JwtService
from src.core.config import get_settings

settings = get_settings()

# T09: in-memory users keep the first milestone runnable. Replace the store
# with a SQLAlchemy-backed implementation without changing AuthService callers.
_user_store = InMemoryUserStore()
_user_store.add("admin", settings.dev_admin_password, "ADMIN", "System administrator")
_auth_service = AuthService(
    _user_store,
    JwtService(settings.jwt_secret_key, issuer=settings.app_name, ttl_seconds=3600),
)
set_auth_service(_auth_service)

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.include_router(health_router, prefix=settings.api_prefix)
app.include_router(auth_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs"}
