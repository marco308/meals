"""The public demo server (services/demo.py, routers/demo.py, app/demo.py)."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Household, Recipe
from app.services import demo
from tests.conftest import register

DATA = {
    "staples": ["olive oil", "salt"],
    "verdicts": [{"name": "olive oil", "value_tier": "premium", "value_note": "the good bottle finishes salads"}],
    "recipes": [
        {
            "title": "Cottage Pie",
            "servings": 4,
            "prep_minutes": 20,
            "cook_minutes": 60,
            "instructions": "1. Brown the mince.\n2. Top with mash and bake.",
            "ingredients": [
                {"name": "minced beef", "quantity": 500, "unit": "g"},
                {"name": "potato", "quantity": 1, "unit": "kg"},
                {"name": "olive oil", "quantity": 1, "unit": "tbsp"},
            ],
        },
        {
            "title": "Pea Soup",
            "servings": 4,
            "instructions": "1. Simmer the peas.\n2. Blend.",
            "ingredients": [{"name": "frozen peas", "quantity": 500, "unit": "g"}],
        },
    ],
    "meals": [
        {"name": "Cottage pie", "slot": "dinner", "recipes": ["Cottage Pie"]},
        {
            "name": "Soup and bread",
            "slot": "lunch",
            "recipes": ["Pea Soup"],
            "loose_ingredients": [{"name": "sourdough", "quantity": 1, "unit": "loaf"}],
        },
    ],
    "plan": {"label": "This week's options", "meals": ["Cottage pie", "Soup and bread"]},
    "list": [{"name": "milk", "quantity": 2, "unit": "l"}],
}


@pytest.fixture
def demo_on(settings_override, tmp_path):
    path = tmp_path / "demo.json"
    path.write_text(json.dumps(DATA))
    settings_override(DEMO_MODE="true", DEMO_DATA_FILE=str(path))
    return path


async def test_off_by_default(auth_client):
    response = await auth_client.post("/demo/fill")
    assert response.status_code == 404
    assert (await auth_client.get("/client-config")).json()["demo_mode"] is False


async def test_fills_an_empty_household_through_the_api(demo_on, auth_client):
    assert (await auth_client.get("/client-config")).json()["demo_mode"] is True

    response = await auth_client.post("/demo/fill")
    assert response.status_code == 200, response.text
    assert response.json() == {"recipes": 2, "meals": 2, "list_items": 1, "expires_in_hours": 24}

    titles = {r["title"] for r in (await auth_client.get("/recipes")).json()}
    assert titles == {"Cottage Pie", "Pea Soup"}
    plans = (await auth_client.get("/plans", params={"status": "active"})).json()
    assert [p["label"] for p in plans] == ["This week's options"]
    names = {item["name"] for item in (await auth_client.get("/shopping-list")).json()["items"]}
    # the plan's ingredients and the loose one, plus the ad-hoc milk; staples stay hidden
    assert {"minced beef", "potato", "frozen peas", "sourdough", "milk"} <= names
    assert "olive oil" not in names


async def test_fills_only_once(demo_on, auth_client):
    assert (await auth_client.post("/demo/fill")).status_code == 200
    again = await auth_client.post("/demo/fill")
    assert again.status_code == 409
    assert "empty household" in again.json()["detail"]
    assert len((await auth_client.get("/recipes")).json()) == 2


async def test_a_missing_data_file_is_the_servers_problem(settings_override, tmp_path, auth_client):
    settings_override(DEMO_MODE="true", DEMO_DATA_FILE=str(tmp_path / "nowhere.json"))
    response = await auth_client.post("/demo/fill")
    assert response.status_code == 503
    assert (await auth_client.get("/recipes")).json() == []


async def test_needs_a_signed_in_caller(demo_on, client):
    assert (await client.post("/demo/fill")).status_code == 401


async def test_the_assistant_webhook_is_refused_on_a_demo(demo_on, auth_client):
    response = await auth_client.put(
        "/household/assistant", json={"url": "https://assistant.example.com/hooks/agent", "token": "t"}
    )
    assert response.status_code == 403
    assert "self-host" in response.json()["detail"]


async def test_reap_deletes_only_households_past_their_time(demo_on, client, engine):
    old = await register(client, email="old@example.com")
    new = await register(client, email="new@example.com")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        await db.execute(
            update(Household)
            .where(Household.id == _uuid(old["user"]["household_id"]))
            .values(created_at=datetime.now(UTC) - timedelta(hours=25))
        )
        await db.commit()

    async with maker() as db:
        assert await demo.reap(db, dry_run=True) == [_uuid(old["user"]["household_id"])]
        expired = await demo.reap(db)
    assert expired == [_uuid(old["user"]["household_id"])]

    async with maker() as db:
        remaining = (await db.execute(select(Household.id))).scalars().all()
        assert remaining == [_uuid(new["user"]["household_id"])]
        assert (await db.execute(select(Recipe))).scalars().all() == []
    assert (
        await client.post("/auth/login", json={"email": "old@example.com", "password": "a-strong-password"})
    ).status_code == 401


async def test_reap_refuses_on_a_real_server(client, engine):
    await register(client)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        with pytest.raises(RuntimeError, match="DEMO_MODE is off"):
            await demo.reap(db, now=datetime.now(UTC) + timedelta(days=365))
        assert len((await db.execute(select(Household.id))).scalars().all()) == 1


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)
