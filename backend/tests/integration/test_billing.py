"""The billing webhook (issue #99, planning/08-freemium.md §2).

Three claims, in the order it would cost to get them wrong:

1. **It does not exist unless a deployment turns it on.** A self-hosted
   instance has no billing and must not be able to acquire one by accident.
2. **Nothing unsigned is ever acted on.** The signature is the whole of the
   authentication: the caller is a machine that has never heard of this app's
   accounts.
3. **A retry cannot grant a second year**, and a *silent* failure cannot happen
   at all. Every request ends as exactly one counted, logged, recorded outcome,
   including the ones that decide to do nothing.
4. **A verified event is not yet a payment.** It grants only when the
   subscription's status says the money arrived, only for the subscription the
   household paid through and only in the order the processor said things, and
   never over a comp. An ending keeps the grace period. And billing belongs to
   whoever pays: the portal is theirs, and they cannot walk away from a
   subscription that will charge them again.

The two adapters are tested against the formats read from the live docs on
2026-08-22: Paddle signs "{ts}:{body}" and sends `Paddle-Signature`, Lemon
Squeezy signs the body and sends `X-Signature`.
"""

import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import limits
from app.models import BillingEvent, Household
from app.services import billing, dunning, entitlements
from tests.conftest import register

SECRET = "a-signing-secret"


async def _false() -> bool:
    """An awaitable `False`, for standing in for a lookup that found nothing."""
    return False


