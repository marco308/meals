"""Everything a household owns, in one streamed JSON document (issue #97).

This is the answer to the only real objection to hosting somebody's data for
them: "take your data and go" has to be one request, not a support ticket, and
it stays free in every tier forever (planning/08-freemium.md §1). It is worth
exactly as much on a server nobody pays for, where it is the thing to run
before a migration and the per-household complement to the whole-database
`backup/` sidecar — which deliberately restores whole rather than piecemeal.

Three decisions shape the module:

- **Streamed, row by row.** A 2,000-recipe household is not small, and neither
  buffering the document nor holding every ORM object is acceptable. Each table
  is read in batches and each row is serialised and then expunged, so the
  session's identity map stays flat and bytes reach the client while the
  database is still reading. `expunge_all()` is *not* the way to do that — it
  invalidates the identity map the open result is still loading through — so
  rows are expunged one at a time.
- **Readable as well as importable.** Children are nested under their parents
  (a recipe carries its lines, a list carries its items and their provenance)
  and every reference to an ingredient or a recipe carries the name beside the
  id, so a human can read the file without joining anything. Ids are kept
  because they are what makes it importable in principle.
- **Explicit field lists, never `__table__.columns`.** Reflecting the columns
  would mean the next column added to `households` — the next billing or quota
  field — silently joins the export, and one of those days it would be a
  secret. Everything here is named on purpose; `tests/unit/test_export.py`
  fails when a new column is neither exported nor written down as excluded, so
  the omission is loud rather than silent.
"""

import json
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base
from app.models import (
    CookedEvent,
    FreezerItem,
    Household,
    Ingredient,
    ListItem,
    Meal,
    Plan,
    Recipe,
    ShoppingList,
    Supermarket,
    User,
)

#: The export document's own version, so an importer written later can tell
#: what it is holding. Bump it when the shape changes in a way a reader would
#: have to notice — a new section or a new field is not that.
EXPORT_VERSION = 1

#: Rows read per round trip. Big enough that a large household is not a
#: thousand queries, small enough that no batch is a memory problem.
BATCH = 200


def _encode(value: Any) -> str:
    """One row as JSON. Datetimes are stamped UTC rather than left naive:
    SQLite round-trips them without a timezone, and a consumer should not have
    to guess which one the file means."""

    def default(item: Any) -> Any:
        if isinstance(item, uuid.UUID):
            return str(item)
        if isinstance(item, datetime):
            return (item if item.tzinfo is not None else item.replace(tzinfo=UTC)).isoformat()
        if isinstance(item, date):
            return item.isoformat()
        raise TypeError(f"{type(item).__name__} is not JSON")

    return json.dumps(value, default=default)


async def _stream_rows(db: AsyncSession, statement: Select) -> AsyncIterator[Any]:
    """Every row the statement selects, in batches, forgetting each as it goes.

    The expunge is per row rather than per batch: `Session.expunge_all()` kills
    the identity map that the still-open streaming result is loading through,
    which fails on the second batch.
    """
    result = await db.stream_scalars(statement)
    async for batch in result.partitions(BATCH):
        for row in batch:
            yield row
            db.expunge(row)


# ------------------------------------------------------------------ the rows


def _member(user: User, *, lead_user_id: uuid.UUID | None) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_lead": user.id == lead_user_id,
        "created_at": user.created_at,
    }


def _ingredient(ingredient: Ingredient) -> dict:
    return {
        "id": ingredient.id,
        "name": ingredient.name,
        "aisle": ingredient.aisle,
        "is_staple": ingredient.is_staple,
        "value_tier": ingredient.value_tier,
        "value_note": ingredient.value_note,
        "created_at": ingredient.created_at,
    }


def _recipe(recipe: Recipe) -> dict:
    return {
        "id": recipe.id,
        "title": recipe.title,
        "source_url": recipe.source_url,
        "servings": recipe.servings,
        "prep_minutes": recipe.prep_minutes,
        "cook_minutes": recipe.cook_minutes,
        "image_url": recipe.image_url,
        "instructions": recipe.instructions,
        "tags": list(recipe.tags or []),
        "parse_source": recipe.parse_source,
        "edited": recipe.edited,
        "times_cooked": recipe.times_cooked,
        "last_cooked_at": recipe.last_cooked_at,
        "created_by": recipe.created_by,
        "created_at": recipe.created_at,
        "updated_at": recipe.updated_at,
        "ingredients": [
            {
                "ingredient_id": line.ingredient_id,
                "name": line.ingredient.name,
                "quantity": line.quantity,
                "unit": line.unit,
                "raw_text": line.raw_text,
                "position": line.position,
            }
            for line in recipe.ingredient_links
        ],
    }


def _meal(meal: Meal) -> dict:
    return {
        "id": meal.id,
        "name": meal.name,
        "slot": meal.slot,
        "times_cooked": meal.times_cooked,
        "last_cooked_at": meal.last_cooked_at,
        "created_at": meal.created_at,
        "recipes": [
            {"recipe_id": link.recipe_id, "title": link.recipe.title, "scale": link.scale} for link in meal.recipe_links
        ],
        "loose_ingredients": [
            {
                "ingredient_id": link.ingredient_id,
                "name": link.ingredient.name,
                "quantity": link.quantity,
                "unit": link.unit,
            }
            for link in meal.ingredient_links
        ],
    }


def _plan(plan: Plan) -> dict:
    return {
        "id": plan.id,
        "label": plan.label,
        "starts_on": plan.starts_on,
        "status": plan.status,
        "created_at": plan.created_at,
        "archived_at": plan.archived_at,
        "meals": [
            {
                "id": link.id,
                "meal_id": link.meal_id,
                "name": link.meal.name,
                "cooked_at": link.cooked_at,
                "created_at": link.created_at,
            }
            for link in plan.meal_links
        ],
    }


