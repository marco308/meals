"""The /metrics endpoint and the counters behind it (app/metrics.py)."""

import re

from sqlalchemy.ext.asyncio import async_sessionmaker

from app import metrics as metrics_module
from tests.conftest import create_recipe

SCRAPE = {"Authorization": "Bearer scrape-secret-1"}


async def test_metrics_absent_without_a_token(client):
    response = await client.get("/metrics")
    assert response.status_code == 404


async def test_metrics_requires_the_exact_bearer(client, settings_override):
    settings_override(METRICS_TOKEN="scrape-secret-1")
    assert (await client.get("/metrics")).status_code == 401
    assert (await client.get("/metrics", headers={"Authorization": "Bearer wrong"})).status_code == 401
    response = await client.get("/metrics", headers=SCRAPE)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "meals_http_requests_total" in response.text


async def test_a_stray_byte_in_the_bearer_is_a_401_not_a_500(client, settings_override):
    """compare_digest raises on a str with anything outside ASCII in it."""
    settings_override(METRICS_TOKEN="scrape-secret-1")
    response = await client.get("/metrics", headers={"Authorization": b"Bearer scrape-secret-\xe9"})
    assert response.status_code == 401


async def test_a_token_outside_ascii_still_opens_it(client, settings_override):
    settings_override(METRICS_TOKEN="scrape-sécret")
    on_the_wire = "Bearer scrape-sécret".encode()  # UTF-8, as a scraper sends it
    assert (await client.get("/metrics", headers={"Authorization": on_the_wire})).status_code == 200


async def test_requests_are_counted_by_route_template(auth_client, settings_override):
    settings_override(METRICS_TOKEN="scrape-secret-1")
    recipe = await create_recipe(auth_client)
    await auth_client.get(f"/recipes/{recipe['id']}")
    body = (await auth_client.get("/metrics", headers=SCRAPE)).text
    assert 'route="/recipes/{recipe_id}"' in body
    assert recipe["id"] not in body  # raw paths never become label values
    assert "meals_http_request_duration_seconds_bucket" in body


async def test_caller_chosen_labels_are_folded_into_fixed_sets(client, settings_override):
    """The platform comes from an unauthenticated header and the method is any
    token HTTP allows; each distinct value used to be a new timeseries."""
    settings_override(METRICS_TOKEN="scrape-secret-1")
    for n in range(3):
        await client.get("/no-such-page", headers={"X-Meals-Client": f"scanner{n}/1.0 (1)"})
    await client.get("/no-such-page", headers={"X-Meals-Client": "ios/1.1 (24)"})
    await client.get("/no-such-page", headers={"X-Meals-Client": "web/1.0 (1)"})
    await client.get("/no-such-page")
    await client.request("FROBNICATE", "/no-such-page")
    body = (await client.get("/metrics", headers=SCRAPE)).text
    samples = [line for line in body.splitlines() if line.startswith("meals_http_requests_total{")]
    platforms = {re.search(r'client_platform="([^"]*)"', line)[1] for line in samples}
    methods = {re.search(r'method="([^"]*)"', line)[1] for line in samples}
    assert platforms == {"ios", "web", "other", "none"}
    assert "OTHER" in methods
    assert methods <= {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "OTHER"}
    assert "scanner" not in body and "FROBNICATE" not in body


async def test_healthy_healthz_is_not_counted(client, settings_override):
    settings_override(METRICS_TOKEN="scrape-secret-1")
    await client.get("/healthz")
    body = (await client.get("/metrics", headers=SCRAPE)).text
    assert 'route="/healthz"' not in body


async def test_events_become_counters(client, settings_override):
    settings_override(METRICS_TOKEN="scrape-secret-1")
    await client.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"})
    body = (await client.get("/metrics", headers=SCRAPE)).text
    assert 'event="auth.login_failed"' in body


async def test_usage_gauges_count_the_whole_server(engine, auth_client, settings_override):
    settings_override(METRICS_TOKEN="scrape-secret-1")
    await create_recipe(auth_client)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await metrics_module.refresh_usage_gauges(session)
    body = (await auth_client.get("/metrics", headers=SCRAPE)).text
    assert "meals_households_total 1.0" in body
    assert "meals_users_total 1.0" in body
    assert "meals_recipes_total 1.0" in body
