"""Starting a checkout, and reading what a household is subscribed to (#121).

#99 built everything that happens *after* a payment. This is the half before it,
and the claims worth the test are the ones that cost money to get wrong:

1. **None of it exists unless a deployment turns it on**, and the checkout needs
   turning on twice over: a server can take webhooks without holding a key that
   can charge anybody.
2. **The household id rides where the webhook will look for it.** That thread is
   the only one tying a payment back to an account; break it and somebody pays
   and is credited to nobody.
3. **`managed_payments[enabled]` is on the Stripe request.** Without it the
   payment still works and Stripe is no longer the merchant of record, which
   silently moves the EU VAT onto us — the exact thing §7 picked an MoR to
   avoid, and the failure leaves no trace anywhere else.
4. **Starting a checkout grants nothing.** Only a verified webhook carrying a
   billing period end does that.

The three request shapes were read from the live documentation on 2026-08-23:
Stripe's Checkout Session create, Paddle's create-transaction, and Lemon
Squeezy's create-checkout.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import limits
from app.models import Household
from app.services import entitlements
from tests.conftest import register

SECRET = "a-signing-secret"
KEY = "sk_test_not_a_real_key"


@pytest.fixture
def sessions(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def webhook_only(settings_override):
    """A server that can be *told* about payments but cannot start one."""
    settings_override(BILLING_PROCESSOR="stripe", BILLING_WEBHOOK_SECRET=SECRET)


@pytest.fixture
def stripe(settings_override):
    settings_override(
        BILLING_PROCESSOR="stripe",
        BILLING_WEBHOOK_SECRET=SECRET,
        DEFAULT_HOUSEHOLD_TIER="free",
        BILLING_API_KEY=KEY,
        BILLING_PRICE_ID="price_founding_year",
        BILLING_MANAGE_URL="https://billing.example.com/p/login/test",
        BILLING_PRICE_PENCE="2000",
    )


@pytest.fixture
def paddle(settings_override):
    settings_override(
        BILLING_PROCESSOR="paddle",
        BILLING_WEBHOOK_SECRET=SECRET,
        DEFAULT_HOUSEHOLD_TIER="free",
        BILLING_API_KEY=KEY,
        BILLING_PRICE_ID="pri_01example",
    )


@pytest.fixture
def lemonsqueezy(settings_override):
    settings_override(
        BILLING_PROCESSOR="lemonsqueezy",
        BILLING_WEBHOOK_SECRET=SECRET,
        DEFAULT_HOUSEHOLD_TIER="free",
        BILLING_API_KEY=KEY,
        BILLING_PRICE_ID="998877",
        BILLING_STORE_ID="12345",
    )


async def only_household(sessions) -> Household:
    async with sessions() as db:
        return (await db.execute(select(Household))).scalars().one()


# ------------------------------------------------------------------ it is off


async def test_neither_route_exists_on_a_server_that_sells_nothing(auth_client):
    """The default, and every self-hosted instance. Not 403: not there."""
    assert (await auth_client.get("/billing/subscription")).status_code == 404
    assert (await auth_client.post("/billing/checkout")).status_code == 404


async def test_a_webhook_alone_does_not_open_a_checkout(webhook_only, auth_client):
    """Receiving payments and taking them are separate switches, and the safer
    one can be on alone."""
    subscription = await auth_client.get("/billing/subscription")
    assert subscription.status_code == 200
    assert subscription.json()["can_checkout"] is False
    assert (await auth_client.post("/billing/checkout")).status_code == 404


async def test_client_config_says_whether_this_server_sells(stripe, client):
    """One answer to "is there anything to buy here", unauthenticated because a
    signup page has nobody to log in as yet."""
    assert (await client.get("/client-config")).json()["billing_enabled"] is True


async def test_client_config_sells_nothing_by_default(client):
    assert (await client.get("/client-config")).json()["billing_enabled"] is False


# ------------------------------------------------------------- what it reports


async def test_subscription_reports_a_household_that_has_paid_for_nothing(stripe, auth_client):
    body = (await auth_client.get("/billing/subscription")).json()
    assert body["state"] == entitlements.PERMANENT  # no expiry, so it never lapses
    assert body["paid_until"] is None
    assert body["source"] is None
    assert body["price_pence"] is None  # nothing was agreed, so nothing is snapshotted
    assert body["offer_price_pence"] == 2000  # what it would cost
    assert body["offer_price_currency"] == "GBP"
    assert body["manage_url"] == "https://billing.example.com/p/login/test"
    assert body["can_checkout"] is True


async def test_subscription_reports_a_comp(stripe, auth_client, sessions):
    """A comp is not a purchase, and says so: no processor, and an expiry that
    is still an expiry."""
    until = datetime.now(UTC) + timedelta(days=365)
    async with sessions() as db:
        household = (await db.execute(select(Household))).scalars().one()
        await entitlements.grant(db, household, tier=limits.PAID, until=until, source=entitlements.COMP)
        await db.commit()

    body = (await auth_client.get("/billing/subscription")).json()
    assert body["tier"] == limits.PAID
    assert body["state"] == entitlements.PAID
    assert body["source"] == entitlements.COMP
    assert body["paid_until"].startswith(until.strftime("%Y-%m-%d"))
    assert body["grace_ends_at"] is not None
    # Nothing to buy: a second subscription would be a second charge.
    assert body["can_checkout"] is False


# -------------------------------------------------------------- opening one


@respx.mock
async def test_stripe_checkout_carries_the_household_and_the_merchant_of_record(stripe, auth_client, sessions):
    route = respx.post("https://api.stripe.com/v1/checkout/sessions").mock(
        return_value=httpx.Response(200, json={"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"})
    )
    household = await only_household(sessions)

    response = await auth_client.post("/billing/checkout")
    assert response.status_code == 201, response.text
    assert response.json()["url"] == "https://checkout.stripe.com/c/pay/cs_test_1"

    sent = dict(part.split("=", 1) for part in route.calls.last.request.content.decode().split("&"))
    from urllib.parse import unquote_plus

    sent = {unquote_plus(k): unquote_plus(v) for k, v in sent.items()}
    assert sent["mode"] == "subscription"
    assert sent["line_items[0][price]"] == "price_founding_year"
    # On the *subscription*, because that is the object the webhook reads;
    # metadata on the session never reaches it.
    assert sent["subscription_data[metadata][household_id]"] == str(household.id)
    # The line that decides who the legal seller is. See the module docstring.
    assert sent["managed_payments[enabled]"] == "true"
    assert sent["success_url"].endswith("/app/#/settings")
    # The key authenticates and is never anywhere else.
    assert route.calls.last.request.headers["authorization"].startswith("Basic ")
    # Pinned, not inherited: `managed_payments` exists from this version on, and
    # the account default is a dashboard setting this server cannot see.
    assert route.calls.last.request.headers["stripe-version"] == "2025-03-31.basil"


@respx.mock
async def test_paddle_checkout_carries_the_household(paddle, auth_client, sessions):
    route = respx.post("https://api.paddle.com/transactions").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "txn_1", "checkout": {"url": "https://pay.example.com/?_ptxn=txn_1"}}}
        )
    )
    household = await only_household(sessions)

    response = await auth_client.post("/billing/checkout")
    assert response.status_code == 201, response.text
    assert response.json()["url"] == "https://pay.example.com/?_ptxn=txn_1"

    sent = json.loads(route.calls.last.request.content)
    assert sent["items"] == [{"price_id": "pri_01example", "quantity": 1}]
    assert sent["custom_data"]["household_id"] == str(household.id)
    assert route.calls.last.request.headers["paddle-version"] == "1"


@respx.mock
async def test_lemonsqueezy_checkout_carries_the_household(lemonsqueezy, auth_client, sessions):
    route = respx.post("https://api.lemonsqueezy.com/v1/checkouts").mock(
        return_value=httpx.Response(
            201, json={"data": {"attributes": {"url": "https://store.lemonsqueezy.com/checkout/x"}}}
        )
    )
    household = await only_household(sessions)

    response = await auth_client.post("/billing/checkout")
    assert response.status_code == 201, response.text
    assert response.json()["url"] == "https://store.lemonsqueezy.com/checkout/x"

    sent = json.loads(route.calls.last.request.content)["data"]
    assert sent["attributes"]["checkout_data"]["custom"]["household_id"] == str(household.id)
    assert sent["relationships"]["store"]["data"]["id"] == "12345"
    assert sent["relationships"]["variant"]["data"]["id"] == "998877"


@respx.mock
async def test_opening_a_checkout_grants_nothing(stripe, auth_client, sessions):
    """The whole point of the webhook is that this is not a payment."""
    respx.post("https://api.stripe.com/v1/checkout/sessions").mock(
        return_value=httpx.Response(200, json={"url": "https://checkout.stripe.com/c/pay/cs_test_1"})
    )
    assert (await auth_client.post("/billing/checkout")).status_code == 201

    household = await only_household(sessions)
    assert household.paid_until is None
    assert household.entitlement_source is None
    assert limits.effective_tier(household) == limits.FREE


# --------------------------------------------------------------- who, and when


async def test_checkout_is_the_leads_alone(stripe, client):
    """Q23: the lead is the member a household is billed to, and this is the one
    kind of thing that gates on them. The refusal names who to ask."""
    lead = await register(client, email="lead@example.com", name="Marcus")
    client.headers["Authorization"] = f"Bearer {lead['token']}"
    invite = (await client.post("/auth/invites", json={"expires_in_days": 7})).json()

    joiner = await register(client, email="other@example.com", name="Sam", invite_code=invite["code"])
    client.headers["Authorization"] = f"Bearer {joiner['token']}"

    response = await client.post("/billing/checkout")
    assert response.status_code == 403
    assert "Marcus" in response.json()["detail"]
    # And the reading half is nobody's secret: everyone can see where they stand.
    assert (await client.get("/billing/subscription")).status_code == 200


@respx.mock
async def test_checkout_refuses_a_household_that_already_has_one(stripe, auth_client, sessions):
    route = respx.post("https://api.stripe.com/v1/checkout/sessions")
    until = datetime.now(UTC) + timedelta(days=200)
    async with sessions() as db:
        household = (await db.execute(select(Household))).scalars().one()
        await entitlements.grant(db, household, tier=limits.PAID, until=until, source="stripe")
        await db.commit()

    response = await auth_client.post("/billing/checkout")
    assert response.status_code == 409
    assert "already on the paid tier" in response.json()["detail"]
    # Nothing was asked of the processor, so nothing could be charged twice.
    assert not route.called


@respx.mock
async def test_a_processor_refusal_says_nobody_was_charged(stripe, auth_client, sessions):
    """A misconfigured price is the operator's problem, and the person who
    pressed the button needs to know only that their card was not touched."""
    respx.post("https://api.stripe.com/v1/checkout/sessions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "No such price: 'price_founding_year'"}})
    )
    response = await auth_client.post("/billing/checkout")
    assert response.status_code == 502
    assert "nothing was charged" in response.json()["detail"]
    # The processor's own words stay out of it. They name price ids, and a
    # rejected request is commonly quoted back with its parameters — one of
    # which is the customer's email address, which /privacy promises this server
    # does not write down.
    assert "price_founding_year" not in response.json()["detail"]

    household = await only_household(sessions)
    assert household.paid_until is None


@respx.mock
async def test_a_processor_that_does_not_answer_is_not_a_500(stripe, auth_client):
    respx.post("https://api.stripe.com/v1/checkout/sessions").mock(side_effect=httpx.ConnectError("no route"))
    response = await auth_client.post("/billing/checkout")
    assert response.status_code == 502
    assert "nothing was charged" in response.json()["detail"]


@respx.mock
async def test_paddle_with_no_default_payment_link_is_refused_not_returned(paddle, auth_client):
    """Paddle answers 200 with no checkout url when the dashboard has no default
    payment link. Returning that as a URL would send somebody to `null`."""
    respx.post("https://api.paddle.com/transactions").mock(
        return_value=httpx.Response(200, json={"data": {"id": "txn_1", "checkout": None}})
    )
    assert (await auth_client.post("/billing/checkout")).status_code == 502


# ------------------------------------------------------- misconfiguration, loudly


def test_selling_to_households_that_start_on_the_top_tier_refuses_to_boot(monkeypatch):
    """The quiet version of this bug is a checkout every household is refused
    from, with a 409 that reads like a bug in the app rather than a setting."""
    from app.config import Settings

    for key, value in {
        "BILLING_PROCESSOR": "stripe",
        "BILLING_WEBHOOK_SECRET": SECRET,
        "BILLING_API_KEY": KEY,
        "BILLING_PRICE_ID": "price_1",
        "DEFAULT_HOUSEHOLD_TIER": "unlimited",
    }.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match="DEFAULT_HOUSEHOLD_TIER=free"):
        Settings()


def test_lemonsqueezy_without_its_store_refuses_to_boot(monkeypatch):
    """Its checkout names a store *and* a variant, and the missing one would
    surface at the first person trying to pay rather than at deploy."""
    from app.config import Settings

    for key, value in {
        "BILLING_PROCESSOR": "lemonsqueezy",
        "BILLING_WEBHOOK_SECRET": SECRET,
        "BILLING_API_KEY": KEY,
        "BILLING_PRICE_ID": "998877",
        "DEFAULT_HOUSEHOLD_TIER": "free",
    }.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match="BILLING_STORE_ID"):
        Settings()


# ------------------------------------------------------------ managing one


async def paid_by_stripe(sessions, *, customer: str | None = "cus_test_1") -> Household:
    """A household that bought something, the way the webhook leaves one."""
    async with sessions() as db:
        household = (await db.execute(select(Household))).scalars().one()
        await entitlements.grant(
            db,
            household,
            tier=limits.PAID,
            until=datetime.now(UTC) + timedelta(days=365),
            source="stripe",
            price_pence=2000,
            customer_id=customer,
        )
        await db.commit()
        return household


@respx.mock
async def test_portal_mints_a_session_for_this_household(stripe, auth_client, sessions):
    """Issue #129: the point of storing the customer id. One click into their own
    portal, rather than a login page that emails them a link."""
    route = respx.post("https://api.stripe.com/v1/billing_portal/sessions").mock(
        return_value=httpx.Response(200, json={"url": "https://billing.stripe.com/session/live_1"})
    )
    await paid_by_stripe(sessions)

    response = await auth_client.post("/billing/portal")
    assert response.status_code == 200, response.text
    assert response.json()["url"] == "https://billing.stripe.com/session/live_1"

    sent = dict(part.split("=", 1) for part in route.calls.last.request.content.decode().split("&"))
    assert sent["customer"] == "cus_test_1"
    assert route.calls.last.request.headers["stripe-version"] == "2025-03-31.basil"


async def test_a_household_that_never_paid_has_nothing_to_manage(stripe, auth_client):
    """Even with a portal URL configured. A link to a login page they have no
    account on is not a feature — it is the bug that started #129."""
    assert (await auth_client.get("/billing/subscription")).json()["can_manage"] is False
    response = await auth_client.post("/billing/portal")
    assert response.status_code == 409
    assert "no subscription to manage" in response.json()["detail"]


