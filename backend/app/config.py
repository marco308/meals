from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Meals"
    environment: str = "development"

    # SQLite by default so a bare `make run` works with zero services;
    # docker-compose overrides this with the Postgres URL.
    database_url: str = "sqlite+aiosqlite:///./data/meals.db"

    registration_enabled: bool = True
    session_token_ttl_days: int = 30

    # Requests per minute per IP on the auth endpoints (public API hardening).
    auth_rate_limit_per_minute: int = 10

    cors_origins: list[str] = ["*"]

    # Timeout for fetching external recipe pages during ingestion.
    recipe_fetch_timeout_seconds: float = 15.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
