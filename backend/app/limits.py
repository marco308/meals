"""Per-household limits: config-driven, and unlimited unless a deployment says
otherwise.

This is the enforcement half of `planning/08-freemium.md`. The rule that shapes
every line of it is §1: **a quota on one person's hosting, never a fence around
the tool**. So:

- every number here defaults to `None`, which means unlimited;
- with nothing configured, `enforce()` returns before it runs a single query, so
  a self-hosted instance pays nothing and behaves exactly as it did before;
- nothing anywhere says a hosted tier exists. A 402 on a server that sells
  nothing is unreachable, because the numbers that produce it are unset.

There is no closed fork: the deployment at meals.marcuslab.uk sets
`LIMITS_PROFILE=hosted` and gets the numbers from §3 of that document, and
anyone else can set the same thing, or their own numbers, or nothing at all.

Two kinds of limit, and they are not the same error (§4)
--------------------------------------------------------

- **A tier cap** is what a bigger tier would fix, so it answers **402**. The
  status code carries that meaning for a web client; the *sentence* deliberately
  does not, because iOS shows 4xx details verbatim and the app must stay free of
  commerce (§6 — "an error string is a call to action if it points anywhere").
  The sentence is equally true on a self-hosted box, which is the tell that the
  framing is right.
- **A fair-use ceiling** protects the box, so no tier fixes it and it answers
  **403**. A *paid* household reaching one is usually a bug rather than a heavy
  user, so every block emits `limit.reached`; alert on
  `outcome="ceiling" AND tier="paid"`.

A cap the top tier cannot lift is a ceiling wearing a cap's clothes, and answers
403 too: sending 402 to someone already on the largest tier this server offers
would be telling them to buy something that does not exist.

What this module does *not* carry
---------------------------------

Four rows of §3's table are deliberately absent, and each absence is a decision
rather than an omission:

- **Items per list** and **archived shops**. Both live under `/shopping-list*`,
  which §5 exempts from every billing block for the same reason the client gate
  exempts it (Q11): iOS replays its offline queue through those endpoints and
  drops any op the server rejects with anything but "offline" or "unauthorised"
  (`ios/.../ShoppingListStore.swift`). A 402 there destroys the user's data
  rather than reducing their features, and no unpaid invoice justifies that.
- **Archived shops readable.** §3 would show a free household its last three
  shops; §5 promises that "everything already there stays readable". The second
  wins here: hiding rows someone already wrote is the one thing lapse semantics
  say we do not do.
- **Requests per token.** That is a rate limiter answering 429, not a count
  answering 402 — a different mechanism with a different window, and it belongs
  next to `deps.auth_rate_limit` rather than here.

Counting
--------

Plain `COUNT`s on the existing `household_id` indexes, and **no locking**: two
concurrent creates that overshoot a cap by one are cheaper to tolerate than to
prevent, which is §3's own instruction.
"""

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AuthToken, Household, Ingredient, Meal, Plan, PlanMeal, Recipe, Supermarket, User
from app.observability import log_event

# ---------------------------------------------------------------------- tiers

#: The tier a household is on. `unlimited` is the default everywhere and is also
#: how a hosted instance comps someone: it lifts every *cap*, though never the
#: ceilings, which exist to protect the box rather than to sell anything.
UNLIMITED = "unlimited"
FREE = "free"
PAID = "paid"
TIERS = (UNLIMITED, FREE, PAID)

#: The ceiling is not a tier — it applies whatever tier a household is on — but
#: it is configured the same way, so it shares the table.
CEILING = "ceiling"
LIMIT_SETS = (*TIERS, CEILING)


@dataclass(frozen=True)
class Limits:
    """One set of numbers. `None` means unlimited, which is every field's
    default: an unconfigured server has no limits, not zero of everything.

    Every field is a limit this module actually applies. Numbers we publish but
    do not enforce would be a lie the moment `GET /limits` (#95) reads them.
    """

    members: int | None = None
    recipes: int | None = None
    ingredients: int | None = None
    meals: int | None = None
    meal_lines: int | None = None
    plans: int | None = None
    plan_meals: int | None = None
    supermarkets: int | None = None
    api_tokens: int | None = None
    ingests_per_month: int | None = None


