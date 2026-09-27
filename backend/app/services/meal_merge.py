"""Fold identical meals into one (the duplicates #193 stopped making).

Before `POST /meals` reused an exact repeat, the iPhone app's "Add to plan" on
a recipe made a new meal every time, so a household that planned one recipe
five times has five identical meals. Two meals are the same here when they
have the same name (ignoring case), slots, recipes at the same scales, and the
same loose ingredients row for row. That is the API's rule plus the extras,
which can be compared exactly once they are stored.

The oldest meal of a group is kept. Everything that points at the others moves
to it: plan entries, cooked events, freezer batches. Then the others are
deleted. When the keeper and a duplicate are both on the same plan, which the
plan's unique (plan, meal) key cannot hold, the duplicate's entry goes: its
cooking is kept on the keeper's entry if that one was not cooked, its cooked
events follow the keeper's entry, and on an active plan its shopping-list
contributions are removed the same way deleting a meal removes them. The
keeper's entry already contributes the same ingredients, so the list loses a
double count and nothing else.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CookedEvent, FreezerItem, Meal, Plan, PlanMeal
from app.services.cooking import refresh_meal_stats
from app.services.shopping import get_active_list, remove_meal_contributions
from app.services.slots import meal_slots


@dataclass
class MergedGroup:
    household_id: uuid.UUID
    name: str
    kept: uuid.UUID
    removed: list[uuid.UUID]
    plan_entries_moved: int
    plan_entries_folded: int
    times_cooked: int


def _signature(meal: Meal) -> tuple:
    return (
        meal.household_id,
        meal.name.strip().lower(),
        tuple(meal_slots(meal)),
        frozenset((link.recipe_id, link.scale) for link in meal.recipe_links),
        frozenset((link.ingredient_id, link.quantity, link.unit) for link in meal.ingredient_links),
    )


async def find_groups(db: AsyncSession, household_id: uuid.UUID | None = None) -> list[list[Meal]]:
    """Every set of two or more identical meals, oldest first within each."""
    query = select(Meal).order_by(Meal.created_at, Meal.id)
    if household_id is not None:
        query = query.where(Meal.household_id == household_id)
    groups: dict[tuple, list[Meal]] = defaultdict(list)
    for meal in (await db.execute(query)).scalars():
        groups[_signature(meal)].append(meal)
    return [meals for meals in groups.values() if len(meals) > 1]


async def _fold(db: AsyncSession, keeper: Meal, duplicate: Meal) -> tuple[int, int]:
    """Move what points at `duplicate` onto `keeper`. Returns (moved, folded)
    plan entries."""
    moved = folded = 0
    keeper_entries = {
        entry.plan_id: entry
        for entry in (await db.execute(select(PlanMeal).where(PlanMeal.meal_id == keeper.id))).scalars()
    }
    rows = await db.execute(
        select(PlanMeal, Plan.status).join(Plan, PlanMeal.plan_id == Plan.id).where(PlanMeal.meal_id == duplicate.id)
    )
    for entry, plan_status in rows.all():
        target = keeper_entries.get(entry.plan_id)
        if target is None:
            entry.meal_id = keeper.id
            keeper_entries[entry.plan_id] = entry
            moved += 1
            continue
        if target.cooked_at is None and entry.cooked_at is not None:
            target.cooked_at = entry.cooked_at
        await db.execute(update(CookedEvent).where(CookedEvent.plan_meal_id == entry.id).values(plan_meal_id=target.id))
        if plan_status == "active":
            await remove_meal_contributions(db, await get_active_list(db, keeper.household_id), entry.id)
        await db.delete(entry)
        folded += 1
    await db.execute(update(CookedEvent).where(CookedEvent.meal_id == duplicate.id).values(meal_id=keeper.id))
    await db.execute(update(FreezerItem).where(FreezerItem.meal_id == duplicate.id).values(meal_id=keeper.id))
    await db.flush()
    await db.delete(duplicate)
    await db.flush()
    return moved, folded


async def merge(db: AsyncSession, household_id: uuid.UUID | None = None) -> list[MergedGroup]:
    """Merge every group of identical meals. Flushes but does not commit, so a
    dry run is a rollback."""
    merged: list[MergedGroup] = []
    for keeper, *duplicates in await find_groups(db, household_id):
        moved = folded = 0
        removed = [duplicate.id for duplicate in duplicates]
        for duplicate in duplicates:
            m, f = await _fold(db, keeper, duplicate)
            moved += m
            folded += f
        await refresh_meal_stats(db, keeper.id)
        merged.append(
            MergedGroup(
                household_id=keeper.household_id,
                name=keeper.name,
                kept=keeper.id,
                removed=removed,
                plan_entries_moved=moved,
                plan_entries_folded=folded,
                times_cooked=keeper.times_cooked,
            )
        )
    return merged
