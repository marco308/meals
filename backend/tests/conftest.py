# Test environment must be pinned before any app module is imported:
# in-memory SQLite, no auth rate limiting.
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["AUTH_RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["ENVIRONMENT"] = "test"

from collections.abc import AsyncIterator  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import Base, enforce_sqlite_foreign_keys, get_db  # noqa: E402
from app.main import app  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_html(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    # Without this the suite runs with foreign keys unenforced while production
    # enforces them — see the docstring on enforce_sqlite_foreign_keys.
    enforce_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def client(engine) -> AsyncIterator[AsyncClient]:
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


async def register(client: AsyncClient, email: str = "marcus@example.com", name: str = "Marcus", **extra) -> dict:
    """Register a user. Extra kwargs (`invite_code`, `household_name`) go
    straight into the payload; with none, the account gets its own fresh
    household (decision Q19)."""
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": "a-strong-password", "display_name": name, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def auth_client(client: AsyncClient) -> AsyncClient:
    """A client already registered and authenticated as the first household user."""
    auth = await register(client)
    client.headers["Authorization"] = f"Bearer {auth['token']}"
    return client


@pytest.fixture
def settings_override(monkeypatch):
    """Temporarily override env-backed settings (get_settings is cached)."""

    def apply(**env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    yield apply
    get_settings.cache_clear()


# ---------------------------------------------------------------- shared builders


async def create_recipe(client: AsyncClient, **overrides) -> dict:
    payload = {
        "title": "Spaghetti Bolognese",
        "servings": 4,
        "ingredients": [
            {"name": "minced beef", "quantity": 500, "unit": "g"},
            {"name": "onion", "quantity": 1, "unit": "item"},
            {"name": "chopped tomatoes", "quantity": 2, "unit": "tins"},
        ],
    }
    payload.update(overrides)
    response = await client.post("/recipes", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def create_meal(client: AsyncClient, **overrides) -> dict:
    payload = {"name": "Spag bol", "slot": "dinner"}
    payload.update(overrides)
    response = await client.post("/meals", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def create_plan(client: AsyncClient, label: str = "w/c 20 July", **overrides) -> dict:
    response = await client.post("/plans", json={"label": label, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


async def get_list(client: AsyncClient, **params) -> dict:
    response = await client.get("/shopping-list", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def item_by_name(shopping_list: dict, name: str, unit: str | None = "any") -> dict | None:
    for item in shopping_list["items"]:
        if item["name"] == name and (unit == "any" or item["unit"] == unit):
            return item
    return None