UNLIMITED_LIMITS = Limits()

#: `LIMITS_PROFILE` picks one of these. The numbers in `hosted` are §3 of
#: planning/08-freemium.md, in this repo under the same licence as everything
#: else — a self-hoster can read them, use them, or ignore them.
PROFILES: dict[str, dict[str, Limits]] = {
    UNLIMITED: {
        UNLIMITED: UNLIMITED_LIMITS,
        FREE: UNLIMITED_LIMITS,
        PAID: UNLIMITED_LIMITS,
        CEILING: UNLIMITED_LIMITS,
    },
    "hosted": {
        UNLIMITED: UNLIMITED_LIMITS,
        FREE: Limits(
            members=1,
            recipes=50,
            ingredients=500,
            meals=100,
            meal_lines=50,
            plans=20,
            plan_meals=30,
            supermarkets=2,
            api_tokens=3,
            ingests_per_month=20,
        ),
        PAID: Limits(
            members=8,
            recipes=2_000,
            ingredients=5_000,
            meals=2_000,
            meal_lines=50,
            plans=1_000,
            plan_meals=30,
            supermarkets=20,
            api_tokens=10,
            ingests_per_month=500,
        ),
        CEILING: Limits(
            members=12,
            recipes=5_000,
            ingredients=10_000,
            meals=5_000,
            meal_lines=50,
            plans=2_000,
            plan_meals=30,
            supermarkets=20,
            api_tokens=10,
            ingests_per_month=1_000,
        ),
    },
}

RESOURCE_NAMES = tuple(field.name for field in fields(Limits))


# ------------------------------------------------------------------ resources

Counter = Callable[[AsyncSession, uuid.UUID, uuid.UUID | None], Awaitable[int]]


async def _count(db: AsyncSession, model: type, *where: object) -> int:
    result = await db.execute(select(func.count()).select_from(model).where(*where))
    return int(result.scalar_one())


async def _count_members(db: AsyncSession, household_id: uuid.UUID, _within: uuid.UUID | None) -> int:
    return await _count(db, User, User.household_id == household_id)


async def _count_recipes(db: AsyncSession, household_id: uuid.UUID, _within: uuid.UUID | None) -> int:
    return await _count(db, Recipe, Recipe.household_id == household_id)


async def _count_ingredients(db: AsyncSession, household_id: uuid.UUID, _within: uuid.UUID | None) -> int:
    return await _count(db, Ingredient, Ingredient.household_id == household_id)


async def _count_meals(db: AsyncSession, household_id: uuid.UUID, _within: uuid.UUID | None) -> int:
    return await _count(db, Meal, Meal.household_id == household_id)


async def _count_plans(db: AsyncSession, household_id: uuid.UUID, _within: uuid.UUID | None) -> int:
    return await _count(db, Plan, Plan.household_id == household_id)


async def _count_plan_meals(db: AsyncSession, _household_id: uuid.UUID, within: uuid.UUID | None) -> int:
    return await _count(db, PlanMeal, PlanMeal.plan_id == within)


async def _count_supermarkets(db: AsyncSession, household_id: uuid.UUID, _within: uuid.UUID | None) -> int:
    return await _count(db, Supermarket, Supermarket.household_id == household_id)


async def _count_api_tokens(db: AsyncSession, household_id: uuid.UUID, _within: uuid.UUID | None) -> int:
    """Per household, not per user: §3's table is per household throughout, and
    the limit is credential hygiene, which is a property of the data the tokens
    reach rather than of who minted them."""
    result = await db.execute(
        select(func.count())
        .select_from(AuthToken)
        .join(User, AuthToken.user_id == User.id)
        .where(User.household_id == household_id, AuthToken.kind == "api")
    )
    return int(result.scalar_one())


async def _count_ingests(db: AsyncSession, household_id: uuid.UUID, _within: uuid.UUID | None) -> int:
    """Read off the household's own counter rather than counting rows.

    Recipes can be deleted, and a count of rows would hand the quota back every
    time one was — which is exactly the loop someone using this server as a
    general-purpose fetcher would run. `reserve_ingest` only ever increments.
    """
    household = await db.get(Household, household_id)
    if household is None:
        return 0
    return _ingests_this_month(household)


