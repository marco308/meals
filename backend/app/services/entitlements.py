"""What a household is entitled to, and the only place that changes it.

Issue #99, planning/08-freemium.md §2 and §5. `households.tier` says which set
of numbers in `app/limits.py` applies; the columns beside it say until when,
where it came from, and what was agreed. Everything else *reads* that through
one function, `limits.effective_tier`, which is what makes this a source of
truth rather than a second opinion.

Three rules the module exists to hold:

- **Lapsing is derived, never written back.** A household past its expiry and
  its grace reads as `free`; nothing rewrites `tier`, nothing is deleted, and
  nothing becomes unreadable (§5). Keeping the stored tier is also what makes a
  renewal a one-line change rather than a reconstruction.
- **The founding price is set once.** §6 promises "founding price for life",
  and a promise stored on the row is only worth something if the code refuses
  to quietly overwrite it. `grant` writes the price snapshot only when there
  isn't one; changing it is a separate, deliberate act.
- **A self-hosted instance never touches any of this.** Every column defaults to
  null, a `paid_until` of null never expires, and nothing here runs unless
  somebody runs it.

The write side is deliberately a library rather than an endpoint. Comping
somebody is an operator action on a box, like `app/provision.py`, and #99 says
the ops surface comes before the money: a spreadsheet is not a source of truth.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import limits
from app.config import get_settings
from app.models import Household, User
from app.observability import log_event

#: `entitlement_source` for anything given rather than bought. A processor's
#: name goes in the same column when there is one to name.
COMP = "comp"

#: What a household's entitlement is doing right now. Not stored: derived from
#: `paid_until` and the clock, so it can never disagree with what the limits
#: are actually doing.
PERMANENT = "permanent"  # no expiry: every self-hosted household, and a standing comp
PAID = "paid"  # in date
GRACE = "grace"  # past expiry, caps not yet re-applied (§5's 14 days)
LAPSED = "lapsed"  # past expiry and past grace: the free tier's caps apply
STATES = (PERMANENT, PAID, GRACE, LAPSED)


class EntitlementError(Exception):
    """A change that would lose something worth keeping. Carries a sentence the
    operator can act on, in the same spirit as the API's 4xx bodies."""


@dataclass(frozen=True)
class Entitlement:
    """One household's entitlement, flattened for reporting."""

    household_id: uuid.UUID
    household_name: str
    stored_tier: str
    effective_tier: str
    state: str
    paid_until: datetime | None
    grace_ends_at: datetime | None
    source: str | None
    note: str | None
    price_pence: int | None
    price_currency: str | None
    lead_email: str | None


def _aware(value: datetime) -> datetime:
    # SQLite round-trips datetimes naive; stored values are UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def grace_ends_at(household: Household) -> datetime | None:
    if household.paid_until is None:
        return None
    return _aware(household.paid_until) + timedelta(days=get_settings().entitlement_grace_days)


