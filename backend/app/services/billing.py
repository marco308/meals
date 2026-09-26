"""Both ends of a payment: opening a checkout, and a payment becoming an
entitlement exactly once.

Issues #99 and #121, planning/08-freemium.md §2 and §7. Two things shape every
line of it.

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

§7 requires a **merchant of record**: EU B2C digital-services VAT applies from
the first sale regardless of the UK threshold, so somebody has to be the legal
seller and file in the customer's country. That requirement is what matters, and
it is not the same as "not Stripe" — **Stripe Managed Payments** (2026) is
Stripe acting as merchant of record, which means the account this project
already has can do the job. `BILLING_PROCESSOR` picks between three adapters
rather than committing the code to one.

All three were read from the live documentation on 2026-08-22 and 2026-08-23:

- **Paddle** signs `"{ts}:{raw body}"` with HMAC-SHA256, hex, and sends it as
  `Paddle-Signature: ts=<unix>;h1=<hex>`. The payload carries `event_id`,
  `event_type` and `data`.
- **Lemon Squeezy** signs the raw body with HMAC-SHA256, hex, and sends it as
  `X-Signature`, with the event name in `X-Event-Name` and again in
  `meta.event_name`. It sends **no event id**, so the ledger key is a digest of
  the body — an identical retry hashes identically, which is exactly what the
  key is for.
- **Stripe** signs `"{ts}.{raw body}"` with HMAC-SHA256, hex, and sends it as
  `Stripe-Signature: t=…,v1=…`. Two details its docs are explicit about and a
  hand-rolled verifier gets wrong: there can be **several `v1` signatures** at
  once, because rolling an endpoint secret keeps the old one live for up to 24
  hours, so any match counts; and every other scheme must be **ignored**,
  because `v0` is a deliberately fake signature sent alongside test events and
  accepting it would be a downgrade attack.

The linkage to a household is `household_id` in the checkout's custom data:
Paddle puts it on `data.custom_data`, Lemon Squeezy on `meta.custom_data`, and
Stripe on the object's `metadata`. For Stripe subscriptions that means setting
`subscription_data.metadata.household_id` on the Checkout Session — metadata on
the *session* does not reach the subscription, and a payment nobody can match to
a household is recorded as an orphan rather than guessed at. The member who
opened the checkout rides beside it as `user_id`, and becomes the payer.

What an event may change
------------------------

A verified event is not yet a payment. Three things decide whether it moves an
entitlement, and each was a way to give away a year or take one that was paid:

- **Its status.** A snapshot of a subscription grants only when its status says
  the money arrived; Stripe moves the period on before it charges. A declined
  card holds the household to the moment it was declined, and the grace period
  runs from there.
- **Its subscription and its time.** An entitlement follows the one
  subscription it was paid through, and only that subscription's events, newer
  than the last one applied, can move it.
- **Whose it is.** A comp in force is the operator's, and a processor never
  changes it. The subscription belongs to its payer, who alone opens the portal.
"""

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import limits, metrics
from app.config import get_settings
from app.models import BillingEvent, Household, User
from app.observability import log_event
from app.services import entitlements

#: Sentinel so `_record` can tell "no household" from "not specified".
_UNSET: Any = object()

PADDLE = "paddle"
LEMONSQUEEZY = "lemonsqueezy"
STRIPE = "stripe"
PROCESSORS = (PADDLE, LEMONSQUEEZY, STRIPE)

#: What this server made of a webhook. Every request ends as exactly one of
#: these, and every one of them is counted.
GRANTED = "granted"  # a payment became (or renewed) an entitlement
UNPAID = "unpaid"  # a renewal the processor could not collect: held to what was paid
REVOKED = "revoked"  # the subscription ended, and the entitlement runs out with grace
IGNORED = "ignored"  # a real event this server has no opinion about, or a stale one
DUPLICATE = "duplicate"  # already in the ledger; deliberately not re-applied
ORPHAN = "orphan"  # verified and understood, but names no household here
REFUSED = "refused"  # understood, but applying it would lose something

