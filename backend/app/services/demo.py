"""The public demo server: filling a visitor's household, and emptying the server again.

A demo server is a deployment with DEMO_MODE on. Somebody who presses "Try the
demo" gets an ordinary account through `POST /auth/register` (so the signup
rate, the instance ceilings and every other guard apply unchanged), and then
`POST /demo/fill` puts the demo recipes, meals, a plan and a shopping list into
that account's empty household.

The fill goes through the public API, exactly like `app.seed` does, over an
in-process transport with the caller's own credential. Nothing here writes a
row directly: the API is the only thing that touches the database, so a demo
household is built by the same code a real one is.

Every household on a demo server is temporary. `reap` deletes each one older
than DEMO_HOUSEHOLD_TTL_HOURS, whoever made it and however, which is why this
must never be switched on for a server that holds anybody's real data.

The data file (DEMO_DATA_FILE) is JSON, kept beside the deployment rather than
in this repository, because its recipes come from a real library:

    {
      "staples":  ["olive oil", ...],
      "verdicts": [{"name": "olive oil", "value_tier": "premium", "value_note": "..."}],
      "recipes":  [<a POST /recipes body>, ...],
      "meals":    [{"name": "...", "slot": "dinner", "recipes": ["<recipe title>"],
                    "loose_ingredients": [...]}],
      "plan":     {"label": "This week's options", "meals": ["<meal name>", ...]},
      "list":     [{"name": "milk", "quantity": 2, "unit": "l"}]
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.users import Household
from app.services.accounts import delete_household_data


class DemoUnavailable(Exception):
    """The demo data can't be read; the server is misconfigured, not the caller."""


_cache: tuple[str, float, dict] | None = None


def load_data() -> dict:
    """The demo data, re-read only when the file changes."""
    global _cache
    path = get_settings().demo_data_file
    if not path:
        raise DemoUnavailable("DEMO_DATA_FILE is not set")
    try:
        mtime = Path(path).stat().st_mtime
        if _cache and _cache[0] == path and _cache[1] == mtime:
            return _cache[2]
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise DemoUnavailable(f"DEMO_DATA_FILE can't be read: {exc.__class__.__name__}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("recipes"), list):
        raise DemoUnavailable("DEMO_DATA_FILE has no recipes")
    _cache = (path, mtime, data)
    return data


@dataclass
class Filled:
    recipes: int
    meals: int
    list_items: int


async def fill(client: httpx.AsyncClient, data: dict) -> Filled:
    """Put the demo into the household `client` is signed in to, through the API.

    Every call raises on failure: a half-filled demo is a worse first
    impression than an error that says to try again."""

    async def call(method: str, url: str, **kwargs) -> httpx.Response:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    for name in data.get("staples", []):
        await call("POST", "/ingredients", json={"name": name, "is_staple": True})
    for verdict in data.get("verdicts", []):
        await call("POST", "/ingredients", json=verdict)

    recipe_ids: dict[str, str] = {}
    for recipe in data["recipes"]:
        created = await call("POST", "/recipes", json=recipe)
        recipe_ids[recipe["title"]] = created.json()["id"]

    meal_ids: dict[str, str] = {}
    for meal in data.get("meals", []):
        body = {k: v for k, v in meal.items() if k != "recipes"}
        body["recipe_ids"] = [recipe_ids[title] for title in meal.get("recipes", []) if title in recipe_ids]
        created = await call("POST", "/meals", json=body)
        meal_ids[meal["name"]] = created.json()["id"]

    plan = data.get("plan")
    if plan:
        created = await call("POST", "/plans", json={"label": plan.get("label", "This week's options")})
        plan_id = created.json()["id"]
        for name in plan.get("meals", []):
            if name in meal_ids:
                await call("POST", f"/plans/{plan_id}/meals", json={"meal_id": meal_ids[name]})

    items = data.get("list", [])
    for item in items:
        await call("POST", "/shopping-list/items", json={"id": str(uuid.uuid4()), **item})

    return Filled(recipes=len(recipe_ids), meals=len(meal_ids), list_items=len(items))


async def reap(db: AsyncSession, *, now: datetime | None = None, dry_run: bool = False) -> list[uuid.UUID]:
    """Delete every household older than the demo's time to live.

    Refuses outright unless DEMO_MODE is on: on any other server this would be
    a mass deletion of real households."""
    settings = get_settings()
    if not settings.demo_mode:
        raise RuntimeError("refusing to reap: DEMO_MODE is off, so these households are real")
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=settings.demo_household_ttl_hours)
    # Compared here rather than in SQL: SQLite hands back naive datetimes for a
    # timezone-aware column, and a demo server holds few enough households that
    # reading them all costs nothing (the same choice services/reaping.py makes).
    rows = (await db.execute(select(Household.id, Household.created_at))).all()
    expired = [hid for hid, created in rows if _aware(created) < cutoff]
    if not dry_run:
        for household_id in expired:
            await delete_household_data(db, household_id)
        await db.commit()
    return expired


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def reaper(interval_seconds: float = 3600) -> None:
    """Reap on a timer, for the life of the app (started in main.lifespan on a
    demo server only). In the app rather than in cron because a demo server has
    nothing else to schedule and its container is all there is: `python -m
    app.demo` does the same by hand. A failed pass is logged and the next one
    tries again."""
    from app.database import SessionLocal
    from app.observability import log_event

    while True:
        try:
            async with SessionLocal() as db:
                expired = await reap(db)
            log_event("demo.reap", outcome="ok", deleted=len(expired))
        except Exception as exc:  # noqa: BLE001 - keep reaping whatever one pass hit
            logging.getLogger("meals.demo").warning("demo reap failed: %s", exc.__class__.__name__)
        await asyncio.sleep(interval_seconds)