@dataclass(frozen=True)
class _Spec:
    """How one resource counts itself and how it says no."""

    singular: str
    plural: str
    #: Where the allowance applies: "per household", "in one meal", "a month".
    scope: str
    #: Who is holding the existing ones, and in what tense.
    holder: str
    #: How to find out how many there are. `None` for a limit whose answer is
    #: always in the payload the caller is holding, which is `meal_lines`: a
    #: PATCH *replaces* a meal's lines rather than adding to them, so counting
    #: the rows that are there would refuse an edit that made the meal smaller.
    count: Counter | None
    #: What to do instead. Appended to the refusal, so it is the last thing an
    #: assistant reads and the first thing it can act on.
    hint: str = ""


RESOURCES: dict[str, _Spec] = {
    "members": _Spec(
        singular="member",
        plural="members",
        scope="per household",
        holder="this household has",
        count=_count_members,
        hint=(
            "Everyone already in the household keeps full access to the recipes, plans and shopping list; "
            "this only stops another person joining."
        ),
    ),
    "recipes": _Spec(
        singular="recipe",
        plural="recipes",
        scope="per household",
        holder="this household has",
        count=_count_recipes,
        hint="Delete a recipe nobody cooks (DELETE /recipes/{recipe_id}) to make room for this one.",
    ),
    "ingredients": _Spec(
        singular="ingredient",
        plural="ingredients",
        scope="per household",
        holder="this household has",
        count=_count_ingredients,
        hint=(
            "A library this size usually holds duplicates: GET /ingredients/duplicates finds them, and "
            "POST /ingredients/{keeper_id}/merge folds each group into one of the ingredients you "
            "already have, which frees rows without losing anything."
        ),
    ),
    "meals": _Spec(
        singular="meal",
        plural="meals",
        scope="per household",
        holder="this household has",
        count=_count_meals,
        hint="Delete a meal you no longer plan (DELETE /meals/{meal_id}); its cooked history survives.",
    ),
    "meal_lines": _Spec(
        singular="line",
        plural="lines",
        scope="in one meal",
        holder="this one would have",
        count=None,
        hint=(
            "A line is one recipe or one loose ingredient. Split this into two meals and put both on the "
            "plan — a plan is a pool of options, so two entries cost nothing."
        ),
    ),
    "plans": _Spec(
        singular="plan",
        plural="plans",
        scope="per household",
        holder="this household has",
        count=_count_plans,
        hint="Delete an old archived plan, or reuse one: POST /plans accepts copy_from_plan_id.",
    ),
    "plan_meals": _Spec(
        singular="meal",
        plural="meals",
        scope="in one plan",
        holder="this plan has",
        count=_count_plan_meals,
        hint=(
            "A plan is a week's pool of options rather than a calendar, so this many is already more than "
            "a week of choices — archive it (POST /plans/{plan_id}/archive) and start the next one."
        ),
    ),
    "supermarkets": _Spec(
        singular="supermarket",
        plural="supermarkets",
        scope="per household",
        holder="this household has",
        count=_count_supermarkets,
        hint="Delete one you no longer shop at (DELETE /supermarkets/{supermarket_id}).",
    ),
    "api_tokens": _Spec(
        singular="API token",
        plural="API tokens",
        scope="per household",
        holder="this household has",
        count=_count_api_tokens,
        hint=(
            "Revoke one you are no longer using (DELETE /auth/tokens/{token_id}) — an unused token is a "
            "credential nobody is watching."
        ),
    ),
    "ingests_per_month": _Spec(
        singular="recipe URL ingest",
        plural="recipe URL ingests",
        scope="a month",
        holder="this household has used",
        count=_count_ingests,
        hint=(
            "Fetching a page is the only thing this counts, and both POST /recipes/ingest and "
            "POST /recipes/{recipe_id}/reparse do it. Read the page yourself and send what it says "
            "instead — POST /recipes with parse_source='ai' for a new recipe, PATCH /recipes/{recipe_id} "
            "to correct one — exactly as a page with no JSON-LD already asks you to."
        ),
    ),
}

