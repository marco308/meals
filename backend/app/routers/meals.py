import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import CurrentUser, DbSession
from app.models import Meal, MealIngredient, MealRecipe, Plan, PlanMeal
from app.schemas.common import IngredientLineIn
from app.schemas.planning import MealCreate, MealOut, MealUpdate
from app.serializers import meal_out
from app.services.catalog import get_or_create_ingredient, get_recipe
from app.services.shopping import get_active_list, remove_meal_contributions, resync_meal_contributions

router = APIRouter(prefix="/meals", tags=["meals"])


async def get_meal(db: AsyncSession, household_id: uuid.UUID, meal_id: uuid.UUID) -> Meal | None:
    result = await db.execute(
        select(Meal)
        .where(Meal.household_id == household_id, Meal.id == meal_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _set_recipes(db: AsyncSession, household_id: uuid.UUID, meal: Meal, recipe_ids: list[uuid.UUID]) -> None:
    existing = await db.execute(select(MealRecipe).where(MealRecipe.meal_id == meal.id))
    for link in existing.scalars():
        await db.delete(link)
    seen: set[uuid.UUID] = set()
    for recipe_id in recipe_ids:
        if recipe_id in seen:
            continue
        seen.add(recipe_id)
        recipe = await get_recipe(db, household_id, recipe_id)
        if recipe is None:
            raise HTTPException(
                status_code=422,
                detail=f"recipe {recipe_id} not found; browse the library via GET /recipes or ingest one first",
            )
        db.add(MealRecipe(meal_id=meal.id, recipe_id=recipe_id))
    await db.flush()


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
async def create_meal(payload: MealCreate, user: CurrentUser, db: DbSession) -> MealOut:
    """A meal = zero or more recipes plus loose ingredients — 'cottage pie
    with peas and carrots on the side' is one recipe and two loose
    ingredients, no recipe needed for the veg."""
    meal = Meal(household_id=user.household_id, name=payload.name.strip(), slot=_clean_slot(payload.slot))
    db.add(meal)
    await db.flush()
    await _set_recipes(db, user.household_id, meal, payload.recipe_ids)
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
    query = select(Meal).where(Meal.household_id == user.household_id).order_by(Meal.name)
    if search:
        query = query.where(Meal.name.ilike(f"%{search}%"))
    if slot:
        query = query.where(Meal.slot == slot.lower())
    result = await db.execute(query)
    return [meal_out(meal) for meal in result.scalars()]


@router.get("/{meal_id}", response_model=MealOut)
async def get_meal_detail(meal_id: uuid.UUID, user: CurrentUser, db: DbSession) -> MealOut:
    meal = await get_meal(db, user.household_id, meal_id)
    if meal is None:
        raise HTTPException(status_code=404, detail="meal not found; list meals via GET /meals")
    return meal_out(meal)


@router.patch("/{meal_id}", response_model=MealOut)
async def update_meal(meal_id: uuid.UUID, payload: MealUpdate, user: CurrentUser, db: DbSession) -> MealOut:
    """Rename, re-slot, or change composition. Composition changes re-sync
    the meal's contributions on the active shopping list."""
    meal = await get_meal(db, user.household_id, meal_id)
    if meal is None:
        raise HTTPException(status_code=404, detail="meal not found; list meals via GET /meals")
    if payload.name is not None:
        meal.name = payload.name.strip()
    if "slot" in payload.model_fields_set:
        meal.slot = _clean_slot(payload.slot)
    composition_changed = payload.recipe_ids is not None or payload.loose_ingredients is not None
    if payload.recipe_ids is not None:
        await _set_recipes(db, user.household_id, meal, payload.recipe_ids)
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


def _clean_slot(slot: str | None) -> str | None:
    if slot is None:
        return None
    cleaned = slot.strip().lower()
    return cleaned or None
