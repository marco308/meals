"""Shopping-list domain logic (guiding principle 3: the list knows *why*).

Every line links back to the meal(s)/recipe(s) that need it via
ListItemSource rows; ad-hoc rows are flagged as such. Adding a meal to the
plan merges its contributions into existing lines (exact canonical unit match
only, decision Q2); removing the meal decrements exactly what it added and
never touches ad-hoc contributions; editing it re-syncs by difference, so a
line keeps its id for as long as anything needs it.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ListItem, ListItemSource, Meal, PlanMeal, ShoppingList
from app.schemas.shopping import AdhocItemIn
from app.services.catalog import get_or_create_ingredient

Contribution = tuple[uuid.UUID, float | None, str | None, uuid.UUID | None]  # ingredient, quantity, unit, recipe
Key = tuple[uuid.UUID, str | None]  # (ingredient, unit): a line's identity on one list
Share = tuple[uuid.UUID | None, float | None]  # (recipe, quantity): one contribution to a line

#: Float dust left by netting one amount against another. A share smaller
#: than this is nothing to buy.
_NOTHING = 1e-6


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


def _meal_contributions(meal: Meal) -> list[Contribution]:
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
        db.add(
            ListItemSource(
                item_id=item.id, plan_meal_id=plan_meal.id, meal_name=meal.name, recipe_id=recipe_id, quantity=quantity
            )
        )
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

    # Never limited, in any tier. `/shopping-list*` is exempt from every billing
    # block exactly as it is exempt from the client gate (planning/08-freemium.md
    # §5): this is the endpoint the offline queue drains through, and iOS drops
    # any op the server refuses, so a cap here would destroy what someone typed
    # in a supermarket rather than reduce their features (Q11).
    ingredient = await get_or_create_ingredient(db, household_id, payload.name, count_against_limits=False)
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
            ad_hoc=True,
            quantity=payload.quantity,
            client_key=str(payload.id) if payload.id is not None else None,
        )
    )
    await db.flush()
    return item, True


def needed_by_a_plan(item: ListItem) -> bool:
    """Whether a meal still on a plan contributes to this line, which is the one
    thing that stops it being deleted by hand. A contribution whose plan-meal
    has since gone (an archived list's history) holds it no more than an
    ad-hoc one does."""
    return any(source.plan_meal_id is not None for source in item.sources)


async def archive_and_replace(db: AsyncSession, shopping_list: ShoppingList) -> ShoppingList:
    from app.models.users import utcnow

    shopping_list.status = "archived"
    shopping_list.archived_at = utcnow()
    fresh = ShoppingList(household_id=shopping_list.household_id)
    db.add(fresh)
    await db.flush()
    return fresh


def _by_line(contributions: list[Contribution]) -> dict[Key, list[Share]]:
    """A meal's contributions grouped by the line each lands on, in recipe order."""
    lines: dict[Key, list[Share]] = {}
    for ingredient_id, quantity, unit, recipe_id in contributions:
        lines.setdefault((ingredient_id, unit), []).append((recipe_id, quantity))
    return lines


def _total(quantities: Iterable[float | None]) -> float | None:
    """Shares added up as the line shows them (ListItem.quantity's rule): None
    when none of them states an amount."""
    amounts = [quantity for quantity in quantities if quantity is not None]
    return round(sum(amounts), 3) if amounts else None


def _still_to_buy(wanted: list[Share], bought: list[Share]) -> list[Share]:
    """The part of one line's need that its archived lists don't already hold.

    Netted per line, not per recipe: each share draws first on what was bought
    for its own recipe and then on whatever else this meal bought for the line,
    so swapping cottage pie for shepherd's pie after the shop doesn't send
    anyone back for the onion. A share with no amount ("salt to taste") is
    bought if anything on the line was."""
    if not bought:
        return wanted
    left: defaultdict[uuid.UUID | None, float] = defaultdict(float)
    for recipe_id, quantity in bought:
        left[recipe_id] += quantity or 0
    owed: list[tuple[uuid.UUID | None, float]] = []
    for recipe_id, quantity in wanted:
        if quantity is None:
            continue  # bought, because something on this line was
        drawn = min(quantity, left[recipe_id])
        left[recipe_id] -= drawn
        owed.append((recipe_id, quantity - drawn))
    pool = sum(left.values())
    still: list[Share] = []
    for recipe_id, amount in owed:
        drawn = min(amount, pool)
        pool -= drawn
        if amount - drawn > _NOTHING:
            still.append((recipe_id, amount - drawn))
    return still


async def resync_meal_contributions(db: AsyncSession, household_id: uuid.UUID, meal: Meal) -> None:
    """After a meal's (or one of its recipes') composition changes, bring its
    contributions on the active list into line, for every active plan it is in.

    A diff, never a rebuild: this meal's shares of each line are updated in
    place, added or removed, and a line goes only once nothing needs it. So a
    line keeps its id, and a tick the phone queued offline against that id
    still lands (Q11) rather than meeting a 404 and being dropped. A line whose
    need from this meal is unchanged keeps the state the shopper gave it:
    adding garlic bread must not quietly un-tick the mince already in the
    trolley. A changed need comes back unticked, which is the point of
    re-surfacing it, and keeps "already have it" and a staple's "I'm low",
    exactly as a fresh need from another meal does.

    What an archived list holds was bought ("Finish shop" archives the list
    while the plan runs on), so only what is still to buy lands on the active
    list: an edit after the shop adds the difference, not the whole meal."""
    from app.models import Plan

    active_list = await get_active_list(db, household_id)
    wanted = _by_line(_meal_contributions(meal))
    result = await db.execute(
        select(PlanMeal)
        .join(Plan, PlanMeal.plan_id == Plan.id)
        .where(PlanMeal.meal_id == meal.id, Plan.status == "active", Plan.household_id == household_id)
    )
    for plan_meal in result.scalars().all():
        await _resync_plan_meal(db, household_id, active_list, plan_meal, meal, wanted)


async def _resync_plan_meal(
    db: AsyncSession,
    household_id: uuid.UUID,
    active_list: ShoppingList,
    plan_meal: PlanMeal,
    meal: Meal,
    wanted: dict[Key, list[Share]],
) -> None:
    result = await db.execute(
        select(ListItem, ListItemSource, ShoppingList.status)
        .join(ListItemSource, ListItemSource.item_id == ListItem.id)
        .join(ShoppingList, ListItem.list_id == ShoppingList.id)
        .where(ListItemSource.plan_meal_id == plan_meal.id, ShoppingList.household_id == household_id)
        # refresh possibly-stale source collections so a line left empty is seen to be
        .execution_options(populate_existing=True)
    )
    # Each share is kept with the line holding it rather than looked up by key:
    # nothing stops two racing ad-hoc adds from making the same line twice.
    held: dict[Key, list[tuple[ListItem, ListItemSource]]] = {}
    bought: dict[Key, list[Share]] = {}
    for item, source, list_status in result.all():
        key = (item.ingredient_id, item.unit)
        if item.list_id == active_list.id:
            held.setdefault(key, []).append((item, source))
        elif list_status == "archived":
            bought.setdefault(key, []).append((source.recipe_id, source.quantity))

    for key in [*wanted, *(key for key in held if key not in wanted)]:
        shares = _still_to_buy(wanted.get(key, []), bought.get(key, []))
        old = held.get(key, [])
        if not shares and not old:
            continue
        before = _total(source.quantity for _, source in old)
        item = old[0][0] if old else await _find_item(db, active_list.id, *key)
        if item is None:
            item = ListItem(list_id=active_list.id, ingredient_id=key[0], unit=key[1], sources=[])
            db.add(item)

        unmatched = list(old)
        for recipe_id, quantity in shares:
            match = next((pair for pair in unmatched if pair[1].recipe_id == recipe_id), None)
            if match is None:
                item.sources.append(
                    ListItemSource(
                        plan_meal_id=plan_meal.id, meal_name=meal.name, recipe_id=recipe_id, quantity=quantity
                    )
                )
            else:
                unmatched.remove(match)
                match[1].quantity = quantity
        for line, source in unmatched:
            line.sources.remove(source)

        if shares and (not old or _total(quantity for _, quantity in shares) != before):
            item.checked = False
        for touched in dict.fromkeys([item, *(line for line, _ in old)]):
            if not touched.sources:
                await db.delete(touched)
    await db.flush()


async def get_list_full(db: AsyncSession, list_id: uuid.UUID) -> ShoppingList:
    """Re-fetch a list for serialization. populate_existing refreshes any
    instances already in the session so post-mutation reads are never stale."""
    result = await db.execute(
        select(ShoppingList).where(ShoppingList.id == list_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()
