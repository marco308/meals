import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import CurrentUser, DbSession
from app.models import Plan, PlanMeal
from app.models.users import utcnow
from app.routers.meals import get_meal
from app.schemas.planning import AddMealIn, PlanCreate, PlanOut, PlanSummary
from app.serializers import plan_out, plan_summary
from app.services.cooking import record_cooked
from app.services.shopping import add_meal_contributions, get_active_list, remove_meal_contributions

router = APIRouter(prefix="/plans", tags=["plans"])


async def get_plan(db: AsyncSession, household_id: uuid.UUID, plan_id: uuid.UUID) -> Plan | None:
    result = await db.execute(
        select(Plan)
        .where(Plan.household_id == household_id, Plan.id == plan_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _add_meal_to_plan(db: AsyncSession, household_id: uuid.UUID, plan: Plan, meal_id: uuid.UUID) -> None:
    meal = await get_meal(db, household_id, meal_id)
    if meal is None:
        raise HTTPException(status_code=422, detail=f"meal {meal_id} not found; list meals via GET /meals")
    duplicate = await db.execute(select(PlanMeal).where(PlanMeal.plan_id == plan.id, PlanMeal.meal_id == meal_id))
    if duplicate.first() is not None:
        raise HTTPException(status_code=409, detail=f"meal '{meal.name}' is already in plan '{plan.label}'")
    plan_meal = PlanMeal(plan_id=plan.id, meal_id=meal_id)
    db.add(plan_meal)
    await db.flush()
    if plan.status == "active":
        active_list = await get_active_list(db, household_id)
        await add_meal_contributions(db, active_list, plan_meal, meal)


@router.post("", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(payload: PlanCreate, user: CurrentUser, db: DbSession) -> PlanOut:
    """Start a weekly-ish plan — a pool of meal options, never a calendar.
    Pass copy_from_plan_id to start from a previous week ('same as two weeks
    ago'); copied meals immediately contribute to the shopping list."""
    plan = Plan(household_id=user.household_id, label=payload.label.strip(), starts_on=payload.starts_on)
    db.add(plan)
    await db.flush()
    if payload.copy_from_plan_id is not None:
        source = await get_plan(db, user.household_id, payload.copy_from_plan_id)
        if source is None:
            raise HTTPException(
                status_code=422,
                detail=f"plan {payload.copy_from_plan_id} not found; list plans via GET /plans",
            )
        for link in source.meal_links:
            await _add_meal_to_plan(db, user.household_id, plan, link.meal_id)
    await db.commit()
    fresh = await get_plan(db, user.household_id, plan.id)
    assert fresh is not None
    return plan_out(fresh)


@router.get("", response_model=list[PlanSummary])
async def list_plans(
    user: CurrentUser,
    db: DbSession,
    plan_status: str | None = Query(default=None, alias="status", pattern="^(active|archived)$"),
) -> list[PlanSummary]:
    query = select(Plan).where(Plan.household_id == user.household_id).order_by(Plan.created_at.desc())
    if plan_status:
        query = query.where(Plan.status == plan_status)
    result = await db.execute(query)
    return [plan_summary(plan) for plan in result.scalars()]


@router.get("/current", response_model=PlanOut)
async def current_plan(user: CurrentUser, db: DbSession) -> PlanOut:
    """The most recent active plan — 'what are our options this week?'"""
    result = await db.execute(
        select(Plan)
        .where(Plan.household_id == user.household_id, Plan.status == "active")
        .order_by(Plan.created_at.desc())
        .execution_options(populate_existing=True)
    )
    plan = result.scalars().first()
    if plan is None:
        raise HTTPException(status_code=404, detail="no active plan; create one via POST /plans")
    return plan_out(plan)


@router.get("/{plan_id}", response_model=PlanOut)
async def get_plan_detail(plan_id: uuid.UUID, user: CurrentUser, db: DbSession) -> PlanOut:
    plan = await get_plan(db, user.household_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found; list plans via GET /plans")
    return plan_out(plan)


class PlanUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)


@router.patch("/{plan_id}", response_model=PlanOut)
async def update_plan(plan_id: uuid.UUID, payload: PlanUpdate, user: CurrentUser, db: DbSession) -> PlanOut:
    plan = await get_plan(db, user.household_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found; list plans via GET /plans")
    if payload.label is not None:
        plan.label = payload.label.strip()
    await db.commit()
    fresh = await get_plan(db, user.household_id, plan_id)
    assert fresh is not None
    return plan_out(fresh)


@router.post("/{plan_id}/meals", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def add_meal(plan_id: uuid.UUID, payload: AddMealIn, user: CurrentUser, db: DbSession) -> PlanOut:
    """Add a meal to the plan. All its recipe ingredients and loose
    ingredients land on the shopping list, merged into existing lines where
    the unit matches."""
    plan = await get_plan(db, user.household_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found; list plans via GET /plans")
    if plan.status != "active":
        raise HTTPException(
            status_code=409,
            detail=f"plan '{plan.label}' is archived; create a new plan (optionally copying it) via POST /plans",
        )
    await _add_meal_to_plan(db, user.household_id, plan, payload.meal_id)
    await db.commit()
    fresh = await get_plan(db, user.household_id, plan.id)
    assert fresh is not None
    return plan_out(fresh)


@router.delete("/{plan_id}/meals/{plan_meal_id}", response_model=PlanOut)
async def remove_meal(plan_meal_id: uuid.UUID, plan_id: uuid.UUID, user: CurrentUser, db: DbSession) -> PlanOut:
    """Remove a meal option ('scratch the burgers, we're out Friday'). Its
    shopping-list contributions are decremented; ad-hoc items are never
    touched."""
    plan = await get_plan(db, user.household_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found; list plans via GET /plans")
    plan_meal = await db.get(PlanMeal, plan_meal_id)
    if plan_meal is None or plan_meal.plan_id != plan.id:
        raise HTTPException(
            status_code=404,
            detail="that meal is not in this plan; use the plan-meal ids from GET /plans/current",
        )
    if plan.status == "active":
        active_list = await get_active_list(db, user.household_id)
        await remove_meal_contributions(db, active_list, plan_meal.id)
    await db.delete(plan_meal)
    await db.commit()
    fresh = await get_plan(db, user.household_id, plan.id)
    assert fresh is not None
    return plan_out(fresh)


@router.post("/{plan_id}/meals/{plan_meal_id}/cooked", response_model=PlanOut)
async def mark_cooked(plan_meal_id: uuid.UUID, plan_id: uuid.UUID, user: CurrentUser, db: DbSession) -> PlanOut:
    """Mark a meal option as cooked — the hook for future 'what do we
    actually eat' insights. Idempotent."""
    plan = await get_plan(db, user.household_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found; list plans via GET /plans")
    plan_meal = await db.get(PlanMeal, plan_meal_id)
    if plan_meal is None or plan_meal.plan_id != plan.id:
        raise HTTPException(
            status_code=404,
            detail="that meal is not in this plan; use the plan-meal ids from GET /plans/current",
        )
    if plan_meal.cooked_at is None:
        plan_meal.cooked_at = utcnow()
        meal = await get_meal(db, user.household_id, plan_meal.meal_id)
        if meal is not None:
            await record_cooked(db, user.household_id, plan_meal, meal, user.id)
    await db.commit()
    fresh = await get_plan(db, user.household_id, plan.id)
    assert fresh is not None
    return plan_out(fresh)


@router.post("/{plan_id}/archive", response_model=PlanOut)
async def archive_plan(plan_id: uuid.UUID, user: CurrentUser, db: DbSession) -> PlanOut:
    """Archive a plan. Its meals' contributions are removed from the active
    shopping list (an already-archived list keeps its history)."""
    plan = await get_plan(db, user.household_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found; list plans via GET /plans")
    if plan.status == "archived":
        return plan_out(plan)
    active_list = await get_active_list(db, user.household_id)
    for link in plan.meal_links:
        await remove_meal_contributions(db, active_list, link.id)
    plan.status = "archived"
    plan.archived_at = utcnow()
    await db.commit()
    fresh = await get_plan(db, user.household_id, plan.id)
    assert fresh is not None
    return plan_out(fresh)