#: What an event asks of an entitlement, in this server's words rather than the
#: processor's, so nothing below `parse` needs to know who sent it.
GRANT = "grant"  # paid for: credit the household through the period end
HOLD = "hold"  # in arrears: nothing is granted, and the expiry comes no later than now
REVOKE = "revoke"  # over at the processor: the expiry comes no later than now
IGNORE = "ignore"  # nothing to do with an entitlement


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
    action: str  # GRANT | HOLD | REVOKE | IGNORE
    household_id: uuid.UUID | None
    renews_at: datetime | None
    # Two things the event already carries and #128/#129 found us throwing away.
    #
    # `price_pence` is the *list* price, before any tax the merchant of record
    # adds on top: §2's "founding price for life" is a promise about what this
    # project charges, not about what a given country's VAT made the total.
    #
    # `customer_id` is who this household is at the processor, which is what
    # turns "Manage billing" from a login page into their own portal.
    #
    # Both are optional in a way `renews_at` is not: a grant with no expiry
    # would never lapse and is refused, while a grant with no price recorded is
    # merely a snapshot we could not take.
    price_pence: int | None = None
    price_currency: str | None = None
    customer_id: str | None = None
    # What scopes an event to the entitlement it may change. Without them any
    # subscription's webhook could grant or end any household's, and a retry
    # from before a cancellation could grant the year back.
    #
    # `subscription_id` is the processor's id for the subscription itself.
    # `occurred_at` is the processor's time for the event, which a retry keeps.
    subscription_id: str | None = None
    occurred_at: datetime | None = None
    # The subscription's status as the processor wrote it, and whether it is
    # one this server knows. A grant needs positive evidence that the money
    # arrived, so an unknown status on a snapshot is refused, not guessed at.
    status: str | None = None
    status_known: bool = True
    # Whether the processor will charge again at the period end: false once it
    # has been cancelled to end there, and what lets the payer walk away.
    renews: bool = True
    # The member who opened the checkout, carried beside the household id.
    payer_id: uuid.UUID | None = None


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
        if not _matches(expected, provided):
            raise BillingError("signature does not match", status_code=401, outcome="bad_signature")
        _check_freshness(timestamp, now=now)
    elif processor == STRIPE:
        header = lowered.get("stripe-signature", "")
        timestamp = ""
        offered = []
        for part in header.split(","):
            prefix, _, value = part.strip().partition("=")
            if prefix == "t":
                timestamp = value
            elif prefix == "v1":
                # Only v1. Every other scheme is ignored on purpose: Stripe
                # sends a deliberately fake `v0` beside test events, and
                # accepting one would be a downgrade attack.
                offered.append(value)
        if not timestamp or not offered:
            raise BillingError("missing or malformed Stripe-Signature header", status_code=401, outcome="unsigned")
        expected = hmac.new(secret, f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
        # Any match counts: rolling an endpoint secret leaves the old one live
        # for up to 24 hours, and Stripe signs once per active secret.
        if not any(_matches(expected, candidate) for candidate in offered):
            raise BillingError("signature does not match", status_code=401, outcome="bad_signature")
        _check_freshness(timestamp, now=now)
    else:
        provided = lowered.get("x-signature", "")
        expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
        if not provided or not _matches(expected, provided):
            raise BillingError("signature does not match", status_code=401, outcome="bad_signature")


def _matches(expected: str, offered: str) -> bool:
    """Constant-time, and over bytes. `compare_digest` raises TypeError on a
    str holding anything outside ASCII, and a header can carry any byte, so a
    signature with one in it was an unhandled 500. That is uncounted, and it is
    retried, where a forgery should be a counted 401."""
    return hmac.compare_digest(expected.encode(), offered.encode())


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
#:
#: `_SNAPSHOT` is an event carrying the whole subscription, and it is the
#: subscription's *status* that decides what it means (`_STATUS_ACTIONS`): the
#: name alone once granted a year to a renewal whose card had been declined.
_SNAPSHOT = "snapshot"

_PADDLE_ACTIONS = {
    "subscription.created": _SNAPSHOT,
    "subscription.updated": _SNAPSHOT,
    "subscription.activated": _SNAPSHOT,
    "subscription.trialing": _SNAPSHOT,
    "subscription.past_due": _SNAPSHOT,
    "subscription.paused": _SNAPSHOT,
    "subscription.resumed": _SNAPSHOT,
    "subscription.canceled": REVOKE,
    "subscription.cancelled": REVOKE,  # spelling insurance; costs nothing
    # `transaction.completed` is deliberately absent, for the reason Stripe's
    # invoice events are: the subscription's own update carries the paid-through
    # date. A transaction calls its period `billing_period`, not
    # `current_billing_period`, so while it was mapped every payment it reported
    # was refused for having no period end, and fired the alert for it.
}
_LEMONSQUEEZY_ACTIONS = {
    "subscription_created": _SNAPSHOT,
    "subscription_updated": _SNAPSHOT,
    "subscription_resumed": _SNAPSHOT,
    "subscription_paused": _SNAPSHOT,
    "subscription_unpaused": _SNAPSHOT,
    # `cancelled` starts a grace period that runs to the paid-through date, so
    # it is deliberately NOT a revoke: the entitlement already expires on its
    # own, and cutting it short would take away days somebody paid for. The
    # `subscription_updated` sent with it carries the same snapshot, and that is
    # how this server hears that it will not renew.
    "subscription_expired": REVOKE,
    # `subscription_payment_success` is absent too: it carries the invoice, not
    # the subscription, so it has no `renews_at` and was refused every time. The
    # `subscription_updated` after a renewal is what moves the date.
}


#: Stripe's subscription lifecycle. `updated` is the catch-all and carries the
#: paid-through date, with the status that says whether it was paid for.
#: Invoice events are deliberately absent: the subscription object is the source
#: of truth for "paid until when", and acting on both would be two writes for
#: one payment. `checkout.session.completed` is absent for a sharper reason —
#: it carries no billing period, and nothing here may grant without one.
_STRIPE_ACTIONS = {
    "customer.subscription.created": _SNAPSHOT,
    "customer.subscription.updated": _SNAPSHOT,
    "customer.subscription.paused": _SNAPSHOT,
    "customer.subscription.resumed": _SNAPSHOT,
    "customer.subscription.deleted": REVOKE,
}

#: Every status each processor documents for a subscription, by what it means
#: for the entitlement. Stripe advances `current_period_end` *before* it charges
#: a renewal, so a snapshot carrying next year's date proves nothing until its
#: status says the money arrived; a `past_due` one is a card that was declined.
#: A status missing from here, or none at all, is a shape this server has not
#: understood, and a snapshot carrying one is refused rather than guessed at.
_STATUS_ACTIONS = {
    STRIPE: {
        "active": GRANT,
        "trialing": GRANT,
        "past_due": HOLD,
        "unpaid": HOLD,
        "incomplete": HOLD,
        "paused": HOLD,
        "canceled": REVOKE,
        "incomplete_expired": REVOKE,
    },
    PADDLE: {
        "active": GRANT,
        "trialing": GRANT,
        "past_due": HOLD,
        "paused": HOLD,
        "canceled": REVOKE,
    },
    LEMONSQUEEZY: {
        "active": GRANT,
        "on_trial": GRANT,
        # Paid through `ends_at` and will not renew: what Stripe calls active
        # with `cancel_at_period_end`, and credited the same way.
        "cancelled": GRANT,
        "past_due": HOLD,
        "unpaid": HOLD,
        "paused": HOLD,
        "expired": REVOKE,
    },
}


def parse(raw_body: bytes, headers: dict[str, str], payload: dict) -> Incoming:
    processor = get_settings().billing_processor
    lowered = {name.lower(): value for name, value in headers.items()}
    price_pence = price_currency = customer_id = None
    if processor == PADDLE:
        event_type = str(payload.get("event_type") or "")
        event_id = str(payload.get("event_id") or "")
        data = _object(payload.get("data"))
        custom = data.get("custom_data") or {}
        renews_at = _timestamp((data.get("current_billing_period") or {}).get("ends_at"))
        # Paddle sends the amount as a string of minor units and the currency
        # beside it, on the price of each item.
        unit = ((data.get("items") or [{}])[0].get("price") or {}).get("unit_price") or {}
        price_pence, price_currency = _money(unit.get("amount"), unit.get("currency_code"))
        customer_id = _identifier(data.get("customer_id"))
        status = data.get("status")
        # A subscription event carries the subscription itself; a transaction
        # names the one it belongs to.
        subscription_id = _identifier(data.get("subscription_id") or data.get("id"))
        # A cancellation or a pause is scheduled for the period end rather than
        # made at once, and is what "will not renew" looks like here.
        renews = _object(data.get("scheduled_change")).get("action") not in ("cancel", "pause")
        occurred_at = _timestamp(payload.get("occurred_at"))
        actions = _PADDLE_ACTIONS
    elif processor == STRIPE:
        event_type = str(payload.get("type") or "")
        event_id = str(payload.get("id") or "")
        obj = _object(_object(payload.get("data")).get("object"))
        custom = obj.get("metadata") or {}
        renews_at = _stripe_period_end(obj)
        price_pence, price_currency = _stripe_amount(obj)
        # `customer` is an id, or the expanded object when somebody has asked
        # for it. Read both rather than assume which.
        customer = obj.get("customer")
        customer_id = _identifier(customer.get("id") if isinstance(customer, dict) else customer)
        status = obj.get("status")
        subscription_id = _identifier(
            obj.get("id") if obj.get("object", "subscription") == "subscription" else obj.get("subscription")
        )
        # Cancelling "at the end of the period" sets the first; newer API
        # versions can schedule the same thing as a date in the second.
        renews = not (obj.get("cancel_at_period_end") or obj.get("cancel_at"))
        occurred_at = _epoch(payload.get("created"))
        actions = _STRIPE_ACTIONS
    else:
        meta = _object(payload.get("meta"))
        # The name inside the signed body is the one believed. `X-Event-Name`
        # carries the same thing outside the signature, where anybody replaying
        # a captured body could change it, so it may only agree.
        event_type = str(meta.get("event_name") or "")
        claimed = lowered.get("x-event-name")
        if claimed and event_type and claimed != event_type:
            raise BillingError(
                f"X-Event-Name says {claimed[:80]!r} but the signed body says {event_type!r}, and only the "
                "signed one is believed",
                status_code=400,
                outcome="unreadable",
            )
        # No event id is sent, so the body is its own identity: a retry of the
        # same event is byte-identical and lands on the same ledger row.
        event_id = hashlib.sha256(raw_body).hexdigest()
        custom = meta.get("custom_data") or {}
        data = _object(payload.get("data"))
        attributes = _object(data.get("attributes"))
        status = attributes.get("status")
        # A cancelled subscription is paid through `ends_at`; `renews_at` is
        # when a live one would next charge.
        ending = bool(attributes.get("cancelled")) or status in ("cancelled", "expired")
        first, second = ("ends_at", "renews_at") if ending else ("renews_at", "ends_at")
        renews_at = _timestamp(attributes.get(first)) or _timestamp(attributes.get(second))
        customer_id = _identifier(attributes.get("customer_id"))
        subscription_id = _identifier(
            data.get("id")
            if data.get("type", "subscriptions") == "subscriptions"
            else attributes.get("subscription_id")
        )
        renews = not ending
        # It sends no event time, but every change to a subscription stamps
        # `updated_at`, which a retry of the same body repeats exactly.
        occurred_at = _timestamp(attributes.get("updated_at"))
        # No price: a Lemon Squeezy subscription payload names the variant it is
        # for, not what it costs, and inferring an amount from a variant id would
        # be a guess written into a column that promises not to change. The
        # snapshot stays null, which #128 is explicit is better than a guess.
        actions = _LEMONSQUEEZY_ACTIONS

    if not event_type:
        raise BillingError("could not tell what event this is", status_code=400, outcome="unreadable")
    if not event_id:
        raise BillingError("event carries no id to deduplicate on", status_code=400, outcome="unreadable")

    status = status if isinstance(status, str) else None
    action, status_known = actions.get(event_type, IGNORE), True
    if action == _SNAPSHOT:
        refined = _STATUS_ACTIONS[processor].get(status or "")
        # An unknown status stays a grant so that it is refused as one, after
        # the household is found: a payment nobody could credit is an orphan
        # whatever else is wrong with it.
        action, status_known = (refined, True) if refined else (GRANT, False)

    return Incoming(
        processor=processor,
        event_id=event_id,
        event_type=event_type,
        action=action,
        household_id=_uuid(custom, "household_id"),
        renews_at=renews_at,
        price_pence=price_pence,
        price_currency=price_currency,
        customer_id=customer_id,
        subscription_id=subscription_id,
        occurred_at=occurred_at,
        status=status,
        status_known=status_known,
        renews=renews,
        payer_id=_uuid(custom, "user_id"),
    )


def _stripe_amount(obj: dict) -> tuple[int | None, str | None]:
    """What one billing period of this subscription lists at.

    Read off the subscription's items rather than the invoice: the invoice total
    includes whatever tax the merchant of record added for that customer's
    country, and two households on the same founding price would snapshot
    different numbers. `plan.amount` is the older shape and says the same thing.
    """
    items = (obj.get("items") or {}).get("data") or []
    first = items[0] if items and isinstance(items[0], dict) else {}
    price = first.get("price") or first.get("plan") or obj.get("plan") or {}
    amount = price.get("unit_amount", price.get("amount"))
    return _money(amount, price.get("currency") or obj.get("currency"))


def _money(amount: object, currency: object) -> tuple[int | None, str | None]:
    """Minor units and a currency, or nothing. Paddle sends the amount as a
    string, Stripe as an int, and either may be absent."""
    try:
        pence = int(str(amount))
    except (TypeError, ValueError):
        return None, None
    if pence <= 0:
        # A zero or negative line is a discount, a trial or a shape we have not
        # understood. None of them is a founding price.
        return None, None
    code = str(currency).upper() if currency else None
    return pence, code if code and len(code) == 3 else None


def _identifier(value: object) -> str | None:
    """A processor's own id for something, as text. Lemon Squeezy sends integers
    where the others send strings, and the column stores what it is given."""
    if value is None or isinstance(value, bool | dict | list):
        return None
    text = str(value).strip()
    return text[:255] or None


def _stripe_period_end(obj: dict) -> datetime | None:
    """When the subscription is paid through.

    `current_period_end` sits on the subscription in older API versions and on
    each subscription *item* in newer ones, so both are read. The latest item
    end is the one that matters: it is the point after which nothing has been
    paid for.
    """
    ends = [obj.get("current_period_end")]
    for item in (obj.get("items") or {}).get("data") or []:
        if isinstance(item, dict):
            ends.append(item.get("current_period_end"))
    stamps = [value for value in ends if isinstance(value, int)]
    return datetime.fromtimestamp(max(stamps), tz=UTC) if stamps else None


def _uuid(custom: object, key: str) -> uuid.UUID | None:
    """One of the ids this server put in the checkout's custom data."""
    if not isinstance(custom, dict):
        return None
    try:
        return uuid.UUID(str(custom.get(key)))
    except (ValueError, TypeError):
        return None


def _object(value: object) -> dict:
    """A JSON object, or an empty one for anything else, so that a payload of an
    unexpected shape reads as missing fields rather than as a 500."""
    return value if isinstance(value, dict) else {}


def _epoch(value: object) -> datetime | None:
    """Stripe's timestamps are Unix seconds."""
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return datetime.fromtimestamp(value, tz=UTC)


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

    if await _already_recorded(db, event):
        # Not an error, and deliberately not re-applied: processors retry on any
        # non-2xx, and a blip between granting and answering 200 is exactly the
        # case this exists for. **No second row** — the ledger row that detected
        # this duplicate is the one whose uniqueness would refuse it.
        return _observe(event, DUPLICATE, detail=None, household_id=event.household_id)

    # Found before anything is decided, the ignored events included. The
    # ledger's household column is a foreign key, and an id naming no row here
    # written into it failed the insert: an uncounted 500 that the processor
    # retried until it gave up on the endpoint.
    household = await db.get(Household, event.household_id) if event.household_id is not None else None
    if household is None:
        return await _record_absent(db, event)
    if event.action == IGNORE:
        return await _record(db, event, IGNORED, detail=None)

    scoped = _out_of_scope(event, household)
    if scoped is not None:
        outcome, detail = scoped
        return await _record(db, event, outcome, detail=detail)
    if entitlements.comped(household):
        return await _leave_the_comp_alone(db, event, household)

    note = f"{event.event_type} via {event.processor}"
    if event.action in (HOLD, REVOKE):
        # The end of a subscription, or a renewal it could not collect, brings
        # the expiry to now and keeps the tier, so the grace period TERMS
        # promises runs, then the free tier's caps, and dunning sends the lapse
        # email: the same path as a year that ran out on its own. Wiping the row
        # at once, as this used to, skipped all three.
        now = datetime.now(UTC)
        await entitlements.expire(
            db,
            household,
            at=min(event.occurred_at or now, now),
            note=note,
            tracking=entitlements.Tracking(
                subscription_id=event.subscription_id, state=_tracked_state(event), at=event.occurred_at
            ),
        )
        if event.action == REVOKE:
            return await _record(db, event, REVOKED, detail=None)
        return await _record(
            db,
            event,
            UNPAID,
            detail=f"status {event.status}, so nothing is granted and the grace period runs from here",
        )

    if not event.status_known:
        return await _record(
            db,
            event,
            REFUSED,
            detail=(
                f"subscription status {event.status!r} is not one this server knows, and a grant needs to know "
                "the money arrived"
            ),
        )
    if event.renews_at is None:
        # A grant with no expiry never lapses, so this would quietly hand out a
        # subscription that nobody has to renew. Every processor sends the
        # paid-through date; an event that does not carry one is a shape this
        # server has not understood, and understanding it wrongly is worse than
        # refusing it.
        return await _record(
            db,
            event,
            REFUSED,
            detail="event carries no billing period end, and a grant without one would never expire",
        )

    try:
        await entitlements.grant(
            db,
            household,
            tier=limits.PAID,
            until=event.renews_at,
            source=event.processor,
            note=note,
            # What they agreed, written on the row the first time they pay and
            # never again (§6, "founding price for life"). The event's own list
            # price where the processor reports one (#128): the setting is what
            # this server *offers* and the snapshot is what this household
            # *agreed*, and they diverge the day the price changes. Where the
            # event names none, the price the checkout they came through was
            # advertising, from `BILLING_PRICE_PENCE`.
            **_price_from(event, household),
            customer_id=event.customer_id,
            # A processor reporting this year's list price on a renewal is not
            # an operator making a typo: honour the renewal and leave the
            # founding snapshot alone. Refusing here would turn a payment into
            # `refused`, which is somebody paying and not being credited for the
            # sake of a column.
            price_conflict=entitlements.KEEP_EXISTING_PRICE,
            tracking=entitlements.Tracking(
                subscription_id=event.subscription_id,
                state=_tracked_state(event),
                at=event.occurred_at,
                payer_id=await _payer(db, event, household),
            ),
        )
    except entitlements.EntitlementError as exc:
        # Deterministic: retrying will fail identically, so the endpoint answers
        # 200 to stop the retries and the alert is what gets a human involved.
        # This is the one branch where somebody has paid and not been credited.
        return await _record(db, event, REFUSED, detail=str(exc)[:300])
    return await _record(db, event, GRANTED, detail=None)


async def _record_absent(db: AsyncSession, event: Incoming) -> str:
    """An event naming no household on this server.

    Only a payment is an orphan: somebody may have paid and been credited to
    nobody, which is what the alert is for. A failed renewal or an ending for a
    household that is not here takes nothing from anybody, and is the expected
    tail of a household whose last member cancelled and then deleted it, so it
    is recorded without paging anyone.

    The id goes in the text rather than the column either way: it names no row
    here, and the column is a foreign key.
    """
    if event.household_id is None:
        missing = "no household_id in the checkout's custom_data"
    else:
        missing = f"household {event.household_id} is not on this server"
    if event.action == GRANT:
        return await _record(db, event, ORPHAN, detail=f"{missing}, so nobody could be credited", household_id=None)
    # An event unrelated to any checkout carries no household id at all, and
    # saying so on every one of them would be noise rather than news.
    detail = None if event.action == IGNORE and event.household_id is None else missing
    return await _record(db, event, IGNORED, detail=detail, household_id=None)


def _out_of_scope(event: Incoming, household: Household) -> tuple[str, str] | None:
    """Why this event may not change this household's entitlement, as the
    outcome and the sentence an operator reads, or None if it may.

    An entitlement follows one subscription, and only what that subscription
    says, in the order the processor said it, can move it. Without this, the
    end of a second, accidental subscription ended the year the first had paid
    for, and a retried `updated` from before a cancellation granted it back.

    The sentences name only what the event itself carries. `detail` is logged
    as well as kept, and CodeQL reads anything named `billing_*` as personal
    data (see `_fail`), so the household's own subscription and time stay on
    its row, where an operator can read them beside the ledger's household id.
    """
    last = household.billing_event_at
    if last is not None and event.occurred_at is not None:
        last = _aware(last)
        # Equal times pass, because a subscription's first events often share a
        # second, except for a hold: at checkout an `incomplete` snapshot can
        # arrive after the `active` one stamped the same second, and it must not
        # pull back a year that has just been paid for.
        if event.occurred_at < last or (event.occurred_at == last and event.action == HOLD):
            return IGNORED, (
                f"stamped {event.occurred_at:%Y-%m-%d %H:%M:%S} UTC, older than the newest event already applied here"
            )

    tracked = household.billing_subscription_id
    ended = household.billing_subscription_state == entitlements.ENDED
    if event.subscription_id is None:
        # Every processor names the subscription on these events, so one that
        # does not is a shape this server has not understood.
        return (REFUSED if event.action == GRANT else IGNORED), (
            "the event names no subscription, so it cannot be matched to the one this household's entitlement follows"
        )
    if event.subscription_id == tracked:
        if ended:
            return IGNORED, (
                f"subscription {event.subscription_id} has already ended, and an ended subscription does not come back"
            )
        return None

    if event.action == GRANT:
        if tracked is not None and not ended and entitlements.state(household) == entitlements.PAID:
            until = entitlements.describe(household).paid_until
            return REFUSED, (
                f"a second subscription ({event.subscription_id}) for a household already paid for until "
                f"{until:%Y-%m-%d}: somebody is paying twice. Refund and cancel one of them at the processor"
            )
        # Nothing live to follow, so this one is adopted: a first payment, a
        # return after the last one ended, or a household that paid before
        # subscriptions were tracked.
        return None
    # Unless both ids are known and differ, this could be the household's own.
    same_customer = event.customer_id is None or household.billing_customer_id in (None, event.customer_id)
    if (
        tracked is None
        and household.billing_event_at is None
        and household.entitlement_source not in (None, entitlements.COMP)
        and same_customer
    ):
        # Paid before subscriptions were tracked, when a household could only
        # have the one: the subscription this is about is that one, as long as
        # it belongs to the customer the household is.
        return None
    return IGNORED, (
        f"about subscription {event.subscription_id}, which is not the one this household's entitlement follows, "
        "so it is not this one's to change"
    )


async def _leave_the_comp_alone(db: AsyncSession, event: Incoming, household: Household) -> str:
    """An operator's comp is theirs, and no processor event moves it.

    What the subscription it already follows is doing is still noted, because
    that decides whether its payer may walk away: somebody comped while a
    subscription of theirs was running can then cancel it and be let go. A
    payment for anything else is money spent on nothing, and is refused, which
    pages a human.
    """
    if event.subscription_id == household.billing_subscription_id:
        entitlements.track(
            household,
            entitlements.Tracking(subscription_id=None, state=_tracked_state(event), at=event.occurred_at),
        )
        return await _record(
            db,
            event,
            IGNORED,
            detail="comped by the operator, so the entitlement is left as it is; the subscription's state is noted",
        )
    if event.action == GRANT:
        return await _record(
            db,
            event,
            REFUSED,
            detail=(
                f"a subscription ({event.subscription_id}) paying for a household the operator has comped: refund "
                "it at the processor, or end the comp if the payment should take over"
            ),
        )
    return await _record(db, event, IGNORED, detail="comped by the operator, so there is nothing here to end")


def _tracked_state(event: Incoming) -> str:
    if event.action == REVOKE:
        return entitlements.ENDED
    return entitlements.RENEWING if event.renews else entitlements.CANCELLED


async def _payer(db: AsyncSession, event: Incoming, household: Household) -> uuid.UUID | None:
    """Whose card this is, or None to keep the one already recorded.

    The checkout carries the member who opened it, beside the household. One
    opened before it did, or by somebody whose account has since gone, falls
    back to the lead, who was the only member allowed to open one.
    """
    if event.payer_id is not None and await db.get(User, event.payer_id) is not None:
        return event.payer_id
    return household.lead_user_id if household.billing_user_id is None else None


def _aware(value: datetime) -> datetime:
    # SQLite round-trips datetimes naive; stored values are UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _price_from(event: Incoming, household: Household) -> dict[str, object]:
    """The price to write on a grant.

    The event's list price when the processor sent one; otherwise the configured
    price, and only for a household that has no snapshot yet. Lemon Squeezy's
    subscription payload names the variant rather than what it costs, so on a
    server that names no price either the snapshot stays null, which #128 is
    explicit is better than a guess in a column that promises not to change.
    """
    if event.price_pence is not None:
        return {"price_pence": event.price_pence, "price_currency": event.price_currency}
    return _price_to_snapshot(household)


def _price_to_snapshot(household: Household) -> dict[str, object]:
    """The price to write on a grant, which is one that has none yet."""
    if household.price_pence is not None:
        return {}
    settings = get_settings()
    if settings.billing_price_pence is None:
        return {}
    return {"price_pence": settings.billing_price_pence, "price_currency": settings.billing_price_currency}


async def _find_event(db: AsyncSession, event: Incoming) -> BillingEvent | None:
    result = await db.execute(
        select(BillingEvent).where(BillingEvent.processor == event.processor, BillingEvent.event_id == event.event_id)
    )
    return result.scalar_one_or_none()


async def _already_recorded(db: AsyncSession, event: Incoming) -> bool:
    """Whether the ledger has seen this event, checked before any work is done."""
    return await _find_event(db, event) is not None


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
    try:
        await db.commit()
    except IntegrityError:
        # Two deliveries of one event can overlap — processors send duplicates
        # and retry on any non-2xx — and then both pass the check at the top of
        # `handle` before either writes. The ledger's uniqueness is what refuses
        # the second, and this is where that refusal is read as what it is: the
        # same duplicate the sequential case answers 200 to, rather than a 500
        # that asks for a retry the ledger would refuse identically. Left to
        # raise it would also be counted nowhere, which is the silent failure
        # this module exists to not have.
        await db.rollback()
        if await _find_event(db, event) is None:
            raise  # not the ledger's uniqueness, so it is not ours to absorb
        return _observe(event, DUPLICATE, detail=None, household_id=on_household)
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


# ------------------------------------------------------- starting a checkout
#
# The webhook above is the *end* of a payment. This is the beginning, and issue
# #121 exists because the beginning was missing: the route waited for a checkout
# that nothing in this repo could start.
#
# Three things shape every line of it.
#
# **The household id has to land where the webhook reads it.** That link is the
# only one there is — a payment that names no household is recorded as an orphan
# and somebody has paid for nothing. Stripe carries it on
# `subscription_data[metadata]`, because metadata on the *session* does not reach
# the subscription and the subscription is what the webhook sees; Paddle on
# `custom_data`, which it copies onto the subscription for recurring items; Lemon
# Squeezy on `checkout_data.custom`, which comes back as `meta.custom_data`. The
# member opening the checkout rides beside it, because billing belongs to whoever
# pays: the webhook records them, and the portal is theirs alone.
#
# **Managed Payments is a parameter, not just a setting.** `managed_payments
# [enabled]=true` on the Checkout Session is what makes Stripe the merchant of
# record for that sale. Leave it off and the payment still succeeds, the customer
# still gets their subscription, and *you* are the seller of record with the EU
# VAT to file — the exact thing §7 chose a merchant of record to avoid, failing
# silently. It is one line and it is the most expensive line here to lose.
#
# **Nothing here grants anything.** Starting a checkout is not a payment;
# `entitlements.grant` is reached only from a verified webhook carrying a billing
# period end. A checkout that is abandoned leaves no trace but a log line.

#: The Stripe API version this integration is written against, sent on every
#: request rather than left to the account's default.
#:
#: `managed_payments` exists from `2025-03-31.basil`, and the account default is
#: a dashboard setting this server cannot see. Left unpinned, an account on an
#: older version would reject the parameter and every checkout would fail — or,
#: worse for the one thing §7 turns on, a future default could change what it
#: means. The version that decides who the legal seller is should not be
#: invisible, so it is written here where it can be read and changed on purpose.
STRIPE_API_VERSION = "2025-03-31.basil"

#: Where each processor's API lives. `BILLING_API_BASE` overrides it, which is
#: how you reach Paddle's sandbox (`sandbox-api.paddle.com`) and how the tests
#: point this at a stub instead of the internet.
_API_BASES = {
    STRIPE: "https://api.stripe.com",
    PADDLE: "https://api.paddle.com",
    LEMONSQUEEZY: "https://api.lemonsqueezy.com",
}


class CheckoutError(Exception):
    """No checkout could be started, so nobody was charged.

    Carries a sentence for the person who pressed the button rather than the
    processor's own words: those name price ids and API versions, which is
    operator business and reaches the operator through the log instead.
    """

    def __init__(self, detail: str, *, status_code: int = 502) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def checkout_refusal(household: Household) -> str | None:
    """Why this household may not start a subscription now, or None if it may.

    Anything still running means a second subscription would be a second
    charge: a household in date, and one in its grace period whose subscription
    is still open at the processor, which is retrying a declined card that
    Manage billing can fix. One in its grace period because its subscription
    *ended* has nothing running, and may pay again straight away rather than
    wait out the grace period to be let back in.
    """
    if limits.effective_tier(household) == limits.FREE:
        return None
    entitlement = entitlements.describe(household)
    if entitlement.state == entitlements.GRACE:
        if not entitlements.subscription_open(household):
            return None
        return (
            "this household's subscription is still open at the payment processor, so a second one would "
            "charge twice. Manage billing (POST /billing/portal) is where it is renewed or its card updated."
        )
    until = f" until {entitlement.paid_until:%-d %B %Y}" if entitlement.paid_until else ", and it does not run out"
    return (
        f"this household is already on the {entitlement.stored_tier} tier{until}, so there is nothing to buy. "
        "GET /billing/subscription has the details, including where to manage it."
    )


async def start_checkout(household: Household, *, payer: User, return_url: str) -> str:
    """Create a hosted checkout for this household and return where to send them.

    The URL is the processor's, is single-use, and is deliberately never logged:
    it is a payment page bound to one household and the member paying for it.
    """
    settings = get_settings()
    processor = settings.billing_processor
    base = (settings.billing_api_base or _API_BASES.get(processor, "")).rstrip("/")
    if not base:
        raise CheckoutError("this server's billing is not configured", status_code=503)

    try:
        async with httpx.AsyncClient(timeout=settings.billing_api_timeout_seconds) as client:
            if processor == STRIPE:
                url = await _stripe_checkout(client, base, settings, household, payer, return_url)
            elif processor == PADDLE:
                url = await _paddle_checkout(client, base, settings, household, payer)
            else:
                url = await _lemonsqueezy_checkout(client, base, settings, household, payer, return_url)
    except httpx.HTTPError as exc:
        log_event("billing.checkout_failed", household_id=household.id, outcome="unreachable")
        raise CheckoutError(
            "the payment processor did not answer, so nothing was charged. Try again in a minute."
        ) from exc

    log_event("billing.checkout_started", household_id=household.id)
    return url


def _fail(household: Household, response: httpx.Response) -> CheckoutError:
    """One place for "the processor said no": a findable log line, and a sentence
    for whoever pressed the button.

    Two things are deliberately *not* in that line.

    **The response body.** It reads like operator business — a bad price id, an
    API version — but a processor rejecting a request commonly quotes the
    offending parameter back, and one of the parameters here is the customer's
    email address. `/privacy` promises this server does not write those down.

    **Which processor it was.** That is `BILLING_PROCESSOR`, one value for the
    whole deployment, so repeating it on every event says nothing the settings do
    not already say. (CodeQL also reads any field whose name contains "billing"
    as personal data, which it is not; dropping a redundant field was the
    cheaper answer to that than arguing.)

    The status and the timestamp are enough to find the exchange in the
    processor's own dashboard, which has all of it.
    """
    log_event(
        "billing.checkout_failed",
        household_id=household.id,
        outcome="refused",
        status=response.status_code,
    )
    return CheckoutError("the payment processor refused to open a checkout, so nothing was charged.")


def _custom(household: Household, payer: User) -> dict[str, str]:
    """What the webhook reads back: who is being paid for, and who is paying."""
    return {"household_id": str(household.id), "user_id": str(payer.id)}


async def _stripe_checkout(
    client: httpx.AsyncClient, base: str, settings, household: Household, payer: User, return_url: str
) -> str:
    form = {
        "mode": "subscription",
        "line_items[0][price]": settings.billing_price_id,
        "line_items[0][quantity]": "1",
        # Stripe as merchant of record for this sale. See the note above: losing
        # this line does not break the payment, it moves the tax liability.
        "managed_payments[enabled]": "true",
        "success_url": return_url,
        "cancel_url": return_url,
        # On the subscription, not the session: the webhook reads the
        # subscription object, and metadata does not travel from one to the other.
        **{f"subscription_data[metadata][{key}]": value for key, value in _custom(household, payer).items()},
        # Belt and braces, and what the Dashboard shows beside the payment.
        "client_reference_id": str(household.id),
        "customer_email": payer.email,
    }
    response = await client.post(
        f"{base}/v1/checkout/sessions",
        data=form,
        auth=(settings.billing_api_key, ""),
        headers={"Stripe-Version": STRIPE_API_VERSION},
    )
    if response.status_code >= 400:
        raise _fail(household, response)
    url = (response.json() or {}).get("url")
    if not url:
        raise _fail(household, response)
    return str(url)


async def _paddle_checkout(client: httpx.AsyncClient, base: str, settings, household: Household, payer: User) -> str:
    # No `checkout.url` is sent: that field names a page of yours hosting
    # Paddle.js, and this server hosts none. Omitted, Paddle composes the link
    # from the default payment link in the dashboard, which is the whole of the
    # setup here.
    body = {
        "items": [{"price_id": settings.billing_price_id, "quantity": 1}],
        "custom_data": _custom(household, payer),
        "customer": {"email": payer.email},
    }
    response = await client.post(
        f"{base}/transactions",
        json=body,
        headers={"Authorization": f"Bearer {settings.billing_api_key}", "Paddle-Version": "1"},
    )
    if response.status_code >= 400:
        raise _fail(household, response)
    url = (((response.json() or {}).get("data") or {}).get("checkout") or {}).get("url")
    if not url:
        # Paddle answers 200 with a null checkout url when no default payment
        # link is set, which is a dashboard setting rather than a bad request.
        raise _fail(household, response)
    return str(url)


async def _lemonsqueezy_checkout(
    client: httpx.AsyncClient, base: str, settings, household: Household, payer: User, return_url: str
) -> str:
    body = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_data": {"email": payer.email, "custom": _custom(household, payer)},
                "product_options": {"redirect_url": return_url},
            },
            "relationships": {
                "store": {"data": {"type": "stores", "id": str(settings.billing_store_id)}},
                "variant": {"data": {"type": "variants", "id": str(settings.billing_price_id)}},
            },
        }
    }
    response = await client.post(
        f"{base}/v1/checkouts",
        json=body,
        headers={
            "Authorization": f"Bearer {settings.billing_api_key}",
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
        },
    )
    if response.status_code >= 400:
        raise _fail(household, response)
    url = (((response.json() or {}).get("data") or {}).get("attributes") or {}).get("url")
    if not url:
        raise _fail(household, response)
    return str(url)