def state(household: Household, *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if household.paid_until is None:
        return PERMANENT
    if now < _aware(household.paid_until):
        return PAID
    return GRACE if now < grace_ends_at(household) else LAPSED


def describe(household: Household, *, lead_email: str | None = None, now: datetime | None = None) -> Entitlement:
    return Entitlement(
        household_id=household.id,
        household_name=household.name,
        stored_tier=household.tier,
        effective_tier=limits.effective_tier(household, now=now),
        state=state(household, now=now),
        paid_until=_aware(household.paid_until) if household.paid_until else None,
        grace_ends_at=grace_ends_at(household),
        source=household.entitlement_source,
        note=household.entitlement_note,
        price_pence=household.price_pence,
        price_currency=household.price_currency,
        lead_email=lead_email,
    )


# ---------------------------------------------------------------- the writes


async def grant(
    db: AsyncSession,
    household: Household,
    *,
    tier: str,
    until: datetime | None,
    source: str,
    note: str | None = None,
    price_pence: int | None = None,
    price_currency: str | None = None,
) -> Entitlement:
    """Put a household on a tier until a date, or forever with `until=None`.

    The price snapshot is written **only if there isn't one**: §6's founding
    price is for life, and a renewal that quietly repriced somebody would make
    that sentence untrue while looking like bookkeeping. Repricing on purpose
    means clearing it first, which is a deliberate act with its own line in the
    log.
    """
    if tier not in limits.TIERS:
        raise EntitlementError(f"{tier!r} is not a tier; choose one of {', '.join(limits.TIERS)}")
    if until is not None and _aware(until) <= datetime.now(UTC):
        raise EntitlementError(
            f"{until.date()} is not in the future, so this would grant an entitlement that has already "
            "lapsed. Use a later date, or 'revoke' if that is what you meant."
        )
    if price_pence is not None and household.price_pence is not None and household.price_pence != price_pence:
        raise EntitlementError(
            f"this household already has a price of {household.price_pence}p and that is theirs for life "
            "(planning/08-freemium.md §6). Extending keeps it; if you really mean to change it, clear "
            "the price snapshot first and say why in the note."
        )

    household.tier = tier
    household.paid_until = until
    household.entitlement_source = source
    if note is not None:
        household.entitlement_note = note
    if price_pence is not None and household.price_pence is None:
        household.price_pence = price_pence
        household.price_currency = price_currency or "GBP"
        household.price_set_at = datetime.now(UTC)
    _reset_dunning(household)

    await db.commit()
    log_event(
        "entitlement.granted",
        outcome=tier,
        household_id=household.id,
        source=source,
        until=until.isoformat() if until else None,
    )
    return describe(household)


async def extend(
    db: AsyncSession,
    household: Household,
    *,
    days: int | None = None,
    until: datetime | None = None,
) -> Entitlement:
    """Push the expiry out, from wherever it actually is.

    A household still in date is extended from its own expiry, so nothing is
    lost by renewing early. One already lapsed is extended from now, so nobody
    pays for the weeks they were locked out of growing.
    """
    if (days is None) == (until is None):
        raise EntitlementError("give either days or until, not both and not neither")
    now = datetime.now(UTC)
    if days is not None:
        if days <= 0:
            raise EntitlementError(f"days must be positive; {days} would shorten the entitlement")
        base = max(now, _aware(household.paid_until)) if household.paid_until else now
        until = base + timedelta(days=days)
    elif _aware(until) <= now:
        raise EntitlementError(f"{until.date()} is not in the future")

    household.paid_until = until
    _reset_dunning(household)
    await db.commit()
    log_event("entitlement.extended", household_id=household.id, until=until.isoformat())
    return describe(household)


async def revoke(db: AsyncSession, household: Household, *, note: str | None = None) -> Entitlement:
    """Back to the free tier, now.

    Nothing is deleted and nothing becomes unreadable — this only stops the
    household growing past the free allowance (§5). The price snapshot is kept
    on purpose: if they come back, the founding price they were promised is
    still the one on their row.
    """
    household.tier = limits.FREE
    household.paid_until = None
    household.entitlement_source = None
    if note is not None:
        household.entitlement_note = note
    _reset_dunning(household)
    await db.commit()
    log_event("entitlement.revoked", household_id=household.id)
    return describe(household)


def _reset_dunning(household: Household) -> None:
    """Every change to the expiry starts dunning over. Without this a renewed
    household would never be warned again, because it was warned once."""
    household.expiry_warned_at = None
    household.lapse_notified_at = None


# ---------------------------------------------------------------- the reading


async def listing(db: AsyncSession, *, everyone: bool = False, now: datetime | None = None) -> list[Entitlement]:
    """Who is on what, ordered by what needs attention first.

    By default only households with an entitlement worth reporting: anything
    that was granted, and anything not on the deployment's default tier. On a
    self-hosted server that is nobody, and the answer is an empty list rather
    than every household you have.
    """
    households = list((await db.execute(select(Household).order_by(Household.created_at, Household.id))).scalars())
    leads = await _lead_emails(db, households)
    default = get_settings().default_household_tier
    rows = [
        describe(household, lead_email=leads.get(household.lead_user_id), now=now)
        for household in households
        if everyone or household.entitlement_source is not None or household.tier != default
    ]
    # Lapsed first, then grace, then the ones with a date, then the rest: the
    # top of the list is what somebody running this actually needs to see.
    order = {LAPSED: 0, GRACE: 1, PAID: 2, PERMANENT: 3}
    return sorted(rows, key=lambda row: (order[row.state], row.paid_until or datetime.max.replace(tzinfo=UTC)))


async def _lead_emails(db: AsyncSession, households: list[Household]) -> dict[uuid.UUID, str]:
    lead_ids = [household.lead_user_id for household in households if household.lead_user_id]
    if not lead_ids:
        return {}
    users = (await db.execute(select(User).where(User.id.in_(lead_ids)))).scalars().all()
    return {user.id: user.email for user in users}
