"""Request ids, the access log, the last-resort 500, and domain events
(app/observability.py). Assertions are on log records via caplog — the wire
format is unit-tested separately."""

import logging

import pytest

from app import deps
from tests.conftest import create_recipe, register


@pytest.fixture(autouse=True)
def capture_info(caplog):
    caplog.set_level(logging.INFO)
    return caplog


def logged(caplog, logger: str, message: str | None = None) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == logger and (message is None or r.getMessage().startswith(message))]


# ---------------------------------------------------------------- request ids


async def test_every_response_carries_a_request_id(client):
    response = await client.get("/")
    generated = response.headers["X-Request-ID"]
    int(generated, 16)  # minted ids are hex
    assert len(generated) == 16


async def test_inbound_request_id_is_honoured(client):
    response = await client.get("/", headers={"X-Request-ID": "proxy-abc.123_x"})
    assert response.headers["X-Request-ID"] == "proxy-abc.123_x"


async def test_hostile_inbound_request_id_is_replaced(client):
    # Header-unsafe ids are dropped, not rejected: the id is a courtesy.
    response = await client.get("/", headers={"X-Request-ID": "spaces and <angles>"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "spaces and <angles>"


# ---------------------------------------------------------------- access log


async def test_one_access_line_per_request(client, caplog):
    await client.get("/", headers={"X-Meals-Client": "web/1.0 (1)"})
    [record] = logged(caplog, "meals.http")
    assert record.method == "GET"
    assert record.path == "/"
    assert record.route == "/"
    assert record.status == 200
    assert record.duration_ms >= 0
    assert record.request_id
    assert record.client_platform == "web"
    assert record.client_build == 1


async def test_route_template_not_raw_path_identifies_the_endpoint(auth_client, caplog):
    recipe = await create_recipe(auth_client)
    await auth_client.get(f"/recipes/{recipe['id']}")
    [record] = logged(caplog, "meals.http", "GET /recipes/")
    assert record.route == "/recipes/{recipe_id}"
    assert record.path == f"/recipes/{recipe['id']}"


async def test_healthy_healthz_is_not_logged(client, caplog):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]  # still correlatable, just quiet
    assert logged(caplog, "meals.http") == []


async def test_access_log_names_the_acting_user(auth_client, caplog):
    me = (await auth_client.get("/auth/me")).json()
    [record] = logged(caplog, "meals.http", "GET /auth/me")
    assert str(record.user_id) == me["id"]
    assert str(record.household_id) == me["household_id"]


# ---------------------------------------------------------------- unhandled errors


async def test_unhandled_error_returns_500_quoting_the_request_id(auth_client, caplog, monkeypatch):
    from app.routers import recipes as recipes_router

    def boom(sort: str) -> tuple:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(recipes_router, "_sort_order", boom)
    response = await auth_client.get("/recipes")
    assert response.status_code == 500
    request_id = response.headers["X-Request-ID"]
    assert request_id in response.json()["detail"]

    [error] = logged(caplog, "meals.error")
    assert error.request_id == request_id
    assert error.exc_info[0] is RuntimeError
    [access] = logged(caplog, "meals.http", "GET /recipes")
    assert access.status == 500
    assert access.levelno == logging.ERROR


# ---------------------------------------------------------------- domain events


async def test_registration_and_invite_redemption_events(client, caplog):
    auth = await register(client)
    client.headers["Authorization"] = f"Bearer {auth['token']}"
    [registered] = logged(caplog, "meals.events", "user.registered")
    assert str(registered.household_id) == auth["user"]["household_id"]
    assert registered.joined_existing is False

    invite = (await client.post("/auth/invites", json={})).json()
    [created] = logged(caplog, "meals.events", "invite.created")
    assert str(created.invite_id) == invite["id"]

    await register(client, email="partner@example.com", name="Partner", invite_code=invite["code"])
    joined = logged(caplog, "meals.events", "user.registered")[-1]
    assert joined.joined_existing is True
    assert str(joined.household_id) == auth["user"]["household_id"]


async def test_failed_login_event_carries_no_email(client, caplog):
    response = await client.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"})
    assert response.status_code == 401
    [event] = logged(caplog, "meals.events", "auth.login_failed")
    assert "nobody@example.com" not in repr(vars(event))


async def test_rate_limit_trip_event(client, caplog, settings_override):
    deps._attempts.clear()
    settings_override(AUTH_RATE_LIMIT_PER_MINUTE="1")
    payload = {"email": "nobody@example.com", "password": "wrong-password-1"}
    try:
        await client.post("/auth/login", json=payload)
        response = await client.post("/auth/login", json=payload)
        assert response.status_code == 429
        [event] = logged(caplog, "meals.events", "auth.rate_limited")
        assert event.path == "/auth/login"
    finally:
        deps._attempts.clear()


async def test_account_deletion_event(auth_client, caplog):
    response = await auth_client.request("DELETE", "/auth/me", json={"password": "a-strong-password"})
    assert response.status_code == 200, response.text
    [event] = logged(caplog, "meals.events", "user.deleted")
    assert event.household_deleted is True


async def test_gate_rejections_are_visible(client, caplog, settings_override):
    settings_override(MIN_IOS_BUILD="5")
    response = await client.get("/recipes", headers={"X-Meals-Client": "ios/1.0 (2)"})
    assert response.status_code == 426
    [event] = logged(caplog, "meals.events", "client.gated")
    assert event.build == 2
    assert event.min_build == 5
    [access] = logged(caplog, "meals.http", "GET /recipes")
    assert access.status == 426
    assert access.client_platform == "ios"


async def test_ingest_events_log_host_and_outcome_never_the_full_url(auth_client, caplog):
    url = "https://example.com/recipes/spag-bol-42"
    await create_recipe(auth_client, source_url=url)
    response = await auth_client.post("/recipes/ingest", json={"url": url})
    assert response.status_code == 200
    assert response.json()["cached"] is True
    [event] = logged(caplog, "meals.events", "recipe.ingested")
    assert event.outcome == "cached"
    assert event.host == "example.com"
    assert url not in repr(vars(event))


async def test_ai_parsed_recipes_close_the_ingest_funnel(auth_client, caplog):
    await create_recipe(auth_client, source_url="https://example.com/paywalled/pie", parse_source="ai")
    [event] = logged(caplog, "meals.events", "recipe.ingested")
    assert event.outcome == "ai_parsed"
    assert event.host == "example.com"