# ------------------------------------------------------- managing one, after
#
# Issue #129. `BILLING_MANAGE_URL` alone was the wrong answer: Stripe's no-code
# portal link lands on "Log in to manage your account", which asks a household
# that is *already signed in* for an email address and then emails them a link.
# Found by clicking it.
#
# So: mint a session where we can, fall back to the configured URL where we
# cannot, and say there is nothing to manage where neither is true.
#
# Only Stripe can be minted here, and that is a fact about the other two rather
# than a gap: Paddle and Lemon Squeezy both hand out *per-subscription*
# management URLs in the webhook payload (`management_urls`, `urls`) rather than
# offering a customer-portal endpoint, and this server does not keep them. For
# those, the configured URL is the honest answer.


def can_manage(household: Household) -> bool:
    """Whether there is anywhere to send this household to manage its billing.

    False for a household that never paid, even on a server with a portal URL
    configured: a link to somebody else's login page is not a feature.
    """
    settings = get_settings()
    if _mintable(household, settings):
        return True
    return bool(settings.billing_manage_url and household.entitlement_source not in (None, entitlements.COMP))


def _mintable(household: Household, settings) -> bool:
    # A session opens straight into the customer's own portal, which shows
    # their card, their address and their invoices, and lets them cancel. So
    # there has to be a payer on record for it to be minted for.
    return bool(
        settings.billing_processor == STRIPE
        and household.billing_customer_id
        and household.billing_user_id
        and settings.billing_api_key
    )