def _cooked_event(event: CookedEvent) -> dict:
    """The household's record of what it actually ate, which outlives the plan,
    the meal and the recipe by design — so it is exported as its own section
    rather than folded into any of them."""
    return {
        "id": event.id,
        "subject": event.subject,
        "meal_id": event.meal_id,
        "meal_name": event.meal_name,
        "recipe_id": event.recipe_id,
        "recipe_title": event.recipe_title,
        "plan_meal_id": event.plan_meal_id,
        "scale": event.scale,
        "cooked_at": event.cooked_at,
        "created_by": event.created_by,
        "created_at": event.created_at,
    }


def _freezer_item(item: FreezerItem) -> dict:
    """The label is the record and the links are a courtesy (Q24), so both are
    written: a reader sees what is in the freezer even when the meal it came
    from is long gone."""
    return {
        "id": item.id,
        "label": item.label,
        "meal_id": item.meal_id,
        "recipe_id": item.recipe_id,
        "portions": item.portions,
        "note": item.note,
        "frozen_on": item.frozen_on,
        "created_by": item.created_by,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _supermarket(market: Supermarket) -> dict:
    return {
        "id": market.id,
        "name": market.name,
        "aisle_order": list(market.aisle_order or []),
        "is_active": market.is_active,
        "created_at": market.created_at,
    }


def _shopping_list(shopping_list: ShoppingList) -> dict:
    return {
        "id": shopping_list.id,
        "status": shopping_list.status,
        "created_at": shopping_list.created_at,
        "archived_at": shopping_list.archived_at,
        "items": [_list_item(item) for item in shopping_list.items],
    }


def _list_item(item: ListItem) -> dict:
    """`quantity` is derived from the sources rather than stored (Q3's cousin:
    a line's amount is the sum of its contributions), so both are written —
    the total for a reader, the rows for anything reconstructing it."""
    return {
        "id": item.id,
        "ingredient_id": item.ingredient_id,
        "name": item.ingredient.name,
        "quantity": item.quantity,
        "unit": item.unit,
        "checked": item.checked,
        "excluded": item.excluded,
        "staple_needed": item.staple_needed,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "sources": [
            {
                "id": source.id,
                "plan_meal_id": source.plan_meal_id,
                "recipe_id": source.recipe_id,
                "quantity": source.quantity,
                "client_key": source.client_key,
                "created_at": source.created_at,
            }
            for source in item.sources
        ],
    }


# --------------------------------------------------------------- the document

#: Section name → (statement builder, row builder). Ordered, and every order is
#: by `created_at` then `id`: two exports of an unchanged household should be
#: the same bytes, so they can be diffed.
Section = tuple[str, Callable[[uuid.UUID], Select], Callable[[Any], dict]]


def _sections() -> tuple[Section, ...]:
    return (
        (
            "ingredients",
            lambda hid: select(Ingredient).where(Ingredient.household_id == hid).order_by(*_order(Ingredient)),
            _ingredient,
        ),
        ("recipes", lambda hid: select(Recipe).where(Recipe.household_id == hid).order_by(*_order(Recipe)), _recipe),
        ("meals", lambda hid: select(Meal).where(Meal.household_id == hid).order_by(*_order(Meal)), _meal),
        ("plans", lambda hid: select(Plan).where(Plan.household_id == hid).order_by(*_order(Plan)), _plan),
        (
            "cooked_events",
            lambda hid: select(CookedEvent).where(CookedEvent.household_id == hid).order_by(*_order(CookedEvent)),
            _cooked_event,
        ),
        (
            "freezer",
            lambda hid: select(FreezerItem).where(FreezerItem.household_id == hid).order_by(*_order(FreezerItem)),
            _freezer_item,
        ),
        (
            "supermarkets",
            lambda hid: select(Supermarket).where(Supermarket.household_id == hid).order_by(*_order(Supermarket)),
            _supermarket,
        ),
        (
            "shopping_lists",
            lambda hid: select(ShoppingList).where(ShoppingList.household_id == hid).order_by(*_order(ShoppingList)),
            _shopping_list,
        ),
    )


def _order(model: type[Base]) -> tuple:
    return (model.created_at, model.id)


SECTION_NAMES = tuple(name for name, _, _ in _sections())


async def stream_household(db: AsyncSession, household: Household, *, api_version: str) -> AsyncIterator[str]:
    """The whole document, a fragment at a time.

    Everything is scoped to `household.id` — the same rule as every other query
    in the app, and the one that matters most here, since this endpoint hands
    back a file rather than a screenful.

    The household row and its members are read up front (both are small and
    bounded); everything after that is streamed.
    """
    household_id = household.id
    members = (
        (await db.execute(select(User).where(User.household_id == household_id).order_by(*_order(User))))
        .scalars()
        .all()
    )
    header = {
        "export_version": EXPORT_VERSION,
        "exported_at": datetime.now(UTC),
        "api_version": api_version,
        "household": {
            "id": household.id,
            "name": household.name,
            "lead_user_id": household.lead_user_id,
            "created_at": household.created_at,
        },
        "members": [_member(member, lead_user_id=household.lead_user_id) for member in members],
    }
    # Written open-ended so the sections can be appended as they are read.
    yield _encode(header)[:-1]

    for name, statement, row in _sections():
        yield f", {json.dumps(name)}: ["
        separator = ""
        async for entity in _stream_rows(db, statement(household_id)):
            yield separator + _encode(row(entity))
            separator = ","
        yield "]"
    yield "}"
