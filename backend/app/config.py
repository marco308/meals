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

    # Where a *browser* hitting the API root lands. Unset, it falls back to
    # the interactive docs, which is the right default for a self-hosted
    # instance; the reference deployment sets the YAMP marketing site so the
    # bare domain has a public face. Machine clients never see this — the
    # JSON landing at `/` is unaffected.
    marketing_url: str | None = None

    # Requests per minute per IP on the auth endpoints (public API hardening).
    auth_rate_limit_per_minute: int = 10

    # Wide open on purpose. This is a bearer-token API with no cookies and no
    # session state, so a browser never attaches a credential of its own — a
    # cross-origin page can only reach it by already holding a token, which CORS
    # doesn't defend against anyway. Narrow this if you put a web frontend in
    # front of the API and want defence in depth.
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
    current_ios_build: int = 22
    ios_upgrade_url: str | None = None

    # Timeout for fetching external recipe pages during ingestion.
    recipe_fetch_timeout_seconds: float = 15.0

    # Outbound email, used only for password resets (Q20). Unset by default: the
    # app has always run with no mail configured, so a self-hoster who doesn't
    # set these gets a clear 503 from the reset endpoint rather than a silently
    # broken feature. Plain SMTP rather than a provider SDK, so any relay works
    # and nobody is pushed towards a paid account.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None  # falls back to smtp_username
    smtp_start_tls: bool = True
    # Reset codes are short-lived on purpose: emailed in plaintext, and holding
    # one is enough to change a password.
    password_reset_ttl_minutes: int = 30

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and (self.smtp_from or self.smtp_username))

    @property
    def email_sender(self) -> str:
        return self.smtp_from or self.smtp_username or "meals@localhost"

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
