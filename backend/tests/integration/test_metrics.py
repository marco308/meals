"""The /metrics endpoint and the counters behind it (app/metrics.py)."""

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


async def test_requests_are_counted_by_route_template(auth_client, settings_override):
    settings_override(METRICS_TOKEN="scrape-secret-1")
    recipe = await create_recipe(auth_client)
    await auth_client.get(f"/recipes/{recipe['id']}")
    body = (await auth_client.get("/metrics", headers=SCRAPE)).text
    assert 'route="/recipes/{recipe_id}"' in body
    assert recipe["id"] not in body  # raw paths never become label values
    assert "meals_http_request_duration_seconds_bucket" in body


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