async def test_a_comp_is_not_something_the_processor_can_manage(stripe, auth_client, sessions):
    async with sessions() as db:
        household = (await db.execute(select(Household))).scalars().one()
        await entitlements.grant(
            db,
            household,
            tier=limits.PAID,
            until=datetime.now(UTC) + timedelta(days=365),
            source=entitlements.COMP,
        )
        await db.commit()
    assert (await auth_client.get("/billing/subscription")).json()["can_manage"] is False


async def test_a_paid_household_with_no_stored_customer_gets_the_configured_page(stripe, auth_client, sessions):
    """Old rows, and the two processors that hand out per-subscription URLs this
    server does not keep. The fallback is the honest answer there."""
    await paid_by_stripe(sessions, customer=None)
    assert (await auth_client.get("/billing/subscription")).json()["can_manage"] is True

    response = await auth_client.post("/billing/portal")
    assert response.status_code == 200
    assert response.json()["url"] == "https://billing.example.com/p/login/test"


@respx.mock
async def test_a_refused_portal_falls_back_rather_than_failing(stripe, auth_client, sessions):
    """A stale customer id is the likely cause, and a login page they can get
    into beats an error they cannot."""
    respx.post("https://api.stripe.com/v1/billing_portal/sessions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "No such customer"}})
    )
    await paid_by_stripe(sessions)

    response = await auth_client.post("/billing/portal")
    assert response.status_code == 200
    assert response.json()["url"] == "https://billing.example.com/p/login/test"


async def test_portal_is_the_leads_alone(stripe, client, sessions):
    lead = await register(client, email="lead2@example.com", name="Marcus")
    client.headers["Authorization"] = f"Bearer {lead['token']}"
    invite = (await client.post("/auth/invites", json={"expires_in_days": 7})).json()
    joiner = await register(client, email="other2@example.com", name="Sam", invite_code=invite["code"])
    client.headers["Authorization"] = f"Bearer {joiner['token']}"

    response = await client.post("/billing/portal")
    assert response.status_code == 403
    assert "Marcus" in response.json()["detail"]


async def test_neither_portal_nor_subscription_exists_without_billing(auth_client):
    assert (await auth_client.post("/billing/portal")).status_code == 404
