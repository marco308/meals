"""Two emails about an expiring entitlement, and never a third.

planning/08-freemium.md §5: "one email before expiry and one after, through the
SMTP that password reset already uses". That is the whole feature, and its
smallness is the point — dunning that nags is dunning people filter.

- **Before**, `DUNNING_WARN_DAYS` ahead of the date, so there is time to act.
- **After**, on the day it expires rather than at the end of the grace period.
  At expiry the news is useful ("you have 14 days before limits re-apply"); at
  the end of grace it is only a complaint.

Both are one-shot, marked on the household, and both marks are cleared whenever
the expiry moves (`entitlements._reset_dunning`), so next year gets its own pair.
A relay failure leaves the mark unset, so the next run tries again rather than
silently swallowing the only warning somebody was going to get.

Nothing here runs on a self-hosted instance. There is no scheduler in the app:
this is `python -m app.dunning` from cron, and with no SMTP configured, or no
household carrying an expiry, it does nothing and says so.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Household, User
from app.observability import log_event
from app.services.mailer import EmailSendFailed, send_email

WARNING = "warning"
LAPSE = "lapse"


@dataclass(frozen=True)
class Notice:
    """One email that is due, or was sent."""

    kind: str
    household_id: object
    household_name: str
    to: str
    paid_until: datetime


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def due(db: AsyncSession, *, now: datetime | None = None) -> list[Notice]:
    """Every email that should go out at this moment, and nothing else.

    Households with no expiry are never in here, which is every self-hosted one
    and every standing comp.
    """
    now = now or datetime.now(UTC)
    warn_from = now + timedelta(days=get_settings().dunning_warn_days)
    result = await db.execute(
        select(Household, User)
        .join(User, Household.lead_user_id == User.id)
        .where(Household.paid_until.is_not(None))
        .order_by(Household.paid_until)
    )
    notices = []
    for household, lead in result:
        expires = _aware(household.paid_until)
        if expires <= now:
            if household.lapse_notified_at is None:
                notices.append(_notice(LAPSE, household, lead, expires))
        elif expires <= warn_from and household.expiry_warned_at is None:
            notices.append(_notice(WARNING, household, lead, expires))
    return notices


def _notice(kind: str, household: Household, lead: User, expires: datetime) -> Notice:
    return Notice(
        kind=kind, household_id=household.id, household_name=household.name, to=lead.email, paid_until=expires
    )


async def run(db: AsyncSession, *, now: datetime | None = None, dry_run: bool = False) -> list[Notice]:
    """Send what is due and mark it. Returns what actually went out.

    Silence is the failure mode worth guarding against here, so every send and
    every failure is an event: `dunning.sent` with the kind, and
    `dunning.failed` with the reason. A failure marks nothing, so the next run
    retries rather than dropping somebody's only notice.
    """
    settings = get_settings()
    notices = await due(db, now=now)
    if dry_run or not notices:
        return notices
    if not settings.email_configured:
        # Not an error: a server with no mail relay is the normal case, and
        # refusing loudly every night would be noise rather than news.
        log_event("dunning.skipped", outcome="no_smtp", count=len(notices))
        return []

    sent = []
    for notice in notices:
        subject, body = _message(notice, grace_days=settings.entitlement_grace_days)
        try:
            await send_email(notice.to, subject, body)
        except EmailSendFailed as exc:
            log_event("dunning.failed", outcome=notice.kind, household_id=notice.household_id, reason=str(exc)[:200])
            continue
        household = await db.get(Household, notice.household_id)
        if household is not None:
            stamp = datetime.now(UTC)
            if notice.kind == WARNING:
                household.expiry_warned_at = stamp
            else:
                household.lapse_notified_at = stamp
        sent.append(notice)
        log_event("dunning.sent", outcome=notice.kind, household_id=notice.household_id)
    await db.commit()
    return sent


def _message(notice: Notice, *, grace_days: int) -> tuple[str, str]:
    """The two emails. Plain, factual, and pointing at the one thing that is
    true whatever somebody decides: their data is theirs and it is one request
    away (§1). Neither says anything the terms page does not."""
    when = notice.paid_until.strftime("%-d %B %Y")
    if notice.kind == WARNING:
        return (
            f"Your Meals household expires on {when}",
            f"Hello,\n\n"
            f"The hosted year for your household, {notice.household_name}, ends on {when}.\n\n"
            f"If you renew, nothing changes. If you do not, nothing is deleted: your recipes, plans, "
            f"lists and cooked history all stay exactly where they are and stay readable, and the "
            f"shopping list keeps working. After a grace period of {grace_days} days the free tier's "
            f"limits re-apply, which only stops the household growing — no new recipes past the free "
            f"allowance, and no new invites.\n\n"
            f"You can take a full copy of everything at any time, free, with one request to "
            f"/household/export. The terms are at /terms on the same server.\n",
        )
    return (
        "Your Meals household has reached the end of its year",
        f"Hello,\n\n"
        f"The hosted year for your household, {notice.household_name}, ended on {when}.\n\n"
        f"Nothing has been deleted and nothing has been switched off. You have {grace_days} days before "
        f"the free tier's limits re-apply, and even then everything already there stays readable, the "
        f"plan stays usable and the shopping list keeps working in full. What stops is growth: no new "
        f"recipes past the free allowance, and no new invites.\n\n"
        f"Renewing puts it back exactly as it was. If you would rather not, that is fine too, and your "
        f"data is still yours: one request to /household/export returns all of it, free. The terms are "
        f"at /terms on the same server.\n",
    )