assert set(RESOURCES) == set(RESOURCE_NAMES), "every Limits field needs a _Spec, and vice versa"


# ------------------------------------------------------------------ resolution


@lru_cache(maxsize=8)
def _table(profile: str, overrides_json: str) -> dict[str, Limits]:
    """The four number sets this deployment runs, overrides applied.

    Cached on its inputs rather than on nothing, so a test that changes the
    environment gets a different table without having to know this cache exists.
    """
    table = dict(PROFILES[profile])
    for name, changes in json.loads(overrides_json).items():
        table[name] = replace(table[name], **changes)
    return table


def _current_table() -> dict[str, Limits]:
    settings = get_settings()
    return _table(settings.limits_profile, json.dumps(settings.limits_overrides, sort_keys=True))


def limits_for(tier: str) -> Limits:
    """The caps for a tier. An unrecognised tier resolves to unlimited rather
    than raising: a household must never be locked out of its own data because a
    column holds a value this build has not heard of."""
    return _current_table().get(tier, UNLIMITED_LIMITS)


def ceilings() -> Limits:
    """The fair-use ceilings, which apply whatever tier a household is on."""
    return _current_table()[CEILING]


def default_tier() -> str:
    return get_settings().default_household_tier


def anything_configured() -> bool:
    """Whether this deployment has set any limit at all.

    The whole module hangs off this: unset, `enforce` returns before it looks at
    the household, let alone counts anything.
    """
    settings = get_settings()
    return settings.limits_profile != UNLIMITED or bool(settings.limits_overrides)


def check_settings() -> None:
    """Validate the `LIMITS_*` environment at startup.

    Called from `app/main.py` at import, because a typo in a limit should stop
    the container rather than surface as a 500 on somebody's fiftieth recipe.
    """
    settings = get_settings()
    if settings.limits_profile not in PROFILES:
        raise ValueError(
            f"LIMITS_PROFILE={settings.limits_profile!r} is not a profile; choose one of {', '.join(sorted(PROFILES))}"
        )
    if settings.default_household_tier not in TIERS:
        raise ValueError(
            f"DEFAULT_HOUSEHOLD_TIER={settings.default_household_tier!r} is not a tier; "
            f"choose one of {', '.join(TIERS)}"
        )
    for name, changes in settings.limits_overrides.items():
        if name not in LIMIT_SETS:
            raise ValueError(f"LIMITS_OVERRIDES has a '{name}' section; the sections are {', '.join(LIMIT_SETS)}")
        for resource, value in changes.items():
            if resource not in RESOURCE_NAMES:
                raise ValueError(
                    f"LIMITS_OVERRIDES['{name}'] sets '{resource}', which is not a limit; "
                    f"the limits are {', '.join(RESOURCE_NAMES)}"
                )
            # Anything that isn't a whole number or null has already been
            # refused by pydantic; what it can't know is that a limit is a
            # count, so a negative one is meaningless rather than merely small.
            if value is not None and value < 0:
                raise ValueError(
                    f"LIMITS_OVERRIDES['{name}']['{resource}'] is {value!r}; a limit is a "
                    "non-negative whole number, or null for unlimited"
                )
    # Build the table once so a bad override value fails here rather than later.
    _current_table()


# ------------------------------------------------------------------- refusals


