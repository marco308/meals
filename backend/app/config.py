import json
from functools import lru_cache
from typing import Annotated, Any, Self

from pydantic import BeforeValidator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _blank_is_unset(value: object) -> object:
    """An empty or whitespace-only string means "not set".

    `- MAX_HOUSEHOLDS=${MAX_HOUSEHOLDS:-}` is the ordinary way to wire an
    optional value through a compose or stack file, and "unset" is what a
    deployer means by it. The string-valued settings already behave that way
    without anybody arranging it (`METRICS_TOKEN=` arrives as `""`, which every
    reader here treats as falsy), so without this the two kinds of setting
    disagree for no reason that is visible from the outside, and they disagree
    by refusing to boot on the node rather than by failing anything the deploy
    could have caught. Wiring a number the way a token is wired is not a
    mistake worth a crash.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


# An int a deployment may leave unset, including by wiring it to an empty
# `${VAR:-}` expansion. `None` is the only spelling of "unset" any reader sees.
OptionalInt = Annotated[int | None, BeforeValidator(_blank_is_unset)]


def _json_object_or_unset(value: object) -> object:
    """The same rule for a setting whose value is JSON: blank overrides nothing.

    This one needs `NoDecode` beside it, because a dict field is "complex" and
    pydantic-settings JSON-decodes it in the environment source, before any
    validator of ours can be reached. Blank therefore failed with a
    `SettingsError` naming no line of JSON and offering no fix, which is a
    worse boot crash than the ints got. Taking the decoding over costs one
    `json.loads` and buys a refusal that says which setting and what was wrong
    with it.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"not valid JSON ({exc}); leave it empty to override nothing") from exc
    return value


# A JSON object a deployment may leave unset the same way, e.g.
# `- LIMITS_OVERRIDES=${LIMITS_OVERRIDES:-}`. Empty means "override nothing",
# which is the profile as published.
JsonOverrides = Annotated[
    dict[str, dict[str, int | None]],
    NoDecode,
    BeforeValidator(_json_object_or_unset),
]


