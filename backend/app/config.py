from functools import lru_cache
from typing import Self

from pydantic import model_validator
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

    # Client compatibility (see app/client_gate.py and CLAUDE.md). The API
    # contract is additive-only, so this stays at 0 — nothing is ever blocked —
    # until a change genuinely can't be made backwards compatible. Raising it
    # cuts off every installed app below that CFBundleVersion, so it is a
    # deploy-time decision, not a code one.
    min_ios_build: int = 0
    # What TestFlight/the App Store currently has, so an older-but-still-allowed
    # app can nudge the user without being locked out. Track `CFBundleVersion`
    # in ios/project.yml when that is bumped for an upload.
    current_ios_build: int = 13
    ios_upgrade_url: str | None = None

    # Timeout for fetching external recipe pages during ingestion.
    recipe_fetch_timeout_seconds: float = 15.0

    @model_validator(mode="after")
    def _client_floor_must_be_reachable(self) -> Self:
        # Requiring a build that was never shipped would lock every install out
        # with no way back except another deploy. Fail at startup instead.
        if self.min_ios_build > self.current_ios_build:
            raise ValueError(
                f"min_ios_build ({self.min_ios_build}) is above current_ios_build "
                f"({self.current_ios_build}) — ship that build first, then raise the floor"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
