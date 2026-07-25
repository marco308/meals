"""Cooked-history: append events, then refresh the counters they feed.

`cooked_events` is the durable record ("how often do we actually cook this?");
`Recipe.times_cooked` / `Meal.times_cooked` are a read model so every
serializer can answer without a join. The counters are always *recomputed*
from the events, never incremented in place — a blind `+= 1` is how the two
drift apart.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CookedEvent, Meal, PlanMeal, Recipe


async def _refresh(
    db: AsyncSession, model: type[Meal] | type[Recipe], subject: str, column, entity_id: uuid.UUID | None
) -> None:
    if entity_id is None:
        return
    entity = await db.get(model, entity_id)
    if entity is None:
        return
    result = await db.execute(
        select(func.count(CookedEvent.id), func.max(CookedEvent.cooked_at)).where(
            column == entity_id, CookedEvent.subject == subject
        )
    )
    count, last = result.one()
    entity.times_cooked = count or 0
    entity.last_cooked_at = last


async def refresh_meal_stats(db: AsyncSession, meal_id: uuid.UUID | None) -> None:
    await _refresh(db, Meal, "meal", CookedEvent.meal_id, meal_id)


async def refresh_recipe_stats(db: AsyncSession, recipe_id: uuid.UUID | None) -> None:
    await _refresh(db, Recipe, "recipe", CookedEvent.recipe_id, recipe_id)


async def record_cooked(
    db: AsyncSession,
    household_id: uuid.UUID,
    plan_meal: PlanMeal,
    meal: Meal,
    user_id: uuid.UUID | None = None,
) -> None:
    """Record one cooking of `meal`: a meal-subject event plus one per recipe
    the meal holds right now. Composition is captured at cook time, so editing
    the meal later (issue #16) can't rewrite what we ate."""
    cooked_at = plan_meal.cooked_at
    assert cooked_at is not None, "record_cooked runs after plan_meal.cooked_at is set"

    db.add(
        CookedEvent(
            household_id=household_id,
            subject="meal",
            meal_id=meal.id,
            plan_meal_id=plan_meal.id,
            meal_name=meal.name,
            cooked_at=cooked_at,
            created_by=user_id,
        )
    )
    for link in meal.recipe_links:
        db.add(
            CookedEvent(
                household_id=household_id,
                subject="recipe",
                meal_id=meal.id,
                recipe_id=link.recipe_id,
                plan_meal_id=plan_meal.id,
                meal_name=meal.name,
                recipe_title=link.recipe.title,
                scale=link.scale,
                cooked_at=cooked_at,
                created_by=user_id,
            )
        )
    await db.flush()

    await refresh_meal_stats(db, meal.id)
    for link in meal.recipe_links:
        await refresh_recipe_stats(db, link.recipe_id)
