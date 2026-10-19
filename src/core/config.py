from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator


class Settings(BaseSettings):
    app_name: str = "AnHeng Security Inspection Service"
    app_env: str = "dev"
    app_version: str = "0.1.0"
    api_prefix: str = "/api"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    hengan_base_url: str = "https://gc.das-ai.com"
    hengan_app_key: str = ""
    hengan_app_secret: str = ""
    hengan_agent_id: str = ""
    jwt_secret_key: str = "change-me-in-development"
    dev_admin_password: str = "change-me-admin-password"
    database_url: str = "sqlite+pysqlite:///./.local/anheng.db"
    jwt_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    hengan_timeout_seconds: float = Field(default=90, gt=0)
    hengan_max_concurrency: int = Field(default=2, ge=1, le=2)
    hengan_retry_attempts: int = Field(default=3, ge=0, le=5)

    @model_validator(mode="after")
    def validate_runtime(self):
        if len(self.jwt_secret_key) < 16:
            raise ValueError("JWT_SECRET_KEY must contain at least 16 characters")
        if self.app_env.lower() not in {"dev", "test"}:
            if self.jwt_secret_key == "change-me-in-development" or len(self.jwt_secret_key) < 32:
                raise ValueError("Production requires an explicit JWT_SECRET_KEY of at least 32 characters")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("Production requires a PostgreSQL DATABASE_URL")
        return self

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
