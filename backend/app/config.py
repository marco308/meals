from functools import lru_cache
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Meals"
    environment: str = "development"

    # Logging (app/observability.py). LOG_FORMAT is "json" or "text"; unset,
    # it follows the environment — JSON in production for whatever ships the
    # container logs, text everywhere a human is reading the terminal.
    log_level: str = "INFO"
    log_format: str = ""

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

    # The MCP server, served by this process at /mcp (app/mcp_mount.py) so a
    # single container is the whole product. Off, the route does not exist and
    # the separate mcp/ image is the only way to reach it remotely.
    mcp_enabled: bool = True
    # Where the embedded MCP server calls the API back. It is an HTTP client
    # like any other (it never touches the database), so it needs our own
    # origin: loopback on whatever port uvicorn is serving.
    mcp_api_url: str = "http://127.0.0.1:8000"

    # Requests per minute per IP on the auth endpoints (public API hardening).
    auth_rate_limit_per_minute: int = 10

    # Prometheus metrics (app/metrics.py). Unset, GET /metrics 404s and no
    # background work runs; set, the endpoint answers to
    # `Authorization: Bearer <METRICS_TOKEN>` only — it shares the public
    # host, so it must be safe while reachable from the internet.
    metrics_token: str | None = None

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
    current_ios_build: int = 26
    ios_upgrade_url: str | None = None

    # Per-household limits (app/limits.py, planning/08-freemium.md). Every
    # number defaults to unlimited, and these three settings are the only way
    # any of them becomes a real limit — a self-hoster who sets nothing sees no
    # cap, no paywall, and no mention that a hosted tier exists.
    #
    #   LIMITS_PROFILE          "unlimited" (default) or "hosted", which is the
    #                           table from that document's §3.
    #   LIMITS_OVERRIDES        JSON, applied on top of the profile, so a
    #                           deployment can tune any single number without a
    #                           code change or a whole profile of its own:
    #                             {"free": {"recipes": 100},
    #                              "ceiling": {"ingredients": null}}
    #                           null means unlimited.
    #   DEFAULT_HOUSEHOLD_TIER  which tier a newly registered household starts
    #                           on. Existing households keep whatever their row
    #                           says, which for every household that predates
    #                           this is "unlimited".
    limits_profile: str = "unlimited"
    limits_overrides: dict[str, dict[str, int | None]] = {}
    default_household_tier: str = "unlimited"

    # Instance ceilings (planning/08-freemium.md §3, "Per instance"). The
    # limits above bound what one household costs; these bound how many
    # households the box holds at all, which is the half that makes capacity
    # planning real. Unset — the default — a server takes everyone it can fit
    # and falls over on its own terms, exactly as it always has.
    #
    #   MAX_HOUSEHOLDS  registrations that would start household N+1 are
    #                   refused with a waitlist sentence rather than accepted
    #                   onto a box with no room for them.
    #   MAX_USERS       the same for accounts, because an invited member grows
    #                   the user count without growing the household count.
    max_households: int | None = None
    max_users: int | None = None

    # Entitlements (app/services/entitlements.py, planning/08-freemium.md §5).
    # Only ever read for a household that has a `paid_until`, which on a
    # self-hosted instance is none of them.
    #
    #   ENTITLEMENT_GRACE_DAYS  how long after expiry before the free tier's
    #                           caps re-apply. Apple's billing grace period has
    #                           no equivalent here because there is no Apple, so
    #                           §5 sets it at 14 days.
    #   DUNNING_WARN_DAYS       how long before expiry the first email goes out.
    entitlement_grace_days: int = 14
    dunning_warn_days: int = 7

    # Billing webhook (app/services/billing.py, planning/08-freemium.md §2).
    # **Off unless both of these are set**, the same shape SMTP has: a
    # self-hosted instance has no billing and must not be able to acquire one by
    # accident, so with BILLING_PROCESSOR unset the endpoint does not exist at
    # all (404, exactly like /metrics with no token).
    #
    #   BILLING_PROCESSOR   'stripe', 'paddle' or 'lemonsqueezy'. All three are
    #                       merchants of record, which is the point (§7): they
    #                       are the legal seller, so EU B2C digital-services VAT
    #                       — due from the first sale regardless of the UK
    #                       threshold — is theirs to file rather than ours.
    #                       'stripe' means Stripe **Managed Payments**, not
    #                       ordinary Stripe, which leaves the tax with you.
    #   BILLING_WEBHOOK_SECRET  the signing secret from that processor's
    #                       dashboard. Every request is verified against it.
    #   BILLING_SIGNATURE_TOLERANCE_SECONDS  how old a signed timestamp may be
    #                       (Paddle only; Lemon Squeezy does not sign one).
    #                       Paddle's SDKs default to 5s, which is tight enough
    #                       that one slow hop loses a payment; replay is already
    #                       prevented by the event ledger, so this is generous
    #                       on purpose.
    billing_processor: str | None = None
    billing_webhook_secret: str | None = None
    billing_signature_tolerance_seconds: int = 300

    @property
    def billing_configured(self) -> bool:
        return bool(self.billing_processor and self.billing_webhook_secret)

    # Timeout for fetching external recipe pages during ingestion.
    recipe_fetch_timeout_seconds: float = 15.0
    # And a ceiling on how much of one we'll read (issue #55). The URL is the
    # caller's, and api runs one replica next to its own Postgres, so an
    # endless response is a memory spike on the database's machine. Recipe
    # pages are heavy with markup and still land well under this.
    recipe_fetch_max_bytes: int = 5 * 1024 * 1024

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
