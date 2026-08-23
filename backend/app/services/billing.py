"""The billing webhook: a payment becoming an entitlement, exactly once.

Issue #99, planning/08-freemium.md §2. Two things shape every line of it.

**It ships inert.** With `BILLING_PROCESSOR` unset the route does not exist —
404, the same posture `/metrics` has without a token. A self-hosted instance has
no billing and must not be able to acquire one by accident, and "off" here means
"not reachable", not "reachable and refuses".

**Silent failure is how you give away a year for free.** So every request ends
in exactly one recorded outcome, on a counter as well as in the log, and the
alert lives on that counter rather than on somebody reading logs. The one
failure mode this module refuses to have is the quiet one.

Which processor, and why both are here
--------------------------------------

§7 requires a **merchant of record** rather than raw Stripe: EU B2C
digital-services VAT applies from the first sale regardless of the UK threshold,
and Paddle and Lemon Squeezy both handle it for roughly 5%, which is cheaper
than the problem. Which of the two is a commercial decision that has not been
made, so both adapters are here and `BILLING_PROCESSOR` picks one. That costs
about forty lines and removes the need to guess; the alternative was writing
this against one of them and rewriting it if the other won.

Both were read from the live documentation on 2026-08-22:

- **Paddle** signs `"{ts}:{raw body}"` with HMAC-SHA256, hex, and sends it as
  `Paddle-Signature: ts=<unix>;h1=<hex>`. The payload carries `event_id`,
  `event_type` and `data`.
- **Lemon Squeezy** signs the raw body with HMAC-SHA256, hex, and sends it as
  `X-Signature`, with the event name in `X-Event-Name` and again in
  `meta.event_name`. It sends **no event id**, so the ledger key is a digest of
  the body — an identical retry hashes identically, which is exactly what the
  key is for.

The linkage to a household is `custom_data.household_id`, set on the checkout.
Both processors pass custom data through to the webhook; Paddle puts it on
`data`, Lemon Squeezy on `meta`.
"""

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import limits, metrics
from app.config import get_settings
from app.models import BillingEvent, Household
from app.observability import log_event
from app.services import entitlements

#: Sentinel so `_record` can tell "no household" from "not specified".
_UNSET: Any = object()

PADDLE = "paddle"
LEMONSQUEEZY = "lemonsqueezy"
PROCESSORS = (PADDLE, LEMONSQUEEZY)

#: What this server made of a webhook. Every request ends as exactly one of
#: these, and every one of them is counted.
GRANTED = "granted"  # a payment became (or renewed) an entitlement
REVOKED = "revoked"  # the subscription ended and the entitlement went with it
IGNORED = "ignored"  # a real event this server has no opinion about
DUPLICATE = "duplicate"  # already in the ledger; deliberately not re-applied
ORPHAN = "orphan"  # verified and understood, but names no household here
REFUSED = "refused"  # understood, but applying it would lose something


class BillingError(Exception):
    """A request that cannot be trusted or cannot be read.

    Carries the status the endpoint should answer with, because the difference
    matters to the sender: 401 says "your secret and mine disagree", 400 says
    "I could not read this", and both are worth telling a processor rather than
    swallowing.
    """

    def __init__(self, detail: str, *, status_code: int, outcome: str) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.outcome = outcome


@dataclass(frozen=True)
class Incoming:
    """One webhook, normalised. `action` is this server's vocabulary, not the
    processor's, so `apply` never has to know which one sent it."""

    processor: str
    event_id: str
    event_type: str  # verbatim, as the processor named it
    action: str  # grant | revoke | ignore
    household_id: uuid.UUID | None
    renews_at: datetime | None


# --------------------------------------------------------------- verification


