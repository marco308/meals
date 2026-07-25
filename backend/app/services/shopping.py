"""Shopping-list domain logic (guiding principle 3: the list knows *why*).

Every line links back to the meal(s)/recipe(s) that need it via
ListItemSource rows; ad-hoc rows have no plan_meal. Adding a meal to the plan
merges its contributions into existing lines (exact canonical unit match
only, decision Q2); removing the meal decrements exactly what it added and
never touches ad-hoc contributions.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ListItem, ListItemSource, Meal, PlanMeal, ShoppingList
from app.schemas.shopping import AdhocItemIn
from app.services.catalog import get_or_create_ingredient


async def get_active_list(db: AsyncSession, household_id: uuid.UUID) -> ShoppingList:
    result = await db.execute(
        select(ShoppingList)
        .where(ShoppingList.household_id == household_id, ShoppingList.status == "active")
        .order_by(ShoppingList.created_at)
    )
    shopping_list = result.scalars().first()
    if shopping_list is None:
        shopping_list = ShoppingList(household_id=household_id)
        db.add(shopping_list)
        await db.flush()
    return shopping_list


def _meal_contributions(meal: Meal) -> list[tuple[uuid.UUID, float | None, str | None, uuid.UUID | None]]:
    """Flatten a meal into (ingredient_id, quantity, unit, recipe_id) rows —
    every recipe line plus every loose ingredient.

    Recipe lines are multiplied by their link's scale (Q18); loose ingredients
    are already stated as the absolute amount the meal needs, so they aren't.
    The exact float is what gets stored — rounding a half tin up here would
    make two meals each needing half a tin buy two."""
    contributions = []
    for recipe_link in meal.recipe_links:
        for line in recipe_link.recipe.ingredient_links:
            quantity = None if line.quantity is None else line.quantity * recipe_link.scale
            contributions.append((line.ingredient_id, quantity, line.unit, recipe_link.recipe_id))
    for loose in meal.ingredient_links:
        contributions.append((loose.ingredient_id, loose.quantity, loose.unit, None))
    return contributions


async def _find_item(
    db: AsyncSession, list_id: uuid.UUID, ingredient_id: uuid.UUID, unit: str | None
) -> ListItem | None:
    result = await db.execute(
        select(ListItem).where(
            ListItem.list_id == list_id, ListItem.ingredient_id == ingredient_id, ListItem.unit == unit
        )
    )
    return result.scalars().first()


async def add_meal_contributions(
    db: AsyncSession, shopping_list: ShoppingList, plan_meal: PlanMeal, meal: Meal
) -> None:
    for ingredient_id, quantity, unit, recipe_id in _meal_contributions(meal):
        item = await _find_item(db, shopping_list.id, ingredient_id, unit)
        if item is None:
            item = ListItem(list_id=shopping_list.id, ingredient_id=ingredient_id, unit=unit)
            db.add(item)
            await db.flush()
        elif item.checked:
            # A fresh need arrived for something already ticked off — surface it again.
            item.checked = False
        db.add(ListItemSource(item_id=item.id, plan_meal_id=plan_meal.id, recipe_id=recipe_id, quantity=quantity))
    await db.flush()


async def remove_meal_contributions(db: AsyncSession, shopping_list: ShoppingList, plan_meal_id: uuid.UUID) -> None:
    """Delete this plan-meal's contributions from the active list, dropping
    lines that end up with no remaining contribution. Ad-hoc contributions
    (and anything on archived lists) are untouched."""
    result = await db.execute(
        select(ListItem)
        .join(ListItemSource, ListItemSource.item_id == ListItem.id)
        .where(ListItem.list_id == shopping_list.id, ListItemSource.plan_meal_id == plan_meal_id)
        .distinct()
        # refresh possibly-stale source collections so orphan removal sees the truth
        .execution_options(populate_existing=True)
    )
    for item in result.scalars().all():
        item.sources[:] = [s for s in item.sources if s.plan_meal_id != plan_meal_id]
        if not item.sources:
            await db.delete(item)
    await db.flush()


async def add_adhoc_item(
    db: AsyncSession, household_id: uuid.UUID, shopping_list: ShoppingList, payload: AdhocItemIn
) -> tuple[ListItem, bool]:
    """Add an ad-hoc item (milk, bin bags). Returns (item, created_new_source).

    Idempotent when the client supplies an id: replaying the same request
    finds the recorded client key and changes nothing.
    """
    if payload.id is not None:
        replay = await db.execute(
            select(ListItemSource)
            .join(ListItem, ListItemSource.item_id == ListItem.id)
            .where(ListItem.list_id == shopping_list.id, ListItemSource.client_key == str(payload.id))
        )
        existing_source = replay.scalars().first()
        if existing_source is not None:
            item = await db.get(ListItem, existing_source.item_id)
            assert item is not None
            return item, False

    ingredient = await get_or_create_ingredient(db, household_id, payload.name)
    item = await _find_item(db, shopping_list.id, ingredient.id, payload.unit)
    if item is None:
        # Honour the client-generated id (offline-first sync) unless it is
        # already taken, e.g. by an item on an archived list.
        reuse_id = payload.id is not None and await db.get(ListItem, payload.id) is None
        item = ListItem(
            list_id=shopping_list.id,
            ingredient_id=ingredient.id,
            unit=payload.unit,
            **({"id": payload.id} if reuse_id else {}),
        )
        db.add(item)
        await db.flush()
    elif item.checked:
        item.checked = False
    db.add(
        ListItemSource(
            item_id=item.id,
            quantity=payload.quantity,
            client_key=str(payload.id) if payload.id is not None else None,
        )
    )
    await db.flush()
    return item, True


def is_adhoc_only(item: ListItem) -> bool:
    return all(source.plan_meal_id is None for source in item.sources)


async def archive_and_replace(db: AsyncSession, shopping_list: ShoppingList) -> ShoppingList:
    from app.models.users import utcnow

    shopping_list.status = "archived"
    shopping_list.archived_at = utcnow()
    fresh = ShoppingList(household_id=shopping_list.household_id)
    db.add(fresh)
    await db.flush()
    return fresh


ItemState = tuple[bool, bool, bool]  # checked, excluded, staple_needed


async def _contribution_state(
    db: AsyncSession, shopping_list: ShoppingList, plan_meal_id: uuid.UUID
) -> dict[tuple[uuid.UUID, str | None], tuple[float | None, ItemState]]:
    """What this plan-meal contributes right now, and the shopping state of the
    lines it lands on: `(ingredient, unit) -> (quantity from this meal, flags)`."""
    result = await db.execute(
        select(ListItem, ListItemSource)
        .join(ListItemSource, ListItemSource.item_id == ListItem.id)
        .where(ListItem.list_id == shopping_list.id, ListItemSource.plan_meal_id == plan_meal_id)
        .execution_options(populate_existing=True)
    )
    state: dict[tuple[uuid.UUID, str | None], tuple[float | None, ItemState]] = {}
    for item, source in result.all():
        key = (item.ingredient_id, item.unit)
        total, _ = state.get(key, (None, None))  # type: ignore[assignment]
        if source.quantity is not None:
            total = (total or 0) + source.quantity
        state[key] = (total, (item.checked, item.excluded, item.staple_needed))
    return state


async def resync_meal_contributions(db: AsyncSession, household_id: uuid.UUID, meal: Meal) -> None:
    """After a meal's (or one of its recipes') composition changes, replace its
    contributions on the active list for every active plan it is in.

    A line whose need is unchanged keeps the shopping state it already had:
    editing a meal to add garlic bread must not quietly un-tick the mince you
    already put in the trolley. Genuinely changed lines (new ingredient, or a
    different quantity of the same one) come back unchecked, which is the point
    of re-surfacing them."""
    from app.models import Plan

    active_list = await get_active_list(db, household_id)
    result = await db.execute(
        select(PlanMeal)
        .join(Plan, PlanMeal.plan_id == Plan.id)
        .where(PlanMeal.meal_id == meal.id, Plan.status == "active", Plan.household_id == household_id)
    )
    for plan_meal in result.scalars().all():
        before = await _contribution_state(db, active_list, plan_meal.id)
        await remove_meal_contributions(db, active_list, plan_meal.id)
        await add_meal_contributions(db, active_list, plan_meal, meal)
        after = await _contribution_state(db, active_list, plan_meal.id)
        for key, (quantity, _) in after.items():
            previous = before.get(key)
            if previous is None or previous[0] != quantity:
                continue
            item = await _find_item(db, active_list.id, key[0], key[1])
            if item is not None:
                item.checked, item.excluded, item.staple_needed = previous[1]
        await db.flush()


async def get_list_full(db: AsyncSession, list_id: uuid.UUID) -> ShoppingList:
    """Re-fetch a list for serialization. populate_existing refreshes any
    instances already in the session so post-mutation reads are never stale."""
    result = await db.execute(
        select(ShoppingList).where(ShoppingList.id == list_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()
