from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.auth import router as auth_router
from src.api.agent_logs import router as agent_logs_router
from src.api.health import router as health_router
from src.auth.persistence import SqlAlchemyUserStore
from src.auth.service import AuthService, JwtService
from src.core.config import get_settings
from src.core.database import create_database
from src.core.handlers import install_handlers


def create_app(settings=None, *, auth_service=None):
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app):
        engine = provider = None
        try:
            if auth_service is not None:
                app.state.auth_service = auth_service
            else:
                development = settings.app_env.lower() in {"dev", "test"}
                engine, sessions = create_database(settings.database_url, development=development)
                store = SqlAlchemyUserStore(sessions)
                if development and store.get("admin") is None:
                    store.add("admin", settings.dev_admin_password, "ADMIN", "System administrator")
                app.state.sessions = sessions
                app.state.auth_service = AuthService(store, JwtService(
                    settings.jwt_secret_key, issuer=settings.app_name, ttl_seconds=settings.jwt_ttl_seconds))
                if settings.hengan_app_key and settings.hengan_app_secret:
                    from src.agent.audit import SqlAlchemyAgentAudit
                    from src.agent.hengan import HenganAgentProvider
                    provider = HenganAgentProvider(
                        settings.hengan_base_url, settings.hengan_app_key, settings.hengan_app_secret,
                        timeout_seconds=settings.hengan_timeout_seconds,
                        max_concurrency=settings.hengan_max_concurrency,
                        retry_attempts=settings.hengan_retry_attempts,
                        audit=SqlAlchemyAgentAudit(sessions))
                    app.state.agent_provider = provider
            yield
        finally:
            if provider:
                provider.close()
            if engine:
                engine.dispose()

    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    install_handlers(app)
    app.include_router(health_router, prefix=settings.api_prefix)
    app.include_router(auth_router, prefix=settings.api_prefix)
    app.include_router(agent_logs_router, prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    def root():
        return {"service": settings.app_name, "docs": "/docs"}

    return app


app = create_app()