def verify(raw_body: bytes, headers: dict[str, str], *, now: datetime | None = None) -> None:
    """Refuse anything not signed with this deployment's secret.

    The signature is the whole of the authentication here: there is no bearer
    token, because the sender is a machine that has never heard of this app's
    accounts. Both comparisons are constant-time.
    """
    settings = get_settings()
    secret = (settings.billing_webhook_secret or "").encode()
    processor = settings.billing_processor
    lowered = {name.lower(): value for name, value in headers.items()}

    if processor == PADDLE:
        header = lowered.get("paddle-signature", "")
        parts = dict(part.split("=", 1) for part in header.split(";") if "=" in part)
        timestamp, provided = parts.get("ts", ""), parts.get("h1", "")
        if not timestamp or not provided:
            raise BillingError("missing or malformed Paddle-Signature header", status_code=401, outcome="unsigned")
        expected = hmac.new(secret, f"{timestamp}:".encode() + raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, provided):
            raise BillingError("signature does not match", status_code=401, outcome="bad_signature")
        _check_freshness(timestamp, now=now)
    else:
        provided = lowered.get("x-signature", "")
        expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
        if not provided or not hmac.compare_digest(expected, provided):
            raise BillingError("signature does not match", status_code=401, outcome="bad_signature")


def _check_freshness(timestamp: str, *, now: datetime | None) -> None:
    """Reject a signature old enough to be a replay.

    The ledger already makes a replay harmless, so this is defence in depth and
    the tolerance is generous: Paddle's SDKs default to five seconds, which
    turns one slow hop into a lost payment.
    """
    try:
        signed_at = datetime.fromtimestamp(int(timestamp), tz=UTC)
    except (ValueError, OSError) as exc:
        raise BillingError(
            "Paddle-Signature carries no readable timestamp", status_code=401, outcome="unsigned"
        ) from exc
    age = abs(((now or datetime.now(UTC)) - signed_at).total_seconds())
    tolerance = get_settings().billing_signature_tolerance_seconds
    if age > tolerance:
        raise BillingError(
            f"signature is {int(age)}s old, past the {tolerance}s tolerance", status_code=401, outcome="stale"
        )


# ------------------------------------------------------------------- parsing

#: Processor event names this server acts on. Everything else is a real event it
#: has no opinion about, which is recorded as `ignored` rather than dropped —
#: "we saw it and did nothing" and "we never got it" are different problems.
_PADDLE_ACTIONS = {
    "subscription.created": "grant",
    "subscription.updated": "grant",
    "subscription.activated": "grant",
    "transaction.completed": "grant",
    "subscription.canceled": "revoke",
    "subscription.cancelled": "revoke",  # spelling insurance; costs nothing
}
_LEMONSQUEEZY_ACTIONS = {
    "subscription_created": "grant",
    "subscription_updated": "grant",
    "subscription_payment_success": "grant",
    "subscription_resumed": "grant",
    # `cancelled` starts a grace period that runs to the paid-through date, so
    # it is deliberately NOT a revoke: the entitlement already expires on its
    # own, and cutting it short would take away days somebody paid for.
    "subscription_expired": "revoke",
}


def parse(raw_body: bytes, headers: dict[str, str], payload: dict) -> Incoming:
    processor = get_settings().billing_processor
    lowered = {name.lower(): value for name, value in headers.items()}
    if processor == PADDLE:
        event_type = str(payload.get("event_type") or "")
        event_id = str(payload.get("event_id") or "")
        data = payload.get("data") or {}
        custom = (data.get("custom_data") or {}) if isinstance(data, dict) else {}
        renews_at = _timestamp((data.get("current_billing_period") or {}).get("ends_at"))
        actions = _PADDLE_ACTIONS
    else:
        meta = payload.get("meta") or {}
        event_type = str(lowered.get("x-event-name") or meta.get("event_name") or "")
        # No event id is sent, so the body is its own identity: a retry of the
        # same event is byte-identical and lands on the same ledger row.
        event_id = hashlib.sha256(raw_body).hexdigest()
        custom = meta.get("custom_data") or {}
        attributes = (payload.get("data") or {}).get("attributes") or {}
        renews_at = _timestamp(attributes.get("renews_at") or attributes.get("ends_at"))
        actions = _LEMONSQUEEZY_ACTIONS

    if not event_type:
        raise BillingError("could not tell what event this is", status_code=400, outcome="unreadable")
    if not event_id:
        raise BillingError("event carries no id to deduplicate on", status_code=400, outcome="unreadable")

    return Incoming(
        processor=processor,
        event_id=event_id,
        event_type=event_type,
        action=actions.get(event_type, "ignore"),
        household_id=_household_id(custom),
        renews_at=renews_at,
    )


