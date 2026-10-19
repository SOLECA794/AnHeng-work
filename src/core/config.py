from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
