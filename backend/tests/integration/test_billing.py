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

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import limits
from app.models import BillingEvent, Household
from app.services import billing, entitlements
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


def paddle_event(
    household: uuid.UUID | None, *, kind="subscription.created", event_id="evt_1", ends="2027-08-22T00:00:00Z"
):
    data: dict = {"current_billing_period": {"ends_at": ends}}
    if household is not None:
        data["custom_data"] = {"household_id": str(household)}
    return {"event_id": event_id, "event_type": kind, "data": data}


def lemon_event(household: uuid.UUID | None, *, kind="subscription_created", renews="2027-08-22T00:00:00.000000Z"):
    meta: dict = {"event_name": kind}
    if household is not None:
        meta["custom_data"] = {"household_id": str(household)}
    return {"meta": meta, "data": {"type": "subscriptions", "id": "1", "attributes": {"renews_at": renews}}}


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
):
    obj: dict = {"id": "sub_1", "object": "subscription"}
    if household is not None:
        obj["metadata"] = {"household_id": str(household)}
    if period_end is not None:
        # Newer API versions carry the period on the items rather than the
        # subscription; both shapes have to work.
        if on_items:
            obj["items"] = {"data": [{"id": "si_1", "current_period_end": period_end}]}
        else:
            obj["current_period_end"] = period_end
    return {"id": event_id, "object": "event", "type": kind, "data": {"object": obj}}


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

    async def test_the_event_name_can_come_from_the_header_alone(self, client, lemonsqueezy, sessions):
        """Lemon Squeezy sends it both ways; the header is the documented one."""
        await register(client)
        target = await household_id(sessions)
        payload = lemon_event(target)
        del payload["meta"]["event_name"]
        body, headers = lemon_post(payload, event="subscription_created")
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "granted"

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
        await register(client)
        target = await household_id(sessions)
        body, headers = lemon_post(lemon_event(target), event="subscription_created")
        await client.post("/billing/webhook", content=body, headers=headers)

        ended = lemon_event(target, kind="subscription_expired")
        body, headers = lemon_post(ended, event="subscription_expired")
        assert (await client.post("/billing/webhook", content=body, headers=headers)).json()["outcome"] == "revoked"
        async with sessions() as db:
            household = await db.get(Household, target)
        assert household.tier == "free"

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
            stripe_event(target, kind="customer.subscription.deleted", event_id="evt_stripe_2")
        )
        assert (await client.post("/billing/webhook", content=ended, headers=ended_headers)).json()[
            "outcome"
        ] == "revoked"
        async with sessions() as db:
            assert (await db.get(Household, target)).tier == "free"

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
