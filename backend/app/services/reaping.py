"""Reaping households that never began, after warning them (issue #122, Q26).

An open registration form accumulates households that were made and never used:
somebody signed up, looked, and went. They hold nothing, but they hold an email
address, and they count against `MAX_HOUSEHOLDS`. §5's "nothing is deleted" is a
promise about a household that *lapsed*, and it stands: this never touches one
that has held anything at all. The test is deliberately narrow, and every part of
it has to hold at once:

- **one member**, since a second one means somebody was invited;
- **nothing in it** that a person made: no recipe, meal, plan, list line,
  ingredient, supermarket, cooked meal, freezer batch or invite. An empty active
  list does not count, because merely opening the app makes one;
- **no money involved, ever**: no expiry, no entitlement source, no processor
  customer, and the member pays for nothing anywhere, so a comp is never reaped;
- **no sign-in** for `REAP_ABANDONED_AFTER_DAYS`, counting any token being made
  or used, and the household at least that old.

Such a household is emailed once, and deleted `REAP_WARNING_DAYS` after that
email went, unless somebody came back in between, which clears the mark so the
next idle spell earns its own warning. A relay failure marks nothing, so no
household is ever deleted on a warning it did not get: with no SMTP, nothing is
warned and so nothing is reaped. Deleting is `accounts.delete_user`, the same
path `DELETE /auth/me` takes for a last member.

Off unless `REAP_ABANDONED_AFTER_DAYS` is set, so a self-hosted instance never
loses anything to it. There is no scheduler in the app: this is
`python -m app.reaping` from cron, like dunning.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps import AUTHENTICATING_KINDS
from app.models import (
    AuthToken,
    CookedEvent,
    FreezerItem,
    Household,
    HouseholdInvite,
    Ingredient,
    ListItem,
    Meal,
    Plan,
    Recipe,
    ShoppingList,
    Supermarket,
    User,
)
from app.observability import log_event
from app.services.accounts import delete_user, household_user_count
from app.services.mailer import EmailSendFailed, send_email

WARNING = "warning"
REAP = "reap"


@dataclass(frozen=True)
class Notice:
    """One household that is due a warning, or due to go."""

    kind: str
    household_id: uuid.UUID
    household_name: str
    user_id: uuid.UUID
    to: str
    reap_on: datetime


def enabled() -> bool:
    return get_settings().reap_abandoned_after_days is not None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _holds_anything(db: AsyncSession, household_id: uuid.UUID) -> bool:
    """Whether anybody ever put something in this household.

    Stricter than `accounts.household_has_content`, which counts a shopping list
    and so answers yes for anyone who has opened the app once: here only a
    *line* on a list counts."""
    for model in (Recipe, Meal, Plan, Ingredient, Supermarket, CookedEvent, FreezerItem, HouseholdInvite):
        found = await db.execute(select(model.id).where(model.household_id == household_id).limit(1))
        if found.scalar_one_or_none() is not None:
            return True
    line = await db.execute(
        select(ListItem.id)
        .join(ShoppingList, ListItem.list_id == ShoppingList.id)
        .where(ShoppingList.household_id == household_id)
        .limit(1)
    )
    return line.scalar_one_or_none() is not None


async def _last_seen(db: AsyncSession, user: User) -> datetime:
    """The latest moment this account showed signs of life: made, signed in
    (which makes a token), or used a token. A token is never used before it is
    made, so its last use, or failing that its making, is its latest moment."""
    latest = (
        await db.execute(
            select(func.max(func.coalesce(AuthToken.last_used_at, AuthToken.created_at))).where(
                # Credentials only: anybody can have a reset code sent to an
                # address, and that must not keep an account alive.
                AuthToken.user_id == user.id,
                AuthToken.kind.in_(AUTHENTICATING_KINDS),
            )
        )
    ).scalar_one_or_none()
    created = _aware(user.created_at)
    return created if latest is None else max(created, _aware(latest))


async def _abandoned(db: AsyncSession, household: Household, lead: User, *, now: datetime) -> bool:
    idle_for = timedelta(days=get_settings().reap_abandoned_after_days or 0)
    if _aware(household.created_at) > now - idle_for:
        return False
    if household.paid_until is not None or household.entitlement_source is not None:
        return False
    if household.billing_customer_id is not None or household.billing_user_id is not None:
        return False
    pays_elsewhere = await db.execute(select(Household.id).where(Household.billing_user_id == lead.id).limit(1))
    if pays_elsewhere.scalar_one_or_none() is not None:
        return False
    if await household_user_count(db, household.id) != 1:
        return False
    if await _last_seen(db, lead) > now - idle_for:
        return False
    return not await _holds_anything(db, household.id)


async def review(db: AsyncSession, *, now: datetime | None = None) -> tuple[list[Notice], list[uuid.UUID]]:
    """What is due now, and which warned households have since come back.

    Nothing at all when reaping is off."""
    if not enabled():
        return [], []
    now = now or datetime.now(UTC)
    grace = timedelta(days=get_settings().reap_warning_days)
    result = await db.execute(
        select(Household, User).join(User, Household.lead_user_id == User.id).order_by(Household.created_at)
    )
    due: list[Notice] = []
    returned: list[uuid.UUID] = []
    for household, lead in result.all():
        if not await _abandoned(db, household, lead, now=now):
            if household.reap_warned_at is not None:
                returned.append(household.id)
            continue
        if household.reap_warned_at is None:
            due.append(_notice(WARNING, household, lead, now + grace))
        elif _aware(household.reap_warned_at) + grace <= now:
            due.append(_notice(REAP, household, lead, _aware(household.reap_warned_at) + grace))
    return due, returned


def _notice(kind: str, household: Household, lead: User, reap_on: datetime) -> Notice:
    return Notice(
        kind=kind,
        household_id=household.id,
        household_name=household.name,
        user_id=lead.id,
        to=lead.email,
        reap_on=reap_on,
    )


async def run(db: AsyncSession, *, now: datetime | None = None, dry_run: bool = False) -> list[Notice]:
    """Warn, reap, and clear the marks of anybody who came back. Returns what
    was actually done (or, dry, what would be).

    Every act is an event (`reaping.warned`, `household.reaped`,
    `reaping.cancelled`, `reaping.failed`), because a deletion nobody can find
    in the log afterwards is worse than one that never happened.
    """
    now = now or datetime.now(UTC)
    notices, returned = await review(db, now=now)
    if dry_run:
        return notices

    for household_id in returned:
        household = await db.get(Household, household_id)
        if household is not None:
            household.reap_warned_at = None
            log_event("reaping.cancelled", household_id=household_id)
    await db.commit()

    done: list[Notice] = []
    warnings = [notice for notice in notices if notice.kind == WARNING]
    if warnings and not get_settings().email_configured:
        # Not an error, and not a reason to reap without warning either: with
        # no relay nobody is warned, so nobody is reaped.
        log_event("reaping.skipped", outcome="no_smtp", count=len(warnings))
        warnings = []
    for notice in warnings:
        subject, body = _message(notice)
        try:
            await send_email(notice.to, subject, body, purpose="reaping", household_id=notice.household_id)
        except EmailSendFailed as exc:
            # The class, never the text: a relay's refusal quotes the address.
            reason = type(exc.__cause__ or exc).__name__
            log_event("reaping.failed", household_id=notice.household_id, reason=reason)
            continue
        household = await db.get(Household, notice.household_id)
        if household is not None:
            household.reap_warned_at = now
            await db.commit()
        done.append(notice)
        log_event("reaping.warned", household_id=notice.household_id)

    for notice in (notice for notice in notices if notice.kind == REAP):
        user = await db.get(User, notice.user_id)
        if user is None or user.household_id != notice.household_id:
            continue
        await delete_user(db, user)
        await db.commit()
        done.append(notice)
        log_event("household.reaped", household_id=notice.household_id, user_id=notice.user_id)
    return done


def _message(notice: Notice) -> tuple[str, str]:
    """The one warning. It says what will go, when, and the single thing that
    stops it, and nothing else: there is nothing in the household to tell them
    about, which is the whole reason it is being reaped."""
    when = notice.reap_on.strftime("%-d %B %Y")
    return (
        f"Your Meals account will be deleted on {when}",
        f"Hello,\n\n"
        f"You made a Meals account with this address, with a household called {notice.household_name}, "
        f"and it has not been used since. There is nothing in it: no recipes, plans or lists.\n\n"
        f"To keep the server tidy, accounts like this are deleted. Yours will be deleted on {when}, "
        f"along with the empty household.\n\n"
        f"If you want to keep it, just sign in before then and nothing will happen. If you don't, "
        f"there is nothing to do: you can sign up again at any time.\n",
    )