@pytest.fixture
def sessions(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def paddle(settings_override):
    settings_override(BILLING_PROCESSOR="paddle", BILLING_WEBHOOK_SECRET=SECRET)


@pytest.fixture
def lemonsqueezy(settings_override):
    settings_override(BILLING_PROCESSOR="lemonsqueezy", BILLING_WEBHOOK_SECRET=SECRET)


async def household_id(sessions) -> uuid.UUID:
    async with sessions() as db:
        return (await db.execute(select(Household))).scalars().one().id


def paddle_post(payload: dict, *, secret: str = SECRET, at: datetime | None = None) -> tuple[str, dict]:
    body = json.dumps(payload)
    stamp = str(int((at or datetime.now(UTC)).timestamp()))
    signature = hmac.new(secret.encode(), f"{stamp}:{body}".encode(), hashlib.sha256).hexdigest()
    return body, {"Paddle-Signature": f"ts={stamp};h1={signature}", "Content-Type": "application/json"}


def lemon_post(payload: dict, *, secret: str = SECRET, event: str | None = None) -> tuple[str, dict]:
    body = json.dumps(payload)
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    headers = {"X-Signature": signature, "Content-Type": "application/json"}
    if event:
        headers["X-Event-Name"] = event
    return body, headers


def _iso(at: datetime | None) -> str:
    return (at or datetime.now(UTC)).isoformat().replace("+00:00", "Z")


def paddle_event(
    household: uuid.UUID | None,
    *,
    kind="subscription.created",
    event_id="evt_1",
    ends="2027-08-22T00:00:00Z",
    status="active",
    subscription="sub_01paddle",
    at: datetime | None = None,
):
    """A subscription notification the way Paddle sends one: the event's own
    `occurred_at`, and the subscription itself, status and all, under `data`."""
    data: dict = {"id": subscription, "status": status, "current_billing_period": {"ends_at": ends}}
    if household is not None:
        data["custom_data"] = {"household_id": str(household)}
    return {"event_id": event_id, "event_type": kind, "occurred_at": _iso(at), "data": data}


def lemon_event(
    household: uuid.UUID | None,
    *,
    kind="subscription_created",
    renews="2027-08-22T00:00:00.000000Z",
    status="active",
    subscription="1",
    at: datetime | None = None,
):
    """Lemon Squeezy's shape: the name in `meta`, and a subscription whose
    `updated_at` is the only clock the payload carries."""
    meta: dict = {"event_name": kind}
    if household is not None:
        meta["custom_data"] = {"household_id": str(household)}
    attributes = {"status": status, "renews_at": renews, "updated_at": _iso(at)}
    return {"meta": meta, "data": {"type": "subscriptions", "id": subscription, "attributes": attributes}}


@pytest.fixture
def stripe(settings_override):
    settings_override(BILLING_PROCESSOR="stripe", BILLING_WEBHOOK_SECRET=SECRET)


def stripe_post(
    payload: dict,
    *,
    secret: str = SECRET,
    at: datetime | None = None,
    extra_schemes: str = "",
    secrets_live: tuple[str, ...] = (),
) -> tuple[str, dict]:
    """Sign the way Stripe does: `t=…,v1=…`, over "{t}.{body}".

    `secrets_live` signs once per secret, which is what a rolled endpoint secret
    looks like for the 24 hours both are active.
    """
    body = json.dumps(payload)
    stamp = str(int((at or datetime.now(UTC)).timestamp()))
    keys = secrets_live or (secret,)
    signatures = ",".join(
        f"v1={hmac.new(key.encode(), f'{stamp}.{body}'.encode(), hashlib.sha256).hexdigest()}" for key in keys
    )
    header = f"t={stamp},{signatures}"
    if extra_schemes:
        header = f"{header},{extra_schemes}"
    return body, {"Stripe-Signature": header, "Content-Type": "application/json"}


def stripe_event(
    household: uuid.UUID | None,
    *,
    kind="customer.subscription.updated",
    event_id="evt_stripe_1",
    period_end: int | None = 1_818_000_000,
    on_items: bool = False,
    status="active",
    subscription="sub_1",
    at: datetime | None = None,
    payer: uuid.UUID | None = None,
    cancelling: bool = False,
):
    """An event the way Stripe sends one: `created` in Unix seconds, and the
    subscription object with its status. `payer` is the member id the checkout
    put in the metadata beside the household's."""
    obj: dict = {"id": subscription, "object": "subscription", "status": status, "cancel_at_period_end": cancelling}
    if household is not None:
        obj["metadata"] = {"household_id": str(household)}
        if payer is not None:
            obj["metadata"]["user_id"] = str(payer)
    if period_end is not None:
        # Newer API versions carry the period on the items rather than the
        # subscription; both shapes have to work.
        if on_items:
            obj["items"] = {"data": [{"id": "si_1", "current_period_end": period_end}]}
        else:
            obj["current_period_end"] = period_end
    created = int((at or datetime.now(UTC)).timestamp())
    return {"id": event_id, "object": "event", "type": kind, "created": created, "data": {"object": obj}}


class TestItDoesNotExistUnlessTurnedOn:
    async def test_no_processor_means_no_endpoint(self, auth_client):
        """404, not 401 or 403: on almost every deployment the route really is
        not there, and saying so is the honest answer."""
        response = await auth_client.post("/billing/webhook", json={"anything": True})
        assert response.status_code == 404

    async def test_a_secret_without_a_processor_is_still_off(self, client, settings_override):
        settings_override(BILLING_WEBHOOK_SECRET=SECRET)
        assert (await client.post("/billing/webhook", json={})).status_code == 404

    async def test_a_processor_without_a_secret_is_still_off(self, client, settings_override):
        """Half-configured is off, not open."""
        settings_override(BILLING_PROCESSOR="paddle")
        assert (await client.post("/billing/webhook", json={})).status_code == 404

    async def test_it_is_absent_from_the_public_schema(self, client):
        spec = (await client.get("/openapi.json")).json()
        assert "/billing/webhook" not in spec["paths"]


class TestNothingUnsignedIsActedOn:
    async def test_no_signature_at_all(self, client, paddle, sessions):
        await register(client)
        body, _ = paddle_post(paddle_event(await household_id(sessions)))
        response = await client.post("/billing/webhook", content=body, headers={"Content-Type": "application/json"})
        assert response.status_code == 401

    async def test_a_signature_from_the_wrong_secret(self, client, paddle, sessions):
        await register(client)
        body, headers = paddle_post(paddle_event(await household_id(sessions)), secret="not-the-secret")
        assert (await client.post("/billing/webhook", content=body, headers=headers)).status_code == 401

    async def test_a_tampered_body_no_longer_verifies(self, client, paddle, sessions):
        """The signature covers the body, so moving the expiry out by a decade
        has to invalidate it."""
        await register(client)
        body, headers = paddle_post(paddle_event(await household_id(sessions)))
        tampered = body.replace("2027", "2037")
        assert (await client.post("/billing/webhook", content=tampered, headers=headers)).status_code == 401

    async def test_lemonsqueezy_signature_is_over_the_raw_body(self, client, lemonsqueezy, sessions):
        await register(client)
        body, headers = lemon_post(lemon_event(await household_id(sessions)), secret="wrong")
        assert (await client.post("/billing/webhook", content=body, headers=headers)).status_code == 401

    async def test_a_stale_paddle_signature_is_refused(self, client, paddle, sessions):
        await register(client)
        old = datetime.now(UTC) - timedelta(hours=2)
        body, headers = paddle_post(paddle_event(await household_id(sessions)), at=old)
        response = await client.post("/billing/webhook", content=body, headers=headers)
        assert response.status_code == 401
        assert "tolerance" in response.json()["detail"]

    async def test_nothing_was_granted_by_any_of_that(self, client, paddle, sessions):
        await register(client)
        target = await household_id(sessions)
        body, headers = paddle_post(paddle_event(target), secret="wrong")
        await client.post("/billing/webhook", content=body, headers=headers)
        async with sessions() as db:
            assert (await db.get(Household, target)).tier == "unlimited"
            assert (await db.execute(select(BillingEvent))).scalars().all() == []

    async def test_semantically_identical_json_still_fails(self, client, paddle, sessions):
        """Proves the signature is over the *bytes*, not over what they parse
        to. Re-serialising the body anywhere in the stack would break this, and
        breaking it silently is how a forged webhook gets accepted."""
        await register(client)
        payload = paddle_event(await household_id(sessions))
        body, headers = paddle_post(payload)
        respaced = json.dumps(payload, indent=2)  # same object, different bytes
        assert json.loads(respaced) == json.loads(body)
        assert (await client.post("/billing/webhook", content=respaced, headers=headers)).status_code == 401

    async def test_a_lemonsqueezy_signature_does_not_pass_a_paddle_server(self, client, paddle, sessions):
        """The header a deployment reads is decided by its own config, not by
        what the caller chose to send."""
        await register(client)
        body, headers = lemon_post(lemon_event(await household_id(sessions)), event="subscription_created")
        assert (await client.post("/billing/webhook", content=body, headers=headers)).status_code == 401

    async def test_a_paddle_signature_does_not_pass_a_lemonsqueezy_server(self, client, lemonsqueezy, sessions):
        await register(client)
        body, headers = paddle_post(paddle_event(await household_id(sessions)))
        assert (await client.post("/billing/webhook", content=body, headers=headers)).status_code == 401

    async def test_a_body_that_is_not_json(self, client, paddle):
        body = "not json"
        signature = hmac.new(SECRET.encode(), f"1:{body}".encode(), hashlib.sha256).hexdigest()
        response = await client.post(
            "/billing/webhook", content=body, headers={"Paddle-Signature": f"ts=1;h1={signature}"}
        )
        assert response.status_code == 400


class TestAPaymentBecomesAnEntitlement:
    async def test_paddle_grants_the_paid_tier_until_the_billing_period_ends(self, client, paddle, sessions):
        await register(client)
        target = await household_id(sessions)
        body, headers = paddle_post(paddle_event(target))

        response = await client.post("/billing/webhook", content=body, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"outcome": "granted"}

        async with sessions() as db:
            household = await db.get(Household, target)
        assert household.tier == "paid"
        assert household.entitlement_source == "paddle"
        assert entitlements.describe(household).paid_until.year == 2027
        assert limits.effective_tier(household) == "paid"

    async def test_the_price_agreed_is_written_on_the_household(self, client, paddle, sessions, settings_override):
        """§6's founding price is "stored as a snapshot on the household, not
        promised in a document", and `/privacy` says the same. The price the
        checkout was advertising is the one they agreed to."""
        settings_override(BILLING_PRICE_PENCE="2000", BILLING_PRICE_CURRENCY="GBP")
        await register(client)
        target = await household_id(sessions)
        body, headers = paddle_post(paddle_event(target))

        await client.post("/billing/webhook", content=body, headers=headers)
        async with sessions() as db:
            household = await db.get(Household, target)
        assert household.price_pence == 2000
        assert household.price_currency == "GBP"
        assert household.price_set_at is not None

    async def test_a_renewal_keeps_the_founding_price_and_is_never_refused_over_it(
        self, client, paddle, sessions, settings_override
    ):
        """The expensive way to get this wrong: send today's price on every
        grant, and the first renewal after a price rise is refused for the
        people the promise was made to."""
        settings_override(BILLING_PRICE_PENCE="2000")
        await register(client)
        target = await household_id(sessions)
        body, headers = paddle_post(paddle_event(target))
        await client.post("/billing/webhook", content=body, headers=headers)

        settings_override(BILLING_PRICE_PENCE="3000")  # a rise, for new customers
        renewal, renewal_headers = paddle_post(
            paddle_event(target, event_id="evt_renewal", ends="2028-08-22T00:00:00Z")
        )
        response = await client.post("/billing/webhook", content=renewal, headers=renewal_headers)
        assert response.json()["outcome"] == "granted"

        async with sessions() as db:
            household = await db.get(Household, target)
        assert household.price_pence == 2000  # theirs for life
        assert entitlements.describe(household).paid_until.year == 2028  # and renewed

    async def test_a_server_that_names_no_price_snapshots_none(self, client, paddle, sessions):
        await register(client)
        target = await household_id(sessions)
        body, headers = paddle_post(paddle_event(target))

        await client.post("/billing/webhook", content=body, headers=headers)
        async with sessions() as db:
            household = await db.get(Household, target)
        assert household.tier == "paid"
        assert household.price_pence is None

    async def test_lemonsqueezy_does_the_same_from_its_own_shape(self, client, lemonsqueezy, sessions):
        await register(client)
        target = await household_id(sessions)
        body, headers = lemon_post(lemon_event(target), event="subscription_created")

        response = await client.post("/billing/webhook", content=body, headers=headers)
        assert response.json() == {"outcome": "granted"}
        async with sessions() as db:
            household = await db.get(Household, target)
        assert (household.tier, household.entitlement_source) == ("paid", "lemonsqueezy")

    async def test_a_renewal_moves_the_expiry(self, client, paddle, sessions):
        await register(client)
        target = await household_id(sessions)
        first, headers = paddle_post(paddle_event(target, event_id="evt_1", ends="2027-01-01T00:00:00Z"))
        await client.post("/billing/webhook", content=first, headers=headers)
        second, headers2 = paddle_post(
            paddle_event(target, kind="subscription.updated", event_id="evt_2", ends="2028-01-01T00:00:00Z")
        )
        assert (await client.post("/billing/webhook", content=second, headers=headers2)).json()["outcome"] == "granted"

        async with sessions() as db:
            household = await db.get(Household, target)
        assert entitlements.describe(household).paid_until.year == 2028

    async def test_an_expiry_revokes(self, client, lemonsqueezy, sessions):
        """Revoked means the year stops now and the grace period starts: the
        tier is kept so that it lapses the way any year does (TERMS)."""
        await register(client)
        target = await household_id(sessions)
        body, headers = lemon_post(lemon_event(target), event="subscription_created")
        await client.post("/billing/webhook", content=body, headers=headers)

        ended = lemon_event(target, kind="subscription_expired", status="expired")
        body, headers = lemon_post(ended, event="subscription_expired")
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "revoked"
        async with sessions() as db:
            household = await db.get(Household, target)
        assert household.tier == "paid"
        assert entitlements.state(household) == entitlements.GRACE

    async def test_a_cancellation_does_not_cut_the_paid_year_short(self, client, lemonsqueezy, sessions):
        """Lemon Squeezy's `subscription_cancelled` starts a grace period that
        runs to the paid-through date. Revoking there would take away days
        somebody paid for; the entitlement expires on its own."""
        await register(client)
        target = await household_id(sessions)
        body, headers = lemon_post(lemon_event(target), event="subscription_created")
        await client.post("/billing/webhook", content=body, headers=headers)

        body, headers = lemon_post(lemon_event(target, kind="subscription_cancelled"), event="subscription_cancelled")
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "ignored"
        async with sessions() as db:
            household = await db.get(Household, target)
        assert household.tier == "paid"


class TestARetryCannotGrantASecondYear:
    async def test_the_same_paddle_event_twice(self, client, paddle, sessions):
        await register(client)
        target = await household_id(sessions)
        body, headers = paddle_post(paddle_event(target))

        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "granted"
        again = await client.post("/billing/webhook", content=body, headers=headers)
        assert again.status_code == 200
        assert again.json()["outcome"] == "duplicate"

    async def test_lemonsqueezy_deduplicates_on_the_body_it_sent(self, client, lemonsqueezy, sessions):
        """It sends no event id, so an identical retry has to be recognised by
        being identical."""
        await register(client)
        target = await household_id(sessions)
        body, headers = lemon_post(lemon_event(target), event="subscription_created")
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "granted"
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "duplicate"

    async def test_two_deliveries_that_overlap_are_still_one_duplicate(self, client, paddle, sessions, monkeypatch):
        """Processors send duplicates and retry on any non-2xx, so two copies of
        one event can be in flight at once and both pass the check at the top of
        `handle` before either writes. The ledger's uniqueness refuses the
        second, and that refusal has to read as the duplicate it is: a 500 would
        ask for a retry that fails identically, and would be counted nowhere."""
        await register(client)
        target = await household_id(sessions)
        body, headers = paddle_post(paddle_event(target))
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "granted"

        # What the race looks like from inside: the pre-check misses the row.
        monkeypatch.setattr(billing, "_already_recorded", lambda db, event: _false())
        again = await client.post("/billing/webhook", content=body, headers=headers)
        assert again.status_code == 200
        assert again.json()["outcome"] == "duplicate"

        async with sessions() as db:
            rows = (await db.execute(select(BillingEvent))).scalars().all()
        assert len(rows) == 1  # and still no second row

    async def test_a_duplicate_does_not_move_the_expiry(self, client, paddle, sessions):
        await register(client)
        target = await household_id(sessions)
        body, headers = paddle_post(paddle_event(target))
        await client.post("/billing/webhook", content=body, headers=headers)
        async with sessions() as db:
            first = (await db.get(Household, target)).paid_until

        await client.post("/billing/webhook", content=body, headers=headers)
        async with sessions() as db:
            assert (await db.get(Household, target)).paid_until == first


class TestStripeManagedPayments:
    """Stripe is a merchant of record too since Managed Payments, so the account
    this project already has can do the job. The signature scheme is the one
    place a hand-rolled verifier goes wrong."""

    async def test_a_subscription_grants_until_the_period_ends(self, client, stripe, sessions):
        await register(client)
        target = await household_id(sessions)
        body, headers = stripe_post(stripe_event(target))

        response = await client.post("/billing/webhook", content=body, headers=headers)
        assert response.json() == {"outcome": "granted"}
        async with sessions() as db:
            household = await db.get(Household, target)
        assert (household.tier, household.entitlement_source) == ("paid", "stripe")
        assert entitlements.describe(household).paid_until == datetime.fromtimestamp(1_818_000_000, tz=UTC)

    async def test_the_period_can_live_on_the_subscription_items(self, client, stripe, sessions):
        """Newer API versions moved `current_period_end` onto each item."""
        await register(client)
        target = await household_id(sessions)
        body, headers = stripe_post(stripe_event(target, on_items=True))
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "granted"

    async def test_a_deleted_subscription_revokes(self, client, stripe, sessions):
        await register(client)
        target = await household_id(sessions)
        body, headers = stripe_post(stripe_event(target))
        await client.post("/billing/webhook", content=body, headers=headers)

        ended, ended_headers = stripe_post(
            stripe_event(target, kind="customer.subscription.deleted", event_id="evt_stripe_2", status="canceled")
        )
        assert (await client.post("/billing/webhook", content=ended, headers=ended_headers)).json()[
            "outcome"
        ] == "revoked"
        async with sessions() as db:
            household = await db.get(Household, target)
        assert entitlements.state(household) == entitlements.GRACE
        assert household.billing_subscription_state == entitlements.ENDED

    async def test_an_invoice_event_is_ignored_rather_than_double_counted(self, client, stripe, sessions):
        """The subscription object is the source of truth for "paid until when";
        acting on the invoice too would be two writes for one payment."""
        await register(client)
        target = await household_id(sessions)
        body, headers = stripe_post(stripe_event(target, kind="invoice.paid", event_id="evt_stripe_3"))
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "ignored"

    async def test_any_live_signature_matches_while_a_secret_is_rolling(self, client, stripe, sessions):
        """Rolling an endpoint secret keeps the old one live for up to 24 hours,
        and Stripe signs once per active secret. Checking only the first would
        break every webhook for a day."""
        await register(client)
        target = await household_id(sessions)
        body, headers = stripe_post(stripe_event(target), secrets_live=("the-previous-secret", SECRET))
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "granted"

    async def test_a_v0_signature_is_never_accepted(self, client, stripe, sessions):
        """Stripe sends a deliberately fake `v0` beside test events. Accepting
        any scheme but v1 is a downgrade attack."""
        await register(client)
        target = await household_id(sessions)
        payload = stripe_event(target)
        body = json.dumps(payload)
        stamp = str(int(datetime.now(UTC).timestamp()))
        # A perfectly good signature, offered under the wrong scheme.
        forged = hmac.new(SECRET.encode(), f"{stamp}.{body}".encode(), hashlib.sha256).hexdigest()
        headers = {"Stripe-Signature": f"t={stamp},v0={forged}", "Content-Type": "application/json"}
        assert (await client.post("/billing/webhook", content=body, headers=headers)).status_code == 401

    async def test_a_wrong_secret_is_refused(self, client, stripe, sessions):
        await register(client)
        body, headers = stripe_post(stripe_event(await household_id(sessions)), secret="not-the-secret")
        assert (await client.post("/billing/webhook", content=body, headers=headers)).status_code == 401

    async def test_a_stale_signature_is_refused(self, client, stripe, sessions):
        await register(client)
        old = datetime.now(UTC) - timedelta(hours=2)
        body, headers = stripe_post(stripe_event(await household_id(sessions)), at=old)
        assert (await client.post("/billing/webhook", content=body, headers=headers)).status_code == 401

    async def test_a_retry_is_absorbed(self, client, stripe, sessions):
        await register(client)
        target = await household_id(sessions)
        body, headers = stripe_post(stripe_event(target))
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "granted"
        # Stripe re-signs every retry, so the header differs while the event id
        # does not. The ledger keys on the id, which is why this is caught.
        again, again_headers = stripe_post(stripe_event(target))
        assert (await client.post("/billing/webhook", content=again, headers=again_headers)).json()[
            "outcome"
        ] == "duplicate"


class TestAGrantAlwaysHasAnEndDate:
    """A grant with no expiry never lapses, so a webhook that produced one would
    quietly hand out a subscription nobody has to renew."""

    async def test_stripe_without_a_period_end(self, client, stripe, sessions):
        await register(client)
        target = await household_id(sessions)
        body, headers = stripe_post(stripe_event(target, period_end=None))
        response = await client.post("/billing/webhook", content=body, headers=headers)
        assert response.json()["outcome"] == "refused"
        async with sessions() as db:
            household = await db.get(Household, target)
            event = (await db.execute(select(BillingEvent))).scalars().one()
        assert household.tier == "unlimited"  # untouched
        assert "would never expire" in event.detail

    async def test_paddle_without_a_billing_period(self, client, paddle, sessions):
        await register(client)
        target = await household_id(sessions)
        body, headers = paddle_post(paddle_event(target, ends=None))
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "refused"


class TestNothingFailsQuietly:
    async def test_an_event_naming_no_household_is_loud(self, client, paddle, sessions):
        """Somebody may have paid and not been credited. It answers 200 so the
        processor stops retrying something deterministic, and the alert is what
        gets a human involved."""
        await register(client)
        body, headers = paddle_post(paddle_event(None))
        response = await client.post("/billing/webhook", content=body, headers=headers)
        assert response.status_code == 200
        assert response.json()["outcome"] == "orphan"

        async with sessions() as db:
            event = (await db.execute(select(BillingEvent))).scalars().one()
        assert event.outcome == "orphan"
        assert "custom_data" in event.detail

    async def test_an_event_naming_a_household_this_server_does_not_have(self, client, paddle, sessions):
        await register(client)
        body, headers = paddle_post(paddle_event(uuid.uuid4()))
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "orphan"

    async def test_an_entitlement_refusal_is_recorded_rather_than_retried(self, client, paddle, sessions):
        """The one branch where somebody has paid and not been credited: it must
        end in a row an operator can read, not a 500 loop."""
        await register(client)
        target = await household_id(sessions)
        async with sessions() as db:
            household = await db.get(Household, target)
            household.paid_until = None
            await db.commit()

        # An expiry in the past is refused by the entitlement layer.
        body, headers = paddle_post(paddle_event(target, ends="2020-01-01T00:00:00Z"))
        response = await client.post("/billing/webhook", content=body, headers=headers)
        assert response.status_code == 200
        assert response.json()["outcome"] == "refused"
        async with sessions() as db:
            event = (await db.execute(select(BillingEvent))).scalars().one()
        assert event.outcome == "refused"
        assert "not in the future" in event.detail

    async def test_an_event_we_have_no_opinion_about_is_still_recorded(self, client, paddle, sessions):
        """ "We saw it and did nothing" and "we never got it" are different
        problems, so the first one leaves a row."""
        await register(client)
        body, headers = paddle_post(paddle_event(await household_id(sessions), kind="customer.updated"))
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "ignored"
        async with sessions() as db:
            assert (await db.execute(select(BillingEvent))).scalars().one().outcome == "ignored"

    async def test_every_outcome_reaches_the_counter(self, client, paddle, sessions, settings_override):
        """The alert watches this counter, not the log.

        Measured as a delta: the registry is module-level and outlives any one
        test, so an absolute count would only be asserting the order this file
        happened to run in.
        """
        settings_override(BILLING_PROCESSOR="paddle", BILLING_WEBHOOK_SECRET=SECRET, METRICS_TOKEN="scrape-secret-1")
        await register(client)
        target = await household_id(sessions)

        async def counts() -> dict[str, float]:
            scraped = (await client.get("/metrics", headers={"Authorization": "Bearer scrape-secret-1"})).text
            found = re.findall(r'meals_billing_webhooks_total\{outcome="(\w+)"\} ([0-9.e+]+)', scraped)
            return {outcome: float(value) for outcome, value in found}

        before = await counts()
        body, headers = paddle_post(paddle_event(target))
        await client.post("/billing/webhook", content=body, headers=headers)
        bad, bad_headers = paddle_post(paddle_event(target, event_id="evt_2"), secret="wrong")
        await client.post("/billing/webhook", content=bad, headers=bad_headers)
        orphan, orphan_headers = paddle_post(paddle_event(None, event_id="evt_3"))
        await client.post("/billing/webhook", content=orphan, headers=orphan_headers)
        after = await counts()

        for outcome in ("granted", "bad_signature", "orphan"):
            moved = after.get(outcome, 0) - before.get(outcome, 0)
            assert moved == 1, f"{outcome} moved by {moved}, not 1"

    async def test_every_transition_is_logged(self, client, paddle, sessions, caplog):
        await register(client)
        body, headers = paddle_post(paddle_event(await household_id(sessions)))
        with caplog.at_level("INFO", logger="meals.events"):
            await client.post("/billing/webhook", content=body, headers=headers)
        record = next(r for r in caplog.records if r.getMessage() == "billing.webhook")
        assert (record.outcome, record.processor) == ("granted", "paddle")


class TestItKeepsWhatTheEventCarries:
    """Issues #128 and #129, both found by putting a real sandbox payment
    through the deployment and then looking at the row it wrote.

    Everything about the grant was right and two columns were empty, because the
    parser read the billing period and dropped the rest of the object on the
    floor. The price is §2's founding-price-for-life, which `grant` defends and
    nothing was writing; the customer id is what lets "Manage billing" open a
    portal instead of a login page.
    """

    async def test_stripe_records_the_price_and_the_customer(self, auth_client, stripe, sessions):
        household = await household_id(sessions)
        event = stripe_event(household, kind="customer.subscription.created")
        event["data"]["object"]["customer"] = "cus_test_1"
        event["data"]["object"]["currency"] = "gbp"
        event["data"]["object"]["items"] = {
            "data": [{"id": "si_1", "current_period_end": 1_818_000_000, "price": {"unit_amount": 2000}}]
        }
        body, headers = stripe_post(event)

        response = await auth_client.post("/billing/webhook", content=body, headers=headers)
        assert response.json()["outcome"] == "granted"

        async with sessions() as db:
            row = (await db.execute(select(Household))).scalars().one()
            assert row.price_pence == 2000
            assert row.price_currency == "GBP"
            assert row.billing_customer_id == "cus_test_1"

    async def test_stripe_reads_the_older_plan_shape_too(self, auth_client, stripe, sessions):
        """`plan.amount` is where this lived before prices moved onto items, and
        an account on an older API version still sends it."""
        household = await household_id(sessions)
        event = stripe_event(household)
        event["data"]["object"]["plan"] = {"amount": 1500, "currency": "gbp"}
        body, headers = stripe_post(event)

        assert (await auth_client.post("/billing/webhook", content=body, headers=headers)).json()[
            "outcome"
        ] == "granted"
        async with sessions() as db:
            assert (await db.execute(select(Household))).scalars().one().price_pence == 1500

    async def test_paddle_sends_the_amount_as_a_string(self, auth_client, paddle, sessions):
        household = await household_id(sessions)
        event = paddle_event(household)
        event["data"]["customer_id"] = "ctm_01example"
        event["data"]["items"] = [{"price": {"unit_price": {"amount": "2000", "currency_code": "GBP"}}}]
        body, headers = paddle_post(event)

        assert (await auth_client.post("/billing/webhook", content=body, headers=headers)).json()[
            "outcome"
        ] == "granted"
        async with sessions() as db:
            row = (await db.execute(select(Household))).scalars().one()
            assert row.price_pence == 2000
            assert row.price_currency == "GBP"
            assert row.billing_customer_id == "ctm_01example"

    async def test_lemonsqueezy_records_the_customer_and_no_price(self, auth_client, lemonsqueezy, sessions):
        """Its subscription payload names the variant, not what the variant
        costs. A snapshot we cannot take honestly stays null."""
        household = await household_id(sessions)
        event = lemon_event(household)
        event["data"]["attributes"]["customer_id"] = 4242  # an integer, unlike the others
        body, headers = lemon_post(event, event="subscription_created")

        assert (await auth_client.post("/billing/webhook", content=body, headers=headers)).json()[
            "outcome"
        ] == "granted"
        async with sessions() as db:
            row = (await db.execute(select(Household))).scalars().one()
            assert row.billing_customer_id == "4242"
            assert row.price_pence is None

    async def test_a_grant_with_no_readable_amount_is_still_a_grant(self, auth_client, stripe, sessions):
        """Unlike a missing billing period end, which is refused because it would
        never lapse, a missing price costs nobody their subscription."""
        household = await household_id(sessions)
        body, headers = stripe_post(stripe_event(household))  # no price anywhere

        assert (await auth_client.post("/billing/webhook", content=body, headers=headers)).json()[
            "outcome"
        ] == "granted"
        async with sessions() as db:
            row = (await db.execute(select(Household))).scalars().one()
            assert row.price_pence is None
            assert row.tier == limits.PAID

    async def test_a_renewal_at_a_higher_price_renews_and_keeps_the_founding_one(self, auth_client, stripe, sessions):
        """The whole point of the snapshot, and the one case that would have
        turned a payment into `refused` if the webhook used the operator's
        stricter rule: the price went up, this household's did not, and the year
        they just paid for still lands."""
        household = await household_id(sessions)
        first = stripe_event(household, event_id="evt_year_1")
        first["data"]["object"]["items"] = {
            "data": [{"current_period_end": 1_818_000_000, "price": {"unit_amount": 2000, "currency": "gbp"}}]
        }
        body, headers = stripe_post(first)
        await auth_client.post("/billing/webhook", content=body, headers=headers)

        renewal = stripe_event(household, event_id="evt_year_2")
        renewal["data"]["object"]["items"] = {
            "data": [{"current_period_end": 1_849_000_000, "price": {"unit_amount": 3000, "currency": "gbp"}}]
        }
        body, headers = stripe_post(renewal)
        assert (await auth_client.post("/billing/webhook", content=body, headers=headers)).json()[
            "outcome"
        ] == "granted"

        async with sessions() as db:
            row = (await db.execute(select(Household))).scalars().one()
            assert row.price_pence == 2000, "the founding price is for life"
            assert row.paid_until is not None
            # Stored values are UTC and SQLite hands them back naive, so say so
            # rather than letting .timestamp() read them as local time.
            assert int(row.paid_until.replace(tzinfo=UTC).timestamp()) == 1_849_000_000, (
                "and the renewal still happened"
            )


def _stamp(value: datetime | None) -> int | None:
    """A stored datetime as Unix seconds. SQLite hands them back naive, and they
    are UTC, so say so rather than let `.timestamp()` read them as local time."""
    if value is None:
        return None
    return int((value if value.tzinfo else value.replace(tzinfo=UTC)).timestamp())


async def webhook(client, event: dict, post=stripe_post) -> str:
    body, headers = post(event)
    response = await client.post("/billing/webhook", content=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["outcome"]


class TestAGrantNeedsTheMoneyToHaveArrived:
    """A snapshot of a subscription carries next year's date before anybody has
    paid for it: Stripe moves `current_period_end` on and *then* charges the
    card. Only a status that says the money arrived may grant, and a status that
    says it did not holds the household to what it has paid for."""

    async def test_an_incomplete_first_payment_grants_nothing(self, client, stripe, sessions):
        await register(client)
        target = await household_id(sessions)
        created = stripe_event(target, kind="customer.subscription.created", status="incomplete")
        assert await webhook(client, created) == "ignored"
        async with sessions() as db:
            household = await db.get(Household, target)
        assert household.paid_until is None
        assert household.entitlement_source is None

    async def test_a_declined_renewal_is_not_a_year_for_free(self, client, stripe, sessions):
        """What Stripe actually sends at a renewal: the period moves on while the
        subscription is still `active`, the card is declined an hour later, and
        it goes `past_due`. The unpaid year must not stand: the household runs to
        the decline, then the grace period, and a recovered card puts it back."""
        await register(client)
        target = await household_id(sessions)
        now = datetime.now(UTC)
        next_year = int((now + timedelta(days=365)).timestamp())
        first = stripe_event(
            target,
            kind="customer.subscription.created",
            event_id="evt_first",
            period_end=int((now + timedelta(hours=1)).timestamp()),
            at=now - timedelta(days=365),
        )
        advanced = stripe_event(target, event_id="evt_advanced", period_end=next_year, at=now - timedelta(minutes=70))
        declined_at = now - timedelta(minutes=10)
        declined = stripe_event(
            target, event_id="evt_declined", period_end=next_year, status="past_due", at=declined_at
        )

        assert await webhook(client, first) == "granted"
        assert await webhook(client, advanced) == "granted"
        assert await webhook(client, declined) == "unpaid"
        async with sessions() as db:
            household = await db.get(Household, target)
            row = (await db.execute(select(BillingEvent).where(BillingEvent.event_id == "evt_declined"))).scalar_one()
        assert _stamp(household.paid_until) == int(declined_at.timestamp())
        assert entitlements.state(household) == entitlements.GRACE
        assert limits.effective_tier(household) == limits.PAID  # grace: nothing changes yet
        assert limits.effective_tier(household, now=now + timedelta(days=15)) == limits.FREE
        assert "past_due" in row.detail

        recovered = stripe_event(target, event_id="evt_recovered", period_end=next_year, at=now - timedelta(minutes=1))
        assert await webhook(client, recovered) == "granted"
        async with sessions() as db:
            assert _stamp((await db.get(Household, target)).paid_until) == next_year

    async def test_a_declined_card_never_moves_the_date_forward(self, client, stripe, sessions):
        await register(client)
        target = await household_id(sessions)
        now = datetime.now(UTC)
        paid_to = int((now + timedelta(days=30)).timestamp())
        first = stripe_event(target, event_id="evt_1", period_end=paid_to, at=now - timedelta(days=1))
        assert await webhook(client, first) == "granted"
        unpaid = stripe_event(target, event_id="evt_2", period_end=paid_to + 400 * 86_400, status="unpaid")
        assert await webhook(client, unpaid) == "unpaid"
        async with sessions() as db:
            assert _stamp((await db.get(Household, target)).paid_until) <= paid_to

    async def test_paddle_past_due_is_not_a_payment(self, client, paddle, sessions):
        await register(client)
        target = await household_id(sessions)
        assert await webhook(client, paddle_event(target), post=paddle_post) == "granted"
        declined = paddle_event(
            target, kind="subscription.updated", event_id="evt_2", ends="2028-08-22T00:00:00Z", status="past_due"
        )
        assert await webhook(client, declined, post=paddle_post) == "unpaid"
        async with sessions() as db:
            household = await db.get(Household, target)
        assert entitlements.describe(household).paid_until < datetime.now(UTC)  # not 2028: nothing was paid

    async def test_lemonsqueezy_past_due_is_not_a_payment(self, client, lemonsqueezy, sessions):
        await register(client)
        target = await household_id(sessions)

        def post(event):
            return lemon_post(event, event=event["meta"]["event_name"])

        assert await webhook(client, lemon_event(target), post=post) == "granted"
        declined = lemon_event(
            target, kind="subscription_updated", renews="2028-08-22T00:00:00.000000Z", status="past_due"
        )
        assert await webhook(client, declined, post=post) == "unpaid"
        async with sessions() as db:
            household = await db.get(Household, target)
        assert entitlements.describe(household).paid_until < datetime.now(UTC)

    async def test_a_trial_is_in_good_standing(self, client, stripe, sessions):
        await register(client)
        target = await household_id(sessions)
        assert await webhook(client, stripe_event(target, status="trialing")) == "granted"

    async def test_a_status_this_server_does_not_know_is_refused_rather_than_granted(self, client, stripe, sessions):
        """Loud, because it may be somebody who paid; never a grant, because
        granting on a guess is how a year goes free."""
        await register(client)
        target = await household_id(sessions)
        assert await webhook(client, stripe_event(target, status="on_fire")) == "refused"
        async with sessions() as db:
            household = await db.get(Household, target)
            row = (await db.execute(select(BillingEvent))).scalars().one()
        assert household.paid_until is None
        assert "on_fire" in row.detail

    async def test_no_status_at_all_is_refused_too(self, client, paddle, sessions):
        await register(client)
        target = await household_id(sessions)
        assert await webhook(client, paddle_event(target, status=None), post=paddle_post) == "refused"


class TestAnEntitlementFollowsOneSubscription:
    """Only the subscription an entitlement came from can move it, and only in
    the order the processor said things. Before this, the end of *any*
    subscription ended the household's year, and a retry from before a
    cancellation granted it back."""

    async def test_ending_a_second_subscription_leaves_the_first_ones_year(self, client, stripe, sessions):
        await register(client)
        target = await household_id(sessions)
        first = stripe_event(target, kind="customer.subscription.created", event_id="evt_a", subscription="sub_a")
        assert await webhook(client, first) == "granted"

        # A second checkout paid for the same household: somebody is paying
        # twice, which is loud, and it is not credited over the first.
        second = stripe_event(target, kind="customer.subscription.created", event_id="evt_b", subscription="sub_b")
        assert await webhook(client, second) == "refused"
        ended = stripe_event(
            target, kind="customer.subscription.deleted", event_id="evt_b_end", subscription="sub_b", status="canceled"
        )
        assert await webhook(client, ended) == "ignored"

        async with sessions() as db:
            household = await db.get(Household, target)
            refused = (await db.execute(select(BillingEvent).where(BillingEvent.event_id == "evt_b"))).scalar_one()
        assert entitlements.state(household) == entitlements.PAID
        assert _stamp(household.paid_until) == 1_818_000_000
        assert household.billing_subscription_id == "sub_a"
        assert "paying twice" in refused.detail

    async def test_a_retry_from_before_a_cancellation_does_not_grant_the_year_back(self, client, stripe, sessions):
        await register(client)
        target = await household_id(sessions)
        now = datetime.now(UTC)
        ended_at = now - timedelta(hours=1)
        created = stripe_event(
            target, kind="customer.subscription.created", event_id="evt_1", at=now - timedelta(hours=2)
        )
        deleted = stripe_event(
            target, kind="customer.subscription.deleted", event_id="evt_3", status="canceled", at=ended_at
        )
        assert await webhook(client, created) == "granted"
        assert await webhook(client, deleted) == "revoked"

        # An `updated` whose first delivery failed, retried after the deletion.
        # It keeps its own time, and that time is older than the ending.
        retried = stripe_event(target, event_id="evt_2", at=now - timedelta(minutes=90))
        assert await webhook(client, retried) == "ignored"
        # And one stamped later is no better: an ended subscription does not
        # come back, whatever the clock says.
        later = stripe_event(target, event_id="evt_4", at=now)
        assert await webhook(client, later) == "ignored"

        async with sessions() as db:
            household = await db.get(Household, target)
        assert _stamp(household.paid_until) == int(ended_at.timestamp())
        assert entitlements.state(household) == entitlements.GRACE

    async def test_events_delivered_out_of_order_do_not_rewind(self, client, paddle, sessions):
        await register(client)
        target = await household_id(sessions)
        now = datetime.now(UTC)
        newer = paddle_event(
            target,
            kind="subscription.updated",
            event_id="evt_2",
            ends="2028-08-22T00:00:00Z",
            at=now - timedelta(minutes=1),
        )
        older = paddle_event(target, event_id="evt_1", ends="2027-08-22T00:00:00Z", at=now - timedelta(minutes=10))
        assert await webhook(client, newer, post=paddle_post) == "granted"
        assert await webhook(client, older, post=paddle_post) == "ignored"
        async with sessions() as db:
            assert entitlements.describe(await db.get(Household, target)).paid_until.year == 2028

    async def test_an_incomplete_snapshot_from_the_same_second_does_not_undo_a_payment(self, client, stripe, sessions):
        """At checkout Stripe stamps `created` (incomplete) and `updated`
        (active) within the same second, and delivers them in either order."""
        await register(client)
        target = await household_id(sessions)
        same_second = datetime.now(UTC).replace(microsecond=0)
        paid = stripe_event(target, event_id="evt_active", at=same_second)
        incomplete = stripe_event(
            target, kind="customer.subscription.created", event_id="evt_incomplete", status="incomplete", at=same_second
        )
        assert await webhook(client, paid) == "granted"
        assert await webhook(client, incomplete) == "ignored"
        async with sessions() as db:
            assert _stamp((await db.get(Household, target)).paid_until) == 1_818_000_000

    async def test_a_forever_comp_is_not_the_processors_to_end(self, client, stripe, sessions):
        await register(client)
        target = await household_id(sessions)
        async with sessions() as db:
            household = await db.get(Household, target)
            await entitlements.grant(db, household, tier=limits.PAID, until=None, source=entitlements.COMP)

        ended = stripe_event(target, kind="customer.subscription.deleted", status="canceled")
        assert await webhook(client, ended) == "ignored"
        paid = stripe_event(target, event_id="evt_stripe_2", subscription="sub_new")
        assert await webhook(client, paid) == "refused"  # money spent on nothing: a human should know

        async with sessions() as db:
            household = await db.get(Household, target)
        assert (household.tier, household.paid_until, household.entitlement_source) == (
            limits.PAID,
            None,
            entitlements.COMP,
        )

    async def test_a_comp_that_has_run_out_can_be_paid_for(self, client, stripe, sessions):
        await register(client)
        target = await household_id(sessions)
        async with sessions() as db:
            household = await db.get(Household, target)
            await entitlements.grant(
                db, household, tier=limits.PAID, until=datetime.now(UTC) + timedelta(days=1), source=entitlements.COMP
            )
            household.paid_until = datetime.now(UTC) - timedelta(days=30)
            await db.commit()

        assert await webhook(client, stripe_event(target)) == "granted"
        async with sessions() as db:
            assert (await db.get(Household, target)).entitlement_source == "stripe"

    async def test_a_comp_still_notes_what_its_own_subscription_does(self, client, stripe, sessions):
        """Somebody comped while their subscription was running can cancel it
        and be let go: the comp is untouched, and the cancellation is noted."""
        await register(client)
        target = await household_id(sessions)
        assert await webhook(client, stripe_event(target, event_id="evt_1")) == "granted"
        async with sessions() as db:
            household = await db.get(Household, target)
            await entitlements.grant(db, household, tier=limits.PAID, until=None, source=entitlements.COMP)

        assert await webhook(client, stripe_event(target, event_id="evt_2", cancelling=True)) == "ignored"
        async with sessions() as db:
            household = await db.get(Household, target)
        assert household.paid_until is None and household.entitlement_source == entitlements.COMP
        assert household.billing_subscription_state == entitlements.CANCELLED
        assert entitlements.charges_again(household) is False

    async def test_a_payment_from_before_subscriptions_were_tracked_is_adopted(self, client, stripe, sessions):
        """Nothing in the database said which subscription an older payment
        was, so the next event about one is about that one."""
        await register(client)
        target = await household_id(sessions)
        async with sessions() as db:
            household = await db.get(Household, target)
            household.tier, household.entitlement_source = limits.PAID, "stripe"
            household.paid_until = datetime.now(UTC) + timedelta(days=200)
            await db.commit()

        ended = stripe_event(target, kind="customer.subscription.deleted", subscription="sub_legacy", status="canceled")
        assert await webhook(client, ended) == "revoked"
        async with sessions() as db:
            household = await db.get(Household, target)
        assert household.billing_subscription_id == "sub_legacy"
        assert entitlements.state(household) == entitlements.GRACE

    async def test_but_not_one_belonging_to_another_customer(self, client, stripe, sessions):
        """Where the household's customer is known, an ending for somebody
        else's subscription is not a guess worth a paid year."""
        await register(client)
        target = await household_id(sessions)
        paid_until = datetime.now(UTC) + timedelta(days=200)
        async with sessions() as db:
            household = await db.get(Household, target)
            household.tier, household.entitlement_source = limits.PAID, "stripe"
            household.paid_until, household.billing_customer_id = paid_until, "cus_theirs"
            await db.commit()

        stranger = stripe_event(
            target, kind="customer.subscription.deleted", subscription="sub_other", status="canceled"
        )
        stranger["data"]["object"]["customer"] = "cus_somebody_else"
        assert await webhook(client, stranger) == "ignored"
        async with sessions() as db:
            assert _stamp((await db.get(Household, target)).paid_until) == int(paid_until.timestamp())


class TestAnEndingKeepsTheGracePeriod:
    """TERMS, "Cancelling, and what happens next": 14 days' grace after the paid
    year ends, then the free limits, and dunning's one email after expiry. An
    ending used to wipe the row instead, which skipped all three."""

    async def test_a_cancellation_runs_the_grace_period(self, client, stripe, sessions):
        await register(client)
        target = await household_id(sessions)
        paid = stripe_event(target, event_id="evt_1", at=datetime.now(UTC) - timedelta(days=1))
        assert await webhook(client, paid) == "granted"
        ended_at = datetime.now(UTC) - timedelta(minutes=5)
        deleted = stripe_event(
            target, kind="customer.subscription.deleted", event_id="evt_2", status="canceled", at=ended_at
        )
        assert await webhook(client, deleted) == "revoked"

        async with sessions() as db:
            household = await db.get(Household, target)
        entitlement = entitlements.describe(household)
        assert household.tier == limits.PAID  # kept, so it lapses like any year
        assert entitlement.state == entitlements.GRACE
        assert _stamp(household.paid_until) == int(ended_at.timestamp())
        assert limits.effective_tier(household) == limits.PAID
        assert limits.effective_tier(household, now=entitlement.grace_ends_at + timedelta(minutes=1)) == limits.FREE

    async def test_and_dunning_sends_the_lapse_email(self, client, stripe, sessions):
        await register(client, email="lead@example.com")
        target = await household_id(sessions)
        assert await webhook(client, stripe_event(target, event_id="evt_1")) == "granted"
        deleted = stripe_event(target, kind="customer.subscription.deleted", event_id="evt_2", status="canceled")
        assert await webhook(client, deleted) == "revoked"

        async with sessions() as db:
            notices = await dunning.due(db)
        assert [(notice.kind, notice.to) for notice in notices] == [(dunning.LAPSE, "lead@example.com")]

    async def test_an_ending_after_the_year_ran_out_changes_nothing(self, client, stripe, sessions):
        """Stripe ends a subscription whose card never recovered weeks after the
        date. That must not move the expiry, nor send the lapse email twice."""
        await register(client)
        target = await household_id(sessions)
        assert await webhook(client, stripe_event(target, event_id="evt_1")) == "granted"
        expired, notified = datetime.now(UTC) - timedelta(days=20), datetime.now(UTC) - timedelta(days=19)
        async with sessions() as db:
            household = await db.get(Household, target)
            household.paid_until, household.lapse_notified_at = expired, notified
            await db.commit()

        deleted = stripe_event(target, kind="customer.subscription.deleted", event_id="evt_2", status="canceled")
        assert await webhook(client, deleted) == "revoked"
        async with sessions() as db:
            household = await db.get(Household, target)
        assert _stamp(household.paid_until) == int(expired.timestamp())
        assert _stamp(household.lapse_notified_at) == int(notified.timestamp())

    async def test_an_ending_after_an_operator_revoke_sends_nothing(self, client, stripe, sessions):
        """The operator got there first and the household is on the free tier
        with no expiry. The processor's ending must not hand it a date, and with
        it an email about a year it no longer has."""
        await register(client)
        target = await household_id(sessions)
        paid = stripe_event(target, event_id="evt_1", at=datetime.now(UTC) - timedelta(days=1))
        assert await webhook(client, paid) == "granted"
        async with sessions() as db:
            await entitlements.revoke(db, await db.get(Household, target))

        deleted = stripe_event(target, kind="customer.subscription.deleted", event_id="evt_2", status="canceled")
        assert await webhook(client, deleted) == "revoked"
        async with sessions() as db:
            household = await db.get(Household, target)
            assert await dunning.due(db) == []
        assert (household.tier, household.paid_until) == (limits.FREE, None)
        assert household.billing_subscription_state == entitlements.ENDED

    async def test_the_operators_revoke_is_still_immediate(self, client, stripe, sessions):
        """`python -m app.entitlements revoke` is for ending a comp or a
        suspension, where "now" is the point; it keeps its meaning."""
        await register(client)
        target = await household_id(sessions)
        assert await webhook(client, stripe_event(target)) == "granted"
        async with sessions() as db:
            revoked = await entitlements.revoke(db, await db.get(Household, target))
        assert (revoked.stored_tier, revoked.state) == (limits.FREE, entitlements.PERMANENT)


class TestEventsForHouseholdsThatAreNotHere:
    """The ledger's household column is a foreign key. An ignored event naming
    a household this server does not have was written into it, failed, and
    answered an uncounted 500 that the processor retried until it gave up on
    the endpoint."""

    async def test_an_ignored_event_for_a_missing_household_is_recorded_not_a_500(self, client, paddle, sessions):
        await register(client)
        missing = uuid.uuid4()
        body, headers = paddle_post(paddle_event(missing, kind="customer.updated"))
        response = await client.post("/billing/webhook", content=body, headers=headers)
        assert response.status_code == 200
        assert response.json()["outcome"] == "ignored"
        async with sessions() as db:
            row = (await db.execute(select(BillingEvent))).scalars().one()
        assert row.household_id is None
        assert str(missing) in row.detail

    async def test_an_ending_for_a_household_that_is_gone_does_not_page_anyone(self, client, stripe, sessions):
        """Nothing to take away and nobody to credit: the orphan alert is for
        payments, and this is what follows a household deleting itself."""
        await register(client)
        ended = stripe_event(uuid.uuid4(), kind="customer.subscription.deleted", status="canceled")
        assert await webhook(client, ended) == "ignored"

    async def test_a_payment_for_one_is_still_an_orphan(self, client, stripe, sessions):
        await register(client)
        assert await webhook(client, stripe_event(uuid.uuid4())) == "orphan"


class TestEventsThatNameNoPeriodAreNotPayments:
    """Two mappings that could never grant: a Paddle transaction calls its
    period `billing_period`, and a Lemon Squeezy payment carries the invoice
    rather than the subscription. Both were refused as having no period end,
    which fired the paid-but-not-credited alert on every successful payment.
    The subscription's own update is what credits the year."""

    async def test_a_paddle_transaction_is_not_refused(self, client, paddle, sessions):
        await register(client)
        target = await household_id(sessions)
        transaction = {
            "event_id": "evt_txn",
            "event_type": "transaction.completed",
            "occurred_at": _iso(None),
            "data": {
                "id": "txn_01",
                "subscription_id": "sub_01paddle",
                "status": "completed",
                "billing_period": {"starts_at": "2026-08-22T00:00:00Z", "ends_at": "2027-08-22T00:00:00Z"},
                "custom_data": {"household_id": str(target)},
            },
        }
        assert await webhook(client, transaction, post=paddle_post) == "ignored"

    async def test_a_lemonsqueezy_payment_is_not_refused(self, client, lemonsqueezy, sessions):
        await register(client)
        target = await household_id(sessions)
        invoice = {
            "meta": {"event_name": "subscription_payment_success", "custom_data": {"household_id": str(target)}},
            "data": {
                "type": "subscription-invoices",
                "id": "99",
                "attributes": {"subscription_id": 1, "status": "paid", "total": 2000, "updated_at": _iso(None)},
            },
        }
        body, headers = lemon_post(invoice, event="subscription_payment_success")
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "ignored"


class TestOnlyTheSignedEventNameIsBelieved:
    """Lemon Squeezy repeats the event name in `X-Event-Name`, outside the
    signature. It used to win over the signed `meta.event_name`, so anybody
    holding one signed body could replay it as a different event."""

    async def test_a_header_that_disagrees_with_the_signed_body_is_refused(self, client, lemonsqueezy, sessions):
        await register(client)
        target = await household_id(sessions)
        body, headers = lemon_post(lemon_event(target), event="subscription_created")
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "granted"

        # A signed `subscription_created` body, relabelled as an expiry.
        replayed, replay_headers = lemon_post(
            lemon_event(target, at=datetime.now(UTC) + timedelta(seconds=1)), event="subscription_expired"
        )
        response = await client.post("/billing/webhook", content=replayed, headers=replay_headers)
        assert response.status_code == 400
        assert "signed" in response.json()["detail"]
        async with sessions() as db:
            household = await db.get(Household, target)
            rows = (await db.execute(select(BillingEvent))).scalars().all()
        assert entitlements.state(household) == entitlements.PAID
        assert len(rows) == 1  # only the real one

    async def test_a_body_with_no_name_of_its_own_is_not_named_by_the_header(self, client, lemonsqueezy, sessions):
        await register(client)
        target = await household_id(sessions)
        payload = lemon_event(target)
        del payload["meta"]["event_name"]
        body, headers = lemon_post(payload, event="subscription_created")
        assert (await client.post("/billing/webhook", content=body, headers=headers)).status_code == 400
        async with sessions() as db:
            assert (await db.get(Household, target)).paid_until is None


KEY = "sk_test_not_a_real_key"
MANAGE_URL = "https://billing.example.com/p/login/test"


@pytest.fixture
def selling(settings_override):
    """Stripe with a key that can open sessions: the hosted deployment."""
    settings_override(
        BILLING_PROCESSOR="stripe",
        BILLING_WEBHOOK_SECRET=SECRET,
        DEFAULT_HOUSEHOLD_TIER="free",
        BILLING_API_KEY=KEY,
        BILLING_PRICE_ID="price_founding_year",
        BILLING_MANAGE_URL=MANAGE_URL,
    )


def as_(client, auth: dict):
    client.headers["Authorization"] = f"Bearer {auth['token']}"
    return client


async def two_members(client) -> tuple[dict, dict]:
    """Marcus leads and pays; Sam joined by invite."""
    marcus = await register(client, email="marcus@example.com", name="Marcus")
    invite = (await as_(client, marcus).post("/auth/invites", json={"expires_in_days": 7})).json()
    sam = await register(client, email="sam@example.com", name="Sam", invite_code=invite["code"])
    return marcus, sam


async def pays(client, household: uuid.UUID, payer: dict, *, event_id="evt_paid", at=None, cancelling=False) -> None:
    event = stripe_event(
        household,
        event_id=event_id,
        payer=uuid.UUID(payer["user"]["id"]),
        at=at or datetime.now(UTC) - timedelta(days=1),
        cancelling=cancelling,
    )
    event["data"]["object"]["customer"] = "cus_marcus"
    assert await webhook(client, event) == "granted"


async def cancels(client, household: uuid.UUID, payer: dict) -> None:
    """What cancelling in the portal sends: the same subscription, still paid
    through its period, now set to end there."""
    await pays(client, household, payer, event_id="evt_cancelled", at=datetime.now(UTC), cancelling=True)


class TestBillingBelongsToThePayer:
    """The portal shows one person's card, address and invoices, and can cancel.
    It used to open for whoever led the household at the time, and nothing
    stopped the payer leaving, being removed or deleting their account with the
    card still renewing."""

    async def test_the_webhook_records_whose_card_it_is(self, client, selling, sessions):
        marcus, _ = await two_members(client)
        target = await household_id(sessions)
        await pays(client, target, marcus)

        body = (await as_(client, marcus).get("/billing/subscription")).json()
        assert body["payer_user_id"] == marcus["user"]["id"]
        assert body["renews"] is True

    @respx.mock
    async def test_only_the_payer_opens_the_portal(self, client, selling, sessions):
        route = respx.post("https://api.stripe.com/v1/billing_portal/sessions").mock(
            return_value=httpx.Response(200, json={"url": "https://billing.stripe.com/session/marcus"})
        )
        marcus, sam = await two_members(client)
        target = await household_id(sessions)
        await pays(client, target, marcus)
        await cancels(client, target, marcus)
        handed = await as_(client, marcus).patch("/auth/household", json={"lead_user_id": sam["user"]["id"]})
        assert handed.status_code == 200, handed.text

        # Sam leads now, and still may not see Marcus's card.
        refused = await as_(client, sam).post("/billing/portal")
        assert refused.status_code == 403
        assert "Marcus" in refused.json()["detail"]
        assert not route.called

        opened = await as_(client, marcus).post("/billing/portal")
        assert opened.status_code == 200
        assert opened.json()["url"] == "https://billing.stripe.com/session/marcus"

    async def test_a_lead_who_pays_may_not_hand_over_while_it_renews(self, client, selling, sessions):
        marcus, sam = await two_members(client)
        target = await household_id(sessions)
        await pays(client, target, marcus)

        response = await as_(client, marcus).patch("/auth/household", json={"lead_user_id": sam["user"]["id"]})
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "renews on" in detail and "Manage billing" in detail
        assert (await client.get("/auth/household")).json()["lead_user_id"] == marcus["user"]["id"]

    async def test_the_payer_may_not_delete_their_account_while_it_renews(self, client, selling, sessions):
        marcus, _ = await two_members(client)
        target = await household_id(sessions)
        await pays(client, target, marcus)

        response = await as_(client, marcus).request("DELETE", "/auth/me", json={"password": "a-strong-password"})
        assert response.status_code == 409
        assert "nothing was deleted" in response.json()["detail"]
        assert (await client.get("/auth/me")).status_code == 200

    async def test_a_payer_who_does_not_lead_may_not_leave_or_be_removed_while_it_renews(
        self, client, selling, sessions
    ):
        marcus, sam = await two_members(client)
        target = await household_id(sessions)
        await pays(client, target, marcus)
        await cancels(client, target, marcus)
        await as_(client, marcus).patch("/auth/household", json={"lead_user_id": sam["user"]["id"]})
        # And then un-cancels it in the portal, so it renews on Marcus's card
        # while Sam leads.
        await pays(client, target, marcus, event_id="evt_resumed", at=datetime.now(UTC) + timedelta(seconds=1))

        leaving = await as_(client, marcus).delete(f"/auth/household/members/{marcus['user']['id']}")
        assert leaving.status_code == 409
        assert "your card" in leaving.json()["detail"]
        removing = await as_(client, sam).delete(f"/auth/household/members/{marcus['user']['id']}")
        assert removing.status_code == 409
        assert "Marcus's card" in removing.json()["detail"]

        elsewhere = await register(client, email="alex@example.com", name="Alex")
        invite = (await as_(client, elsewhere).post("/auth/invites", json={"expires_in_days": 7})).json()
        joining = await as_(client, marcus).post("/auth/invites/redeem", json={"code": invite["code"]})
        assert joining.status_code == 409
        assert "your card" in joining.json()["detail"]

    async def test_once_it_is_cancelled_the_payer_is_free_to_go(self, client, selling, sessions):
        marcus, sam = await two_members(client)
        target = await household_id(sessions)
        await pays(client, target, marcus)
        await cancels(client, target, marcus)

        deleted = await as_(client, marcus).request("DELETE", "/auth/me", json={"password": "a-strong-password"})
        assert deleted.status_code == 200, deleted.text
        async with sessions() as db:
            household = await db.get(Household, target)
        assert household.billing_user_id is None
        assert household.lead_user_id == uuid.UUID(sam["user"]["id"])

        # Sam leads now, and gets the processor's own login page rather than a
        # session into somebody else's card.
        portal = await as_(client, sam).post("/billing/portal")
        assert portal.json()["url"] == MANAGE_URL

        # And the year still ends with its grace period when Stripe says so.
        ended = stripe_event(
            target, kind="customer.subscription.deleted", event_id="evt_end", status="canceled", at=datetime.now(UTC)
        )
        assert await webhook(client, ended) == "revoked"

    async def test_the_last_member_deleting_after_cancelling_leaves_no_alarm_behind(self, client, selling, sessions):
        marcus = await register(client, email="marcus@example.com", name="Marcus")
        target = await household_id(sessions)
        await pays(client, target, marcus)
        await cancels(client, target, marcus)
        deleted = await as_(client, marcus).request("DELETE", "/auth/me", json={"password": "a-strong-password"})
        assert deleted.json()["household_deleted"] is True

        ended = stripe_event(target, kind="customer.subscription.deleted", event_id="evt_end", status="canceled")
        assert await webhook(client, ended) == "ignored"

    async def test_a_payment_from_before_tracking_is_assumed_to_renew(self, client, selling, sessions):
        """Nothing here says whether it still renews. Assuming it does costs a
        visit to Manage billing; assuming it does not could cost a year's
        charge for a household somebody has left."""
        marcus = await register(client, email="marcus@example.com", name="Marcus")
        target = await household_id(sessions)
        async with sessions() as db:
            household = await db.get(Household, target)
            household.tier, household.entitlement_source = limits.PAID, "stripe"
            household.paid_until = datetime.now(UTC) + timedelta(days=200)
            household.billing_user_id = uuid.UUID(marcus["user"]["id"])  # the migration's backfill
            await db.commit()

        response = await as_(client, marcus).request("DELETE", "/auth/me", json={"password": "a-strong-password"})
        assert response.status_code == 409