class LimitExceeded(Exception):
    """A write that would grow a household past one of its limits.

    Deliberately not an `HTTPException`: this is raised from the service layer,
    and `app/main.py` owns the one place it becomes a response.
    """

    def __init__(
        self,
        *,
        status_code: int,
        resource: str,
        tier: str,
        limit: int,
        used: int,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.resource = resource
        self.tier = tier
        self.limit = limit
        self.used = used
        self.detail = detail

    @property
    def kind(self) -> str:
        return "cap" if self.status_code == 402 else "ceiling"

    def payload(self) -> dict[str, object]:
        """The response body. `detail` is what every client shows the user; the
        rest is for a caller that wants to reason about it (and for `GET /limits`
        to line up with)."""
        return {
            "detail": self.detail,
            "resource": self.resource,
            "limit": self.limit,
            "used": self.used,
            "tier": self.tier,
        }


def _quantity(count: int, spec: _Spec) -> str:
    return f"{count:,} {spec.singular if count == 1 else spec.plural}"


def _refusal(spec: _Spec, *, tier: str, limit: int, used: int, kind: str, upgradable: bool) -> str:
    """The sentence a person and an assistant both read.

    It names the limit, the tier and the number in use (§4), and it points
    nowhere: no price, no upgrade, no address to write to. On a self-hosted
    instance every word of it is still true, which is the test §6 sets for
    anything the iPhone app might render.
    """
    allowance = (
        f"This server's {tier} tier allows {_quantity(limit, spec)} {spec.scope}"
        if kind == "cap"
        else f"This server allows at most {_quantity(limit, spec)} {spec.scope}"
    )
    tail = (
        "Nothing has been removed and everything already here still works, so this only stops it growing."
        if upgradable
        else (
            "No tier on this server goes beyond that, so it is not something this household can change — "
            "going further needs a word with whoever runs the server."
        )
    )
    return f"{allowance}, and {spec.holder} {used:,}. {tail} {spec.hint}".strip()


# ------------------------------------------------------------------ enforcement

#: The tiers a household can actually move between. `unlimited` is a comp rather
#: than a tier anyone buys, so it is not evidence that a bigger allowance exists:
#: without this, every cap would look upgradable and answer 402.
PURCHASABLE_TIERS = (FREE, PAID)


def _best_purchasable_cap(resource: str) -> int | None:
    """The largest cap any purchasable tier offers, or None if one of them is
    unlimited. What decides whether a cap is worth a 402 at all."""
    table = _current_table()
    caps = [getattr(table[tier], resource) for tier in PURCHASABLE_TIERS]
    if any(cap is None for cap in caps):
        return None
    return max(caps)  # type: ignore[type-var]


def _verdict(household: Household, spec: _Spec, resource: str, *, used: int, adding: int) -> LimitExceeded | None:
    """Whether this household may add `adding` more of `resource`, and if not,
    the refusal to raise. The single place caps, ceilings and status codes meet.
    """
    tier = household.tier if household.tier in TIERS else UNLIMITED
    cap = getattr(limits_for(tier), resource)
    ceiling = getattr(ceilings(), resource)
    after = used + adding

    # The ceiling is checked first: it is the lower bound on what this server
    # will do at all, and when both would refuse, "no tier fixes this" is the
    # more useful thing to have been told.
    if ceiling is not None and after > ceiling:
        return _refuse(household, spec, resource, tier=tier, limit=ceiling, used=used, kind="ceiling")
    if cap is not None and after > cap:
        best = _best_purchasable_cap(resource)
        upgradable = best is None or best > cap
        return _refuse(household, spec, resource, tier=tier, limit=cap, used=used, kind="cap", upgradable=upgradable)
    return None


def _has_limit(household: Household, resource: str) -> bool:
    """Whether either a cap or a ceiling applies here — the check that keeps an
    unconfigured server from ever running a COUNT."""
    tier = household.tier if household.tier in TIERS else UNLIMITED
    return getattr(limits_for(tier), resource) is not None or getattr(ceilings(), resource) is not None


def _refuse(
    household: Household,
    spec: _Spec,
    resource: str,
    *,
    tier: str,
    limit: int,
    used: int,
    kind: str,
    upgradable: bool = False,
) -> LimitExceeded:
    error = LimitExceeded(
        status_code=402 if upgradable else 403,
        resource=resource,
        tier=tier,
        limit=limit,
        used=used,
        detail=_refusal(spec, tier=tier, limit=limit, used=used, kind=kind, upgradable=upgradable),
    )
    # The alert the issue asks for lives on this line: a *paid* household meeting
    # a ceiling is usually a bug in what we let them do rather than a heavy user,
    # so it is worth waking somebody rather than only telling them.
    log_event(
        "limit.reached",
        outcome=error.kind,
        household_id=household.id,
        resource=resource,
        tier=tier,
        limit=limit,
        used=used,
    )
    return error


async def _resolve_household(db: AsyncSession, household: Household | uuid.UUID) -> Household | None:
    if isinstance(household, Household):
        return household
    # Usually free: the caller's household was eagerly loaded with the user, so
    # this is an identity-map hit rather than a query.
    return await db.get(Household, household)


async def enforce(
    db: AsyncSession,
    household: Household | uuid.UUID,
    resource: str,
    *,
    adding: int = 1,
    used: int | None = None,
    within: uuid.UUID | None = None,
) -> None:
    """Refuse a write that would take `resource` past this household's limit.

    Call it from the service layer, immediately before the row is added, and let
    it decide everything: a router never holds a number and never chooses a
    status code.

    `used` short-circuits the count for a limit whose answer the caller already
    has in front of it (a meal's lines are in the payload). `within` scopes the
    count to a parent, for the per-meal and per-plan limits.

    Raises `LimitExceeded`. It runs before the insert, so nothing of this write
    has been staged when it does.
    """
    if not anything_configured():
        return  # the self-hosted default: no queries, no behaviour, no cost

    row = await _resolve_household(db, household)
    if row is None:  # deleted underneath us; the write will fail on its own terms
        return
    if not _has_limit(row, resource):
        return

    spec = RESOURCES[resource]
    if used is None:
        assert spec.count is not None, f"{resource} has no counter and must be enforced with used="
        used = await spec.count(db, row.id, within)
    refusal = _verdict(row, spec, resource, used=used, adding=adding)
    if refusal is not None:
        raise refusal


# ------------------------------------------------------------------ ingest quota

# The one limit that costs real bandwidth (§3), and so the one that cannot be a
# `COUNT`: deleting the recipe would hand the quota back, and delete-and-retry is
# precisely the loop the limit exists to stop. The household carries a counter
# for the calendar month instead, and it only ever goes up.


def _month_start(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(start: datetime) -> datetime:
    # Day 28 plus four days lands in the next month whatever its length, and
    # snapping to the 1st finds the boundary without any calendar arithmetic.
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def _as_aware(value: datetime) -> datetime:
    # SQLite round-trips datetimes naive; stored values are UTC (deps.as_aware).
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _ingests_this_month(household: Household) -> int:
    """This month's ingest count, without writing.

    A counter left over from an earlier month reads as zero rather than being
    reset here, so a caller that is only *looking* never dirties the row — and
    a month nobody ingested in needs no job to roll it over.
    """
    started = household.ingest_period_started_at
    if started is None or _as_aware(started) < _month_start(datetime.now(UTC)):
        return 0
    return household.ingests_used


async def reserve_ingest(db: AsyncSession, household: Household) -> None:
    """Charge one URL ingest against this household's month, or refuse.

    Charged **up front and committed**, before the page is fetched, the same way
    `deps.auth_rate_limit` charges an attempt before it is known to be good: a
    fetch that turns out to be bot-blocked or to carry no JSON-LD has already
    cost the bandwidth this limit protects, and a refund on failure would make
    the limit free to evade. There is deliberately no refund to forget.

    Committing here is safe because both callers have staged nothing at this
    point — a URL already in the library is answered from it before anything is
    fetched (Q3), which is the whole reason the number can be as small as it is.

    Both callers: `POST /recipes/ingest` and `POST /recipes/{id}/reparse`. A
    re-parse makes exactly the same outbound request for exactly the same
    reason, and metering only the first would leave the limit one POST away
    from being bypassed.
    """
    if not anything_configured():
        return
    if not _has_limit(household, "ingests_per_month"):
        return

    spec = RESOURCES["ingests_per_month"]
    month = _month_start(datetime.now(UTC))
    assert spec.count is not None
    used = await spec.count(db, household.id, None)
    # The reset date is the most useful thing an assistant that has just been
    # refused can be told, and it is only knowable here.
    dated = replace(spec, hint=f"The count resets on {_next_month(month).strftime('%-d %B %Y')}. {spec.hint}")

    refusal = _verdict(household, dated, "ingests_per_month", used=used, adding=1)
    if refusal is not None:
        raise refusal

    household.ingest_period_started_at = month
    household.ingests_used = used + 1
    await db.commit()
