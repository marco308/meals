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

A cap nothing can be bought to lift is a ceiling wearing a cap's clothes, and
answers 403 too — sending 402 to a household already on the largest tier this
server offers, or to a comped one, or on a self-hosted box that sells nothing at
all, would be pointing at something that does not exist. `UPGRADE_PATH` is the
whole of that judgement, and it is deliberately a path rather than "is some
other tier bigger".

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

The other axis
--------------

§3 has a second table, "Per instance", and it is a different question: not what
one household costs but how many of them the box holds. `MAX_HOUSEHOLDS` and
`MAX_USERS` live at the bottom of this module under "instance ceilings". They
share the shape — unset by default, refuse only growth, say what to do next —
and nothing else: no tier reaches them, no upgrade path leads out of them, and
they answer 503 because the caller has done nothing wrong and the server is
simply full.
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
    """Archived plans don't count, and that is the difference between a cap and
    a wall.

    There is no `DELETE /plans/{id}` — a plan is archived rather than removed,
    because its `cooked_events` are the record of what the household actually
    ate. So counting every row would mean a cap nothing could ever bring a
    household back under: on the free tier they would plan their twentieth week
    and never plan another, with the iPhone app's "add to plan" dead behind it
    (`PlanStore.planForWriting` creates the plan implicitly). §5 promises plans
    stay usable, so what this bounds is how many pools a household is juggling,
    and finishing a week — the action they already take — is what frees the
    place for the next one.
    """
    return await _count(db, Plan, Plan.household_id == household_id, Plan.status != "archived")


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
    #: Whether `count` answers for the household as a whole. `False` for an
    #: allowance scoped to one parent — a meal's lines, a plan's meals — where
    #: "how many are used" has no answer until the caller names which meal or
    #: which plan. `GET /limits` publishes the allowance for those and leaves
    #: the usage null rather than inventing a household-wide number for them.
    household_wide: bool = True