async def portal_url(household: Household, *, user: User, return_url: str) -> str:
    """Where this household manages its subscription: a one-time session into
    the payer's own portal when it is the payer asking, the configured page
    otherwise."""
    settings = get_settings()
    if not _mintable(household, settings):
        if can_manage(household):
            return str(settings.billing_manage_url)
        raise CheckoutError(
            "this household has no subscription to manage. GET /billing/subscription says where it stands.",
            status_code=409,
        )
    if household.billing_user_id != user.id:
        # The router refuses first and names the payer. This is the line that
        # keeps anything else from ever minting a session into their card.
        raise CheckoutError("only the member who pays for this household can open its billing portal.", status_code=403)

    base = (settings.billing_api_base or _API_BASES[STRIPE]).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings.billing_api_timeout_seconds) as client:
            response = await client.post(
                f"{base}/v1/billing_portal/sessions",
                data={"customer": household.billing_customer_id, "return_url": return_url},
                auth=(settings.billing_api_key, ""),
                headers={"Stripe-Version": STRIPE_API_VERSION},
            )
    except httpx.HTTPError as exc:
        log_event("billing.portal_failed", household_id=household.id, outcome="unreachable")
        raise CheckoutError("the payment processor did not answer. Try again in a minute.") from exc

    if response.status_code >= 400:
        log_event("billing.portal_failed", household_id=household.id, outcome="refused", status=response.status_code)
        # Falling back rather than failing: a stale customer id is the likely
        # cause, and a login page they can get into beats an error they cannot.
        if settings.billing_manage_url:
            return str(settings.billing_manage_url)
        raise CheckoutError("the payment processor would not open a billing portal just now.")

    url = (response.json() or {}).get("url")
    if not url:
        raise CheckoutError("the payment processor would not open a billing portal just now.")
    return str(url)
