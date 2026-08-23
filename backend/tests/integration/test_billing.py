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
from app.services import entitlements
from tests.conftest import register

SECRET = "a-signing-secret"


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