def _household_id(custom: object) -> uuid.UUID | None:
    if not isinstance(custom, dict):
        return None
    try:
        return uuid.UUID(str(custom.get("household_id")))
    except (ValueError, TypeError):
        return None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ------------------------------------------------------------------ applying


async def handle(db: AsyncSession, raw_body: bytes, headers: dict[str, str], payload: dict) -> str:
    """Verify, deduplicate, apply, and record. Returns the outcome.

    Every path through here writes exactly one ledger row and counts exactly one
    outcome, including the paths that decide to do nothing.
    """
    verify(raw_body, headers)
    event = parse(raw_body, headers, payload)

    existing = await db.execute(
        select(BillingEvent).where(BillingEvent.processor == event.processor, BillingEvent.event_id == event.event_id)
    )
    if existing.scalar_one_or_none() is not None:
        # Not an error, and deliberately not re-applied: processors retry on any
        # non-2xx, and a blip between granting and answering 200 is exactly the
        # case this exists for. **No second row** — the ledger row that detected
        # this duplicate is the one whose uniqueness would refuse it.
        return _observe(event, DUPLICATE, detail=None, household_id=event.household_id)

    if event.action == "ignore":
        return await _record(db, event, IGNORED, detail=None)
    if event.household_id is None:
        return await _record(
            db,
            event,
            ORPHAN,
            detail="no household_id in the checkout's custom_data, so nobody could be credited",
            household_id=None,
        )
    household = await db.get(Household, event.household_id)
    if household is None:
        # The id is recorded in the text rather than the column: it names no row
        # here, and the column is a foreign key.
        return await _record(
            db,
            event,
            ORPHAN,
            detail=f"household {event.household_id} is not on this server",
            household_id=None,
        )

    try:
        if event.action == "grant":
            await entitlements.grant(
                db,
                household,
                tier=limits.PAID,
                until=event.renews_at,
                source=event.processor,
                note=f"{event.event_type} via {event.processor}",
            )
            return await _record(db, event, GRANTED, detail=None)
        await entitlements.revoke(db, household, note=f"{event.event_type} via {event.processor}")
        return await _record(db, event, REVOKED, detail=None)
    except entitlements.EntitlementError as exc:
        # Deterministic: retrying will fail identically, so the endpoint answers
        # 200 to stop the retries and the alert is what gets a human involved.
        # This is the one branch where somebody has paid and not been credited.
        return await _record(db, event, REFUSED, detail=str(exc)[:300])


async def _record(
    db: AsyncSession,
    event: Incoming,
    outcome: str,
    *,
    detail: str | None,
    household_id: uuid.UUID | None = _UNSET,
) -> str:
    """Write the ledger row, then count and log it."""
    on_household = event.household_id if household_id is _UNSET else household_id
    db.add(
        BillingEvent(
            processor=event.processor,
            event_id=event.event_id,
            event_type=event.event_type,
            outcome=outcome,
            household_id=on_household,
            detail=detail,
        )
    )
    await db.commit()
    return _observe(event, outcome, detail=detail, household_id=on_household)


def _observe(event: Incoming, outcome: str, *, detail: str | None, household_id: uuid.UUID | None) -> str:
    """The counter and the log line, for an outcome that has been decided.

    Split out because the duplicate path must not write a second ledger row but
    must still be counted: a retry storm that nobody can see is its own problem.
    """
    count(outcome)
    log_event(
        "billing.webhook",
        outcome=outcome,
        processor=event.processor,
        event_type=event.event_type,
        household_id=household_id,
        detail=detail,
    )
    return outcome


def count(outcome: str) -> None:
    """The counter the alert watches. Separate from `_record` so the paths that
    never reach the ledger — a bad signature, an unreadable body — are counted
    too. Those are the ones that would otherwise be silent."""
    metrics.count_billing_webhook(outcome)