class BlankIsDefault:
    """Annotation marker: blank means "use the default written on the field".

    `OptionalInt` and `JsonOverrides` can say that with a validator, because
    "unset" has a spelling in their type. A setting with a real default has
    none, so this is a marker instead: `_blank_means_default` below drops the
    value before pydantic sees it, and the default is then taken from the one
    place it is written down. That is the point rather than a detail of the
    mechanism. The stack file repeats six of these defaults today
    (`- SMTP_PORT=${SMTP_PORT:-587}`) for no reason except that an empty string
    would fail validation at boot, and a default written in two places is one
    that will eventually disagree with itself.

    It is opt-in per setting on purpose. `DATABASE_URL=${DATABSE_URL:-}` with
    the variable name fat-fingered is a typo, and a server that quietly booted
    onto the default SQLite path instead of failing would hide it until
    somebody went looking for their data.
    """


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

    registration_enabled: Annotated[bool, BlankIsDefault] = True
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
    current_ios_build: int = 27
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
    limits_profile: Annotated[str, BlankIsDefault] = "unlimited"
    limits_overrides: JsonOverrides = {}
    default_household_tier: Annotated[str, BlankIsDefault] = "unlimited"

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
    max_households: OptionalInt = None
    max_users: OptionalInt = None

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

    # Starting a checkout (issue #121). The webhook is the *end* of a payment;
    # these are what it takes to begin one, and they are deliberately separate
    # settings: a deployment can take webhooks with no key on the box that can
    # charge anybody, which is the safer half to turn on first.
    #
    #   BILLING_API_KEY     the processor's secret key. The only credential here
    #                       that can create a charge, so it is never logged and
    #                       never reaches a client.
    #   BILLING_PRICE_ID    what is being sold: a Stripe price id, a Paddle
    #                       price id, or a Lemon Squeezy *variant* id.
    #   BILLING_STORE_ID    Lemon Squeezy only, which wants the store beside the
    #                       variant.
    #   BILLING_API_BASE    override the processor's API host. Paddle's sandbox
    #                       is a different one (sandbox-api.paddle.com), and it
    #                       is how the tests point at a stub.
    #   BILLING_MANAGE_URL  where a household goes to change a card, read an
    #                       invoice or cancel. With a merchant of record that is
    #                       the processor's own portal, so it is a URL out of
    #                       their dashboard rather than anything served here.
    #   BILLING_PRICE_PENCE / BILLING_PRICE_CURRENCY  what to *say* it costs, on
    #                       the one screen that offers it. No default, like every
    #                       other number in this project: a deployment that has
    #                       not set one shows no price rather than someone
    #                       else's.
    billing_api_key: str | None = None
    billing_price_id: str | None = None
    billing_store_id: str | None = None
    billing_api_base: str | None = None
    billing_manage_url: str | None = None
    billing_price_pence: OptionalInt = None
    billing_price_currency: Annotated[str, BlankIsDefault] = "GBP"
    billing_api_timeout_seconds: float = 20.0

    @property
    def billing_configured(self) -> bool:
        return bool(self.billing_processor and self.billing_webhook_secret)

    @property
    def billing_sells(self) -> bool:
        """Whether this deployment can actually take money.

        Deliberately stricter than `billing_configured`: a server that can only
        *receive* webhooks has no checkout to offer, and offering one it cannot
        create would be a button that 500s. This is the single answer to "does
        this server sell anything", published on `/client-config` so no client
        has to infer it from a 404 or, worse, from the shape of the limits.
        """
        return bool(self.billing_configured and self.billing_api_key and self.billing_price_id)

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
    smtp_port: Annotated[int, BlankIsDefault] = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None  # falls back to smtp_username
    smtp_start_tls: Annotated[bool, BlankIsDefault] = True
    # Reset codes are short-lived on purpose: emailed in plaintext, and holding
    # one is enough to change a password.
    password_reset_ttl_minutes: int = 30

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and (self.smtp_from or self.smtp_username))

    @property
    def email_sender(self) -> str:
        return self.smtp_from or self.smtp_username or "meals@localhost"

    @model_validator(mode="before")
    @classmethod
    def _blank_means_default(cls, values: Any) -> Any:
        # Dropping the key is what makes pydantic fall back to the default, so
        # this has to run before field validation rather than after it. Only
        # fields carrying the marker are dropped; see BlankIsDefault for why
        # that is not every field.
        if not isinstance(values, dict):
            return values
        marked = {name for name, field in cls.model_fields.items() if BlankIsDefault in field.metadata}
        return {
            key: value
            for key, value in values.items()
            if not (key in marked and isinstance(value, str) and not value.strip())
        }

    @model_validator(mode="after")
    def _a_server_that_sells_needs_somebody_to_sell_to(self) -> Self:
        # A household that starts on the top tier already has everything a
        # subscription would buy, so `POST /billing/checkout` correctly refuses
        # it — and a deployment that enabled billing and left this alone would
        # have a checkout nobody on it can ever use, with no error to explain
        # why. Fail at boot instead, where it is one line to fix.
        if self.billing_sells and self.default_household_tier != "free":
            raise ValueError(
                f"billing is configured but DEFAULT_HOUSEHOLD_TIER is {self.default_household_tier!r}: "
                "a new household would already have everything a subscription buys, so nothing could be sold. "
                "Set DEFAULT_HOUSEHOLD_TIER=free."
            )
        return self

    @model_validator(mode="after")
    def _lemonsqueezy_needs_its_store(self) -> Self:
        # Lemon Squeezy is the one processor whose checkout names two things,
        # and a missing store id fails at the API rather than at boot, which is
        # to say at the first person trying to pay.
        if self.billing_processor == "lemonsqueezy" and self.billing_price_id and not self.billing_store_id:
            raise ValueError("BILLING_STORE_ID is required with lemonsqueezy: its checkout names a store and a variant")
        return self

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
