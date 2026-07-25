import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import CurrentUser, DbSession
from app.models import Ingredient
from app.schemas.catalog import IngredientOut, IngredientUpdate
from app.serializers import ingredient_out
from app.services.aisles import AISLES, is_valid_aisle
from app.services.catalog import get_or_create_ingredient
from app.services.values import VALUE_TIER_HINT, is_valid_value_tier

router = APIRouter(tags=["ingredients"])


class AisleOut(BaseModel):
    emoji: str
    label: str


class IngredientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    aisle: str | None = None
    is_staple: bool = False
    value_tier: str | None = None
    value_note: str | None = Field(default=None, max_length=200)


@router.get("/aisles", response_model=list[AisleOut])
async def list_aisles(user: CurrentUser) -> list[AisleOut]:
    """The aisle vocabulary, in store-walking order — the same order the
    shopping list is sorted in."""
    return [AisleOut(emoji=emoji, label=label) for emoji, label in AISLES]


@router.get("/ingredients", response_model=list[IngredientOut])
async def list_ingredients(
    user: CurrentUser,
    db: DbSession,
    search: str | None = Query(default=None, max_length=200),
    staples_only: bool = Query(default=False),
    value_tier: str | None = Query(default=None, description=f"filter by buying advice; {VALUE_TIER_HINT}"),
) -> list[IngredientOut]:
    """Filter by `value_tier=premium` for the shopping list of things the
    household has decided are worth paying up for (and `budget` for the
    own-brand-is-fine ones)."""
    if value_tier is not None and not is_valid_value_tier(value_tier):
        raise HTTPException(status_code=422, detail=f"unknown value tier '{value_tier}'; {VALUE_TIER_HINT}")
    query = select(Ingredient).where(Ingredient.household_id == user.household_id).order_by(Ingredient.name)
    if search:
        query = query.where(Ingredient.name.ilike(f"%{search.lower()}%"))
    if staples_only:
        query = query.where(Ingredient.is_staple == True)  # noqa: E712
    if value_tier is not None:
        query = query.where(Ingredient.value_tier == value_tier)
    result = await db.execute(query)
    return [ingredient_out(ingredient) for ingredient in result.scalars()]


@router.post("/ingredients", response_model=IngredientOut, status_code=status.HTTP_201_CREATED)
async def create_ingredient(payload: IngredientCreate, user: CurrentUser, db: DbSession) -> IngredientOut:
    """Find-or-create by canonical name; useful for pre-tagging aisles,
    staples and premium/budget advice before any recipe references the
    ingredient."""
    if payload.aisle is not None and not is_valid_aisle(payload.aisle):
        raise HTTPException(
            status_code=422,
            detail=f"unknown aisle '{payload.aisle}'; valid aisles: {' '.join(e for e, _ in AISLES)}",
        )
    if payload.value_tier is not None and not is_valid_value_tier(payload.value_tier):
        raise HTTPException(status_code=422, detail=f"unknown value tier '{payload.value_tier}'; {VALUE_TIER_HINT}")
    ingredient = await get_or_create_ingredient(db, user.household_id, payload.name)
    if payload.aisle is not None:
        ingredient.aisle = payload.aisle
    if payload.is_staple:
        ingredient.is_staple = True
    if payload.value_tier is not None:
        ingredient.value_tier = payload.value_tier
    if payload.value_note is not None:
        ingredient.value_note = payload.value_note.strip() or None
    await db.commit()
    return ingredient_out(ingredient)


@router.patch("/ingredients/{ingredient_id}", response_model=IngredientOut)
async def update_ingredient(
    ingredient_id: uuid.UUID, payload: IngredientUpdate, user: CurrentUser, db: DbSession
) -> IngredientOut:
    """Set the aisle (e.g. an AI tagging an ❓ ingredient), toggle the staples
    flag (staples are hidden from the list by default), or record whether the
    premium version of this ingredient is worth it.

    `value_tier` is one of premium / budget / any — `any` clears the advice.
    `value_note` is the one-line reason shown next to the item while shopping
    ("the cheap stuff goes bitter"); send `null` or "" to clear it."""
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None or ingredient.household_id != user.household_id:
        raise HTTPException(status_code=404, detail="ingredient not found; list ingredients via GET /ingredients")
    if payload.aisle is not None:
        ingredient.aisle = payload.aisle
    if payload.is_staple is not None:
        ingredient.is_staple = payload.is_staple
    if payload.value_tier is not None:
        ingredient.value_tier = payload.value_tier
    if "value_note" in payload.model_fields_set:  # explicit null/"" clears the note
        ingredient.value_note = (payload.value_note or "").strip() or None
    await db.commit()
    return ingredient_out(ingredient)


@router.get("/ingredients/{ingredient_id}", response_model=IngredientOut)
async def get_ingredient(ingredient_id: uuid.UUID, user: CurrentUser, db: DbSession) -> IngredientOut:
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None or ingredient.household_id != user.household_id:
        raise HTTPException(status_code=404, detail="ingredient not found; list ingredients via GET /ingredients")
    return ingredient_out(ingredient)