RESOURCES: dict[str, _Spec] = {
    "members": _Spec(
        singular="member",
        plural="members",
        scope="per household",
        holder="this household has",
        count=_count_members,
        hint=(
            "This counts everyone in the household, so a place frees up only when one of them leaves "
            "(DELETE /auth/household/members/{user_id}) — and a household of one has nobody to remove."
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
        household_wide=False,
    ),
    "plans": _Spec(
        singular="plan",
        plural="plans",
        scope="per household",
        holder="this household has",
        count=_count_plans,
        hint=(
            "Archived plans do not count, so finishing a week frees a place: "
            "POST /plans/{plan_id}/archive on one you are done with, which keeps its cooked history."
        ),
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
        household_wide=False,
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
            "This counts every member's tokens, and GET /auth/tokens lists your own — revoke one you "
            "are no longer using (DELETE /auth/tokens/{token_id}), since an unused token is a "
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
    for name, value in instance_ceilings().items():
        # A ceiling of zero is meaningful (a server closed to new arrivals but
        # still serving everyone on it); a negative one is a typo.
        if value is not None and value < 0:
            raise ValueError(
                f"MAX_{name.upper()} is {value!r}; an instance ceiling is a non-negative whole "
                "number, or unset for no ceiling"
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
    # A ceiling belongs to the server rather than to a tier, and so does a cap
    # met by a comped household, so neither names one.
    allowance = (
        f"This server's {tier} tier allows {_quantity(limit, spec)} {spec.scope}"
        if kind == "cap" and tier in NAMED_TIERS
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

#: Where a tier can move *up* to, and the only thing that ever justifies a 402.
#: Deliberately a path rather than "is any other tier bigger": `unlimited` is a
#: comp, not something anyone buys, so a household on it — or on the largest
#: tier this server offers — has nothing to be sold and must not be told
#: otherwise. Without this, a self-hoster who capped one tier and left the rest
#: alone would have their own server answer them 402 Payment Required.
UPGRADE_PATH: dict[str, tuple[str, ...]] = {FREE: (PAID,)}

#: The tiers a refusal will name. `unlimited` is not one of them: a household on
#: it is comped, so "this server's unlimited tier allows 10 recipes" would be a
#: sentence that contradicts itself.
NAMED_TIERS = (FREE, PAID)


def _upgradable(tier: str, resource: str, cap: int) -> bool:
    """Whether a tier above this one would actually lift this cap."""
    for better in UPGRADE_PATH.get(tier, ()):
        allowance = getattr(limits_for(better), resource)
        if allowance is None or allowance > cap:
            return True
    return False


def effective_tier(household: Household) -> str:
    """The tier this household is actually treated as being on.

    A column holding a value this build has not heard of resolves to
    `unlimited` rather than raising, for the same reason `limits_for` does: a
    household must never be locked out of its own data by a tier name from a
    newer deployment.
    """
    return household.tier if household.tier in TIERS else UNLIMITED


def _verdict(household: Household, spec: _Spec, resource: str, *, used: int, adding: int) -> LimitExceeded | None:
    """Whether this household may add `adding` more of `resource`, and if not,
    the refusal to raise. The single place caps, ceilings and status codes meet.
    """
    tier = effective_tier(household)
    cap = getattr(limits_for(tier), resource)
    ceiling = getattr(ceilings(), resource)
    after = used + adding

    # The ceiling is checked first: it is the lower bound on what this server
    # will do at all, and when both would refuse, "no tier fixes this" is the
    # more useful thing to have been told.
    if ceiling is not None and after > ceiling:
        return _refuse(household, spec, resource, tier=tier, limit=ceiling, used=used, kind="ceiling")
    if cap is not None and after > cap:
        upgradable = _upgradable(tier, resource, cap)
        return _refuse(household, spec, resource, tier=tier, limit=cap, used=used, kind="cap", upgradable=upgradable)
    return None


def _has_limit(household: Household, resource: str) -> bool:
    """Whether either a cap or a ceiling applies here — the check that keeps an
    unconfigured server from ever running a COUNT."""
    tier = effective_tier(household)
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


# ----------------------------------------------------------------- publication

# §4: "Better than a good error is not hitting the wall at all, so limits are
# published." An assistant about to import two hundred recipes can ask what it
# is allowed before it starts, rather than finding out on the fifty-first — and
# on a server that has configured nothing, the honest answer is "no limits",
# which is why `GET /limits` answers rather than 404s.


@dataclass(frozen=True)
class ResourceAllowance:
    """One row of `GET /limits`: what this household is allowed, and how much of
    it is spent."""

    resource: str
    #: The number this household will actually meet, whichever of the tier cap
    #: and the fair-use ceiling is lower. `None` means unlimited.
    limit: int | None
    #: How many exist now, or `None` when the number has no meaning here:
    #: unlimited (so nothing was counted), or an allowance scoped to one meal
    #: or one plan, which has no household-wide answer.
    used: int | None
    remaining: int | None
    #: Where the allowance applies: "per household", "in one meal", "a month".
    scope: str
    #: Whether a larger tier on this server would raise this number. `False` is
    #: the ceiling, the top tier and the self-hosted default alike, and it is
    #: the same judgement that decides 402 from 403 on a refusal.
    upgradable: bool


@dataclass(frozen=True)
class LimitsSnapshot:
    tier: str
    #: Whether this deployment limits anything at all. `False` on a self-hosted
    #: server that has set nothing, where every allowance below is unlimited.
    limited: bool
    resources: tuple[ResourceAllowance, ...]


def _effective(tier: str, resource: str) -> tuple[int | None, bool]:
    """The binding number for this tier and resource, and whether money lifts it.

    Ties go to the ceiling, because `_verdict` checks the ceiling first: when a
    tier's cap has caught up with it, "no tier fixes this" is the true answer.
    """
    cap = getattr(limits_for(tier), resource)
    ceiling = getattr(ceilings(), resource)
    if ceiling is not None and (cap is None or cap >= ceiling):
        return ceiling, False
    if cap is None:
        return None, False
    return cap, _upgradable(tier, resource, cap)


async def snapshot(db: AsyncSession, household: Household) -> LimitsSnapshot:
    """Every allowance this household has, with usage where usage means anything.

    Counts only what is actually limited, which keeps the module's first promise
    on the endpoint too: a self-hosted server answers this without running a
    single query, because there is nothing to be short of.
    """
    tier = effective_tier(household)
    allowances = []
    for resource, spec in RESOURCES.items():
        limit, upgradable = _effective(tier, resource)
        used = None
        if limit is not None and spec.count is not None and spec.household_wide:
            used = await spec.count(db, household.id, None)
        allowances.append(
            ResourceAllowance(
                resource=resource,
                limit=limit,
                used=used,
                remaining=None if limit is None or used is None else max(0, limit - used),
                scope=spec.scope,
                upgradable=upgradable,
            )
        )
    return LimitsSnapshot(tier=tier, limited=anything_configured(), resources=tuple(allowances))


def free_tier_allowances() -> dict[str, int | None]:
    """The free tier's own numbers, for the unauthenticated pricing table (§4).

    The tier caps rather than the effective numbers: a pricing table is about
    what the tiers differ by, and a ceiling is the same in all of them. On a
    server that has configured nothing every value is `None`, which says
    "unlimited" and reveals no hosted tier that does not exist.
    """
    free = limits_for(FREE)
    return {name: getattr(free, name) for name in RESOURCE_NAMES}


# ------------------------------------------------------------ instance ceilings

# §3's other half, "Per instance". Everything above bounds what *one household*
# costs; nothing above says how many households the box can hold, and a server
# that accepts the two-hundredth family because no number stopped it does not
# fail politely. `MAX_HOUSEHOLDS` and `MAX_USERS` are that number, and like
# every other one here they default to unset: a self-hoster who configures
# nothing is in exactly the position they were before, taking everyone who
# arrives.
#
# Three things make this a different animal from the caps above, and each one
# shows up in the code:
#
# - **It is not about the caller.** No tier lifts it, no upgrade path leads out
#   of it, and the household reaching it has done nothing wrong — the server is
#   simply full. So it answers **503**, not 402 or 403: this is capacity, and a
#   waitlist means "later, yes", which is the one thing a status code can carry
#   that 403 cannot.
# - **Only registration can cross it.** `POST /auth/invites/redeem` moves an
#   existing user between existing households and can only ever *lower* the
#   household count (`move_user_to_household` collects the one they left), so it
#   is deliberately not checked — refusing it would block a move that costs the
#   server nothing.
# - **Who is knocking changes the sentence.** Somebody starting a household of
#   their own is a stranger the server has no room for, and the honest answer is
#   the waitlist. Somebody registering against an invite is expected: a
#   household here issued them a code and is waiting. Turning them away with a
#   waitlist sentence would be telling them to queue for something they have
#   already been let into.

HOUSEHOLDS = "households"
USERS = "users"
INSTANCE_RESOURCES = (HOUSEHOLDS, USERS)


class InstanceFull(Exception):
    """This deployment holds as many households, or accounts, as it will.

    Not an `HTTPException` for the same reason `LimitExceeded` isn't: one place
    in `app/main.py` owns turning it into a response.
    """

    status_code = 503

    def __init__(self, *, resource: str, limit: int, used: int, detail: str) -> None:
        super().__init__(detail)
        self.resource = resource
        self.limit = limit
        self.used = used
        self.detail = detail

    def payload(self) -> dict[str, object]:
        return {"detail": self.detail, "resource": self.resource, "limit": self.limit, "used": self.used}


def instance_ceilings() -> dict[str, int | None]:
    """What this deployment will hold, `None` for "as much as it can"."""
    settings = get_settings()
    return {HOUSEHOLDS: settings.max_households, USERS: settings.max_users}


_INSTANCE_MODELS = {HOUSEHOLDS: Household, USERS: User}

#: What each ceiling counts, singular and plural, so "at most 1 account" reads
#: like English on a server that really is that small.
_INSTANCE_NOUNS = {HOUSEHOLDS: ("household", "households"), USERS: ("account", "accounts")}

#: One sentence per (resource, was-invited). Every one of them says the server
#: is full rather than that the caller did something wrong, and none of them
#: mentions money: a full instance is not something a bigger tier fixes.
_INSTANCE_REFUSALS = {
    (HOUSEHOLDS, False): (
        "This server is full: it holds at most {allowance} and has {used:,}. Nothing here is broken "
        "and no existing account is affected — ask whoever runs it to put you on the waitlist, and "
        "register once they say there is room. If you were sent an invite code, register with it "
        "instead: joining an existing household needs no new one."
    ),
    (USERS, False): (
        "This server is full: it holds at most {allowance} and has {used:,}. Nothing here is broken "
        "and no existing account is affected — ask whoever runs it to put you on the waitlist, and "
        "register once they say there is room."
    ),
    (USERS, True): (
        "This server is full: it holds at most {allowance} and has {used:,}, so it cannot open another "
        "one yet. Your invite code has not been used and the household that sent it is still here — "
        "ask whoever runs the server to make room, then register with the same code. Nobody needs to "
        "send you a new one."
    ),
}


def _instance_refusal(resource: str, *, limit: int, used: int, invited: bool) -> str:
    singular, plural = _INSTANCE_NOUNS[resource]
    allowance = f"{limit:,} {singular if limit == 1 else plural}"
    return _INSTANCE_REFUSALS[(resource, invited)].format(allowance=allowance, used=used)


async def admit_registration(db: AsyncSession, *, invited: bool) -> None:
    """Refuse a registration this server has no room for.

    Called from `POST /auth/register` once the request is known to be otherwise
    good, and before anything is written — so a refusal leaves no half-made
    household behind and, for an invited caller, leaves their code unredeemed.

    `invited` decides both what is counted and what the caller is told: a new
    household needs room for a household *and* an account, while an invited
    member only ever adds the account.
    """
    ceilings = instance_ceilings()
    resources = INSTANCE_RESOURCES if not invited else (USERS,)
    for resource in resources:
        limit = ceilings[resource]
        if limit is None:
            continue  # the default everywhere: this server holds what it holds
        used = await _count(db, _INSTANCE_MODELS[resource])
        if used < limit:
            continue
        # The operator's signal, and the reason it is worth alerting on: by the
        # time this fires, somebody has already been turned away.
        log_event("instance.full", outcome=resource, limit=limit, used=used, invited=invited)
        raise InstanceFull(
            resource=resource,
            limit=limit,
            used=used,
            detail=_instance_refusal(resource, limit=limit, used=used, invited=invited),
        )


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
