import uuid

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import limits
from app.deps import CurrentUser, DbSession
from app.models import Meal, MealIngredient, MealRecipe, Plan, PlanMeal
from app.schemas.common import IngredientLineIn
from app.schemas.planning import MealCreate, MealOut, MealRecipeIn, MealUpdate
from app.serializers import meal_out
from app.services.catalog import get_or_create_ingredient, get_recipe
from app.services.scaling import ScalingError, scale_for_servings
from app.services.shopping import get_active_list, remove_meal_contributions, resync_meal_contributions
from app.services.slots import apply_single_slot, clean_slot, clean_slots, meal_slots, set_meal_slots

router = APIRouter(prefix="/meals", tags=["meals"])


async def get_meal(db: AsyncSession, household_id: uuid.UUID, meal_id: uuid.UUID) -> Meal | None:
    result = await db.execute(
        select(Meal)
        .where(Meal.household_id == household_id, Meal.id == meal_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _resolve_scales(
    db: AsyncSession, household_id: uuid.UUID, lines: list[MealRecipeIn]
) -> dict[uuid.UUID, float]:
    """Each recipe once, at the scale it will be stored at, in payload order."""
    scales: dict[uuid.UUID, float] = {}
    for line in lines:
        if line.recipe_id in scales:
            continue
        recipe = await get_recipe(db, household_id, line.recipe_id)
        if recipe is None:
            raise HTTPException(
                status_code=422,
                detail=f"recipe {line.recipe_id} not found; browse the library via GET /recipes or ingest one first",
            )
        # Portions are resolved here rather than in the schema: the divisor is
        # the recipe's own servings, which only exists once it's been fetched.
        scale = line.scale
        if line.servings is not None:
            try:
                scale = scale_for_servings(recipe.title, recipe.servings, line.servings)
            except ScalingError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        scales[line.recipe_id] = scale
    return scales


async def _set_recipes(db: AsyncSession, household_id: uuid.UUID, meal: Meal, lines: list[MealRecipeIn]) -> None:
    scales = await _resolve_scales(db, household_id, lines)
    existing = await db.execute(select(MealRecipe).where(MealRecipe.meal_id == meal.id))
    for link in existing.scalars():
        await db.delete(link)
    await db.flush()  # the old links go before the new: (meal, recipe) is unique
    for recipe_id, scale in scales.items():
        db.add(MealRecipe(meal_id=meal.id, recipe_id=recipe_id, scale=scale))
    await db.flush()


async def _same_meal(
    db: AsyncSession, household_id: uuid.UUID, name: str, slots: list[str], scales: dict[uuid.UUID, float]
) -> Meal | None:
    """A meal this household already has that the one being posted would
    duplicate: same name (ignoring case), same slots, the same recipes at the
    same scales, and no extras. Extras are left out on purpose, because
    comparing them means canonicalising names and units the way an insert
    would, and the duplicates that actually happen are one recipe planned
    again from a phone, which never carries any."""
    result = await db.execute(
        select(Meal).where(Meal.household_id == household_id, func.lower(Meal.name) == name.lower())
    )
    for meal in result.scalars():
        if meal.name.lower() != name.lower() or meal_slots(meal) != slots or meal.ingredient_links:
            continue
        if {link.recipe_id: link.scale for link in meal.recipe_links} == scales:
            return meal
    return None


async def _set_loose_ingredients(
    db: AsyncSession, household_id: uuid.UUID, meal: Meal, lines: list[IngredientLineIn]
) -> None:
    existing = await db.execute(select(MealIngredient).where(MealIngredient.meal_id == meal.id))
    for link in existing.scalars():
        await db.delete(link)
    for line in lines:
        ingredient = await get_or_create_ingredient(db, household_id, line.name)
        db.add(MealIngredient(meal_id=meal.id, ingredient_id=ingredient.id, quantity=line.quantity, unit=line.unit))
    await db.flush()


@router.post("", response_model=MealOut, status_code=status.HTTP_201_CREATED)
async def create_meal(payload: MealCreate, user: CurrentUser, db: DbSession, response: Response) -> MealOut:
    """A meal = zero or more recipes plus loose ingredients — 'cottage pie
    with peas and carrots on the side' is one recipe and two loose
    ingredients, no recipe needed for the veg.

    Use `recipes: [{recipe_id, scale}]` instead of `recipe_ids` to batch-cook:
    scale 2 doubles that recipe's quantities on the shopping list without
    touching the recipe or any other meal using it.

    `{recipe_id, servings}` says the same thing in portions — 6 servings of a
    recipe that serves 4 is stored as scale 1.5 — and needs the recipe to say
    how many it serves. Send one or the other, never both. Each recipe comes
    back with its `scale` and the `scaled_servings` that follows from it.

    `slots` is every time of day the meal can fill — `["breakfast", "lunch"]`
    for a meal that works as either; `slot` is the one-slot spelling and
    comes back as the first of them.

    Posting a meal the household already has (same name, slots, recipes and
    scales, no extras) returns that meal with 200 instead of a copy, so
    "plan this recipe" can be sent every time without filling the library
    with duplicates."""
    name = payload.name.strip()
    slots = clean_slots(payload.slots if payload.slots is not None else [payload.slot or ""])
    if not payload.loose_ingredients:
        # Before the cap: reusing a meal adds nothing, so a household at its
        # limit can still put an old favourite back on the plan.
        scales = await _resolve_scales(db, user.household_id, payload.resolved_recipes)
        existing = await _same_meal(db, user.household_id, name, slots, scales)
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return meal_out(existing)
    await limits.enforce(db, user.household, "meals")
    # The lines are in the payload in front of us, so this needs no query.
    await limits.enforce(
        db,
        user.household,
        "meal_lines",
        used=len(payload.resolved_recipes) + len(payload.loose_ingredients),
        adding=0,
    )
    meal = Meal(household_id=user.household_id, name=name)
    set_meal_slots(meal, slots)
    db.add(meal)
    await db.flush()
    await _set_recipes(db, user.household_id, meal, payload.resolved_recipes)
    await _set_loose_ingredients(db, user.household_id, meal, payload.loose_ingredients)
    await db.commit()
    fresh = await get_meal(db, user.household_id, meal.id)
    assert fresh is not None
    return meal_out(fresh)


@router.get("", response_model=list[MealOut])
async def list_meals(
    user: CurrentUser,
    db: DbSession,
    search: str | None = Query(default=None, max_length=300),
    slot: str | None = Query(default=None, max_length=30),
) -> list[MealOut]:
    """`slot` keeps the meals that can fill it, whichever of their slots it is."""
    query = select(Meal).where(Meal.household_id == user.household_id).order_by(Meal.name)
    if search:
        query = query.where(Meal.name.ilike(f"%{search}%"))
    result = await db.execute(query)
    meals = list(result.scalars())
    # Filtered here rather than in SQL, like recipe tags: JSON membership is
    # spelt differently on SQLite and Postgres, and a household's meals are few.
    if wanted := clean_slot(slot):
        meals = [meal for meal in meals if wanted in meal_slots(meal)]
    return [meal_out(meal) for meal in meals]


@router.get("/{meal_id}", response_model=MealOut)
async def get_meal_detail(meal_id: uuid.UUID, user: CurrentUser, db: DbSession) -> MealOut:
    meal = await get_meal(db, user.household_id, meal_id)
    if meal is None:
        raise HTTPException(status_code=404, detail="meal not found; list meals via GET /meals")
    return meal_out(meal)


@router.patch("/{meal_id}", response_model=MealOut)
async def update_meal(meal_id: uuid.UUID, payload: MealUpdate, user: CurrentUser, db: DbSession) -> MealOut:
    """Rename, re-slot, or change composition. `slots` replaces the whole list
    (`[]` clears it); `slot` alone, re-sending one the meal already has,
    leaves the others where they are. Composition changes re-sync
    the meal's contributions on the active shopping list.

    Changing a recipe's `scale` is a composition change: send the whole
    `recipes: [{recipe_id, scale}]` list, and the list follows — ×1 to ×2
    re-surfaces exactly the doubled lines and leaves everything else ticked.
    `{recipe_id, servings}` scales the same way in portions instead."""
    meal = await get_meal(db, user.household_id, meal_id)
    if meal is None:
        raise HTTPException(status_code=404, detail="meal not found; list meals via GET /meals")
    if payload.name is not None:
        meal.name = payload.name.strip()
    if "slots" in payload.model_fields_set:
        set_meal_slots(meal, clean_slots(payload.slots))
    elif "slot" in payload.model_fields_set:
        apply_single_slot(meal, payload.slot)
    recipes = payload.resolved_recipes
    composition_changed = recipes is not None or payload.loose_ingredients is not None
    if composition_changed:
        # What the meal would hold once this PATCH lands: a half-sent payload
        # leaves the other half of the composition where it is.
        after = (len(recipes) if recipes is not None else len(meal.recipe_links)) + (
            len(payload.loose_ingredients) if payload.loose_ingredients is not None else len(meal.ingredient_links)
        )
        await limits.enforce(db, user.household, "meal_lines", used=after, adding=0)
    if recipes is not None:
        await _set_recipes(db, user.household_id, meal, recipes)
    if payload.loose_ingredients is not None:
        await _set_loose_ingredients(db, user.household_id, meal, payload.loose_ingredients)
    await db.flush()
    if composition_changed:
        fresh = await get_meal(db, user.household_id, meal.id)
        assert fresh is not None
        await resync_meal_contributions(db, user.household_id, fresh)
    await db.commit()
    fresh = await get_meal(db, user.household_id, meal.id)
    assert fresh is not None
    return meal_out(fresh)


@router.delete("/{meal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meal(meal_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    """Deleting a meal takes it off every plan and decrements its
    shopping-list contributions first. The cooked history survives (issue
    #13): `cooked_events` keeps its own copy of the name."""
    meal = await get_meal(db, user.household_id, meal_id)
    if meal is None:
        raise HTTPException(status_code=404, detail="meal not found; list meals via GET /meals")
    active_list = await get_active_list(db, user.household_id)
    # Status comes back with the row: touching plan_meal.plan here would be a
    # lazy load in async context.
    plan_meals = await db.execute(
        select(PlanMeal, Plan.status)
        .join(Plan, PlanMeal.plan_id == Plan.id)
        .where(PlanMeal.meal_id == meal.id, Plan.household_id == user.household_id)
    )
    # Delete the plan links here rather than leaning on the FK's ON DELETE
    # CASCADE: SQLite doesn't enforce foreign keys by default, so on a local or
    # test database the rows would survive as plan entries pointing at nothing
    # and the plan screen would 500 on the next read.
    for plan_meal, plan_status in plan_meals.all():
        if plan_status == "active":
            await remove_meal_contributions(db, active_list, plan_meal.id)
        await db.delete(plan_meal)
    await db.flush()
    await db.delete(meal)
    await db.commit()
