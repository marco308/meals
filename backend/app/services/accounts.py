"""Account deletion (Q20) and moving between households (Q23).

Deleting a person is easy. Deciding what happens to the food is not: a household
is shared, so one member leaving must not take the other's recipes with them —
and the last member leaving must not leave an orphaned library nobody can ever
reach again.

Leaving, being removed and joining somewhere else are all the same write with
different callers: move the user's `household_id`, then collect the household
behind them if nobody is left in it. The permission rules that decide *who* may
ask for each of those live in the router; what is here is what the answer costs.
"""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuthToken,
    CookedEvent,
    FreezerItem,
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


async def household_members(db: AsyncSession, household_id: uuid.UUID) -> list[User]:
    """Everyone in a household, longest-standing first. That order is not
    cosmetic: it is also the order the lead falls to if a lead deletes their
    account (Q23)."""
    result = await db.execute(select(User).where(User.household_id == household_id).order_by(User.created_at, User.id))
    return list(result.scalars())


async def next_lead(db: AsyncSession, household_id: uuid.UUID, *, excluding: uuid.UUID) -> uuid.UUID | None:
    """Who leads a household once `excluding` is out of it, or None if nobody is
    left. The longest-standing remaining member: the household's oldest
    relationship, and the one answer nobody has to be asked for — which matters
    because this is only reached when the lead has already gone (`delete_user`).
    A lead who is still here hands over instead."""
    result = await db.execute(
        select(User.id)
        .where(User.household_id == household_id, User.id != excluding)
        .order_by(User.created_at, User.id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def household_has_content(db: AsyncSession, household_id: uuid.UUID) -> bool:
    """Whether a household holds anything a person would miss.

    Used to decide when leaving it needs confirming. A household that has only
    ever been registered into holds nothing, and making that person type a
    confirmation flag to accept an invite would be ceremony about deleting
    nothing at all.
    """
    for model in (Recipe, Meal, Plan, ShoppingList, Ingredient, Supermarket, CookedEvent, FreezerItem):
        # One row is the whole question, so stop at one rather than counting a
        # library that could be thousands of rows deep.
        result = await db.execute(select(model.id).where(model.household_id == household_id).limit(1))
        if result.scalar_one_or_none() is not None:
            return True
    return False


async def leads_alongside_others(db: AsyncSession, user: User) -> bool:
    """Whether this person leads a household that other people are still in.

    Both doors out — leaving, and redeeming an invite into somewhere else — are
    closed to them until they hand the lead on (Q23). They are still here to be
    asked, so the household should not have a lead chosen for it; that automatic
    succession is only for a lead who deletes their account and is gone.
    """
    household = await db.get(Household, user.household_id)
    if household is None or household.lead_user_id != user.id:
        return False
    return await household_user_count(db, user.household_id) > 1


async def move_user_to_household(db: AsyncSession, user: User, target_household_id: uuid.UUID) -> bool:
    """Move one person into another household. Returns True if the household
    they left was collected because they were the last one in it.

    Only the `household_id` moves. Recipes, meals, plans, lists and cooked
    history belong to the household rather than to the person (Q20), and their
    tokens hang off `user_id`, so nothing is revoked and nothing is copied — the
    next request they make simply resolves somewhere else (`deps.py`).

    Callers check `leads_alongside_others` first and refuse with a 409 naming the
    way out; a lead reaching here with members behind them is a bug rather than
    a user error, so it raises instead of quietly picking their replacement.
    """
    origin_id = user.household_id
    if origin_id == target_household_id:
        raise ValueError("move_user_to_household called with the household the user is already in")

    successor = await next_lead(db, origin_id, excluding=user.id)
    origin = await db.get(Household, origin_id)
    if successor is not None and origin is not None and origin.lead_user_id == user.id:
        raise ValueError("move_user_to_household called for a lead who has not handed over")

    user.household_id = target_household_id
    await db.flush()

    if successor is None:
        # They were the last one out, so the library they leave behind is
        # unreachable forever — the same reasoning as deleting the last account.
        await delete_household_data(db, origin_id)
        return True
    return False


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
    # Same shape: its meal/recipe links are SET NULL, so it goes before them too.
    await db.execute(delete(FreezerItem).where(FreezerItem.household_id == household_id))
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
    # The household points at its lead, so that reference goes before the users
    # do. `ondelete="SET NULL"` would also handle it, but this module makes its
    # order explicit rather than trusting two engines to agree (Q20).
    household = await db.get(Household, household_id)
    if household is not None:
        household.lead_user_id = None
        await db.flush()
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

    # A lead who deletes their account still has to leave one behind (Q23), and
    # nobody is around to be asked which — the longest-standing member takes it.
    # A household with a subscription and no lead is a support ticket.
    household = await db.get(Household, household_id)
    if household is not None and household.lead_user_id == user.id:
        household.lead_user_id = await next_lead(db, household_id, excluding=user.id)
        await db.flush()

    # Session and API tokens cascade from the user row, but be explicit: this is
    # the one part where leaving a stale credential behind would matter.
    await db.execute(delete(AuthToken).where(AuthToken.user_id == user.id))
    await db.delete(user)
    return False
