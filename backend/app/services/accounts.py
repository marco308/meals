"""Account deletion (decision Q20).

Deleting a person is easy. Deciding what happens to the food is not: a household
is shared, so one member leaving must not take the other's recipes with them —
and the last member leaving must not leave an orphaned library nobody can ever
reach again.
"""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuthToken,
    CookedEvent,
    Household,
    HouseholdInvite,
    Ingredient,
    Meal,
    Plan,
    Recipe,
    ShoppingList,
    Supermarket,
    User,
)


async def household_user_count(db: AsyncSession, household_id: uuid.UUID) -> int:
    result = await db.execute(select(func.count()).select_from(User).where(User.household_id == household_id))
    return int(result.scalar_one())


async def delete_household_data(db: AsyncSession, household_id: uuid.UUID) -> None:
    """Delete everything belonging to a household, then the household itself.

    The order is load-bearing and is *not* left to the database. Only some of
    these relationships carry an `ondelete` — the `household_id` columns
    deliberately carry none, so Postgres refuses a household delete while any of
    its rows survive — and SQLite historically didn't enforce them at all. Doing
    it explicitly, parent-last, behaves identically on both.

    Children with `ondelete="CASCADE"` (recipe_ingredients, meal_recipes,
    meal_ingredients, plan_meals, list_items, list_item_sources) go with their
    parents and aren't repeated here.
    """
    # Cooked history first: its meal/recipe/plan_meal columns are SET NULL, so
    # leaving it until later would blank them out row by row for no reason.
    await db.execute(delete(CookedEvent).where(CookedEvent.household_id == household_id))
    # Lists before plans: list_item_sources point at plan_meals.
    await db.execute(delete(ShoppingList).where(ShoppingList.household_id == household_id))
    await db.execute(delete(Plan).where(Plan.household_id == household_id))
    await db.execute(delete(Meal).where(Meal.household_id == household_id))
    await db.execute(delete(Recipe).where(Recipe.household_id == household_id))
    # Ingredients last of the food: list items and meal/recipe lines reference
    # them without a cascade, so they must already be gone.
    await db.execute(delete(Ingredient).where(Ingredient.household_id == household_id))
    await db.execute(delete(Supermarket).where(Supermarket.household_id == household_id))
    await db.execute(delete(HouseholdInvite).where(HouseholdInvite.household_id == household_id))
    await db.execute(delete(User).where(User.household_id == household_id))
    await db.execute(delete(Household).where(Household.id == household_id))


async def delete_user(db: AsyncSession, user: User) -> bool:
    """Delete one account. Returns True if the whole household went with it.

    Last one out takes the data: an unreachable household would sit in the
    database forever, which is precisely what a deletion request is asking us
    not to do.

    Otherwise only the person goes. What they contributed stays, because it
    belongs to the household rather than to them — `Recipe.created_by`,
    `CookedEvent.created_by` and `HouseholdInvite.accepted_by_user_id` are all
    SET NULL, so "cooked 12×" survives the cook leaving. Their tokens cascade.
    """
    household_id = user.household_id
    if await household_user_count(db, household_id) <= 1:
        await delete_household_data(db, household_id)
        return True

    # Session and API tokens cascade from the user row, but be explicit: this is
    # the one part where leaving a stale credential behind would matter.
    await db.execute(delete(AuthToken).where(AuthToken.user_id == user.id))
    await db.delete(user)
    return False
