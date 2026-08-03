import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.deps import CurrentUser, DbSession
from app.models import Ingredient, ListItem, MealIngredient, RecipeIngredient
from app.schemas.catalog import (
    DuplicateGroup,
    DuplicatesOut,
    IngredientOut,
    IngredientUpdate,
    MergeIn,
    MergeOut,
    UnfoldedIngredient,
)
from app.serializers import ingredient_out
from app.services.aisles import AISLES, is_valid_aisle
from app.services.catalog import get_or_create_ingredient
from app.services.ingredient_merge import MergeError, find_duplicate_groups, find_unfolded, merge_ingredients
from app.services.ingredient_names import canonical_ingredient_name
from app.services.supermarkets import effective_aisle_order, get_active_supermarket
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
async def list_aisles(user: CurrentUser, db: DbSession) -> list[AisleOut]:
    """The aisle vocabulary, in store-walking order — the same order the
    shopping list is sorted in. When the household has an active supermarket
    (see /supermarkets) this is that store's saved walking order; otherwise
    the built-in one."""
    market = await get_active_supermarket(db, user.household_id)
    labels = dict(AISLES)
    return [
        AisleOut(emoji=emoji, label=labels[emoji])
        for emoji in effective_aisle_order(market.aisle_order if market else None)
    ]


@router.get("/ingredients", response_model=list[IngredientOut])
async def list_ingredients(
    user: CurrentUser,
    db: DbSession,
    search: str | None = Query(default=None, max_length=200),
    name: str | None = Query(
        default=None,
        max_length=200,
        description="exact lookup, folded the same way a write is: name=fresh mint finds 'mint'",
    ),
    staples_only: bool = Query(default=False),
    value_tier: str | None = Query(default=None, description=f"filter by buying advice; {VALUE_TIER_HINT}"),
) -> list[IngredientOut]:
    """Filter by `value_tier=premium` for the shopping list of things the
    household has decided are worth paying up for (and `budget` for the
    own-brand-is-fine ones).

    `search` matches anywhere in the name. `name` is an exact lookup that
    folds what you send the same way a write would, so `name=mint leaves`
    finds the "mint" it was filed under — use it to resolve a name a user
    said into the ingredient it belongs to, rather than guessing from a
    substring hit."""
    if value_tier is not None and not is_valid_value_tier(value_tier):
        raise HTTPException(status_code=422, detail=f"unknown value tier '{value_tier}'; {VALUE_TIER_HINT}")
    query = select(Ingredient).where(Ingredient.household_id == user.household_id).order_by(Ingredient.name)
    if search:
        query = query.where(Ingredient.name.ilike(f"%{search.lower()}%"))
    if name is not None:
        query = query.where(Ingredient.name == canonical_ingredient_name(name))
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


@router.get("/ingredients/duplicates", response_model=DuplicatesOut)
async def list_duplicate_ingredients(user: CurrentUser, db: DbSession) -> DuplicatesOut:
    """Ingredients in this household that are the same food under two names —
    "mint" and "mint leaves", "garlic" and "garlic cloves".

    New writes are folded to one name automatically, so what shows up here is
    the catalogue as it was written before, plus anything an older client added.
    Each group's first entry is the suggested keeper; collapse a group with
    `POST /ingredients/{keeper_id}/merge`.

    `unfolded` is the related single-row case: an ingredient whose name would
    now be stored differently but that has no twin to merge with. To tidy one,
    `POST /ingredients` with its `canonical_name` and merge the old row into
    the ingredient that comes back.

    Reported groups are name-folding facts, not guesses — two ingredients that
    are really the same but share no wording ("beef mince" and "minced beef")
    won't appear. Merge those directly if you know they match."""
    groups = await find_duplicate_groups(db, user.household_id)
    unfolded = await find_unfolded(db, user.household_id)
    return DuplicatesOut(
        groups=[
            DuplicateGroup(
                canonical_name=canonical_ingredient_name(group[0].name) or group[0].name,
                keeper=ingredient_out(group[0]),
                duplicates=[ingredient_out(other) for other in group[1:]],
            )
            for group in groups
        ],
        unfolded=[
            UnfoldedIngredient(ingredient=ingredient_out(ingredient), canonical_name=canonical)
            for ingredient, canonical in unfolded
        ],
    )


@router.post("/ingredients/{ingredient_id}/merge", response_model=MergeOut)
async def merge_into_ingredient(
    ingredient_id: uuid.UUID, payload: MergeIn, user: CurrentUser, db: DbSession
) -> MergeOut:
    """Fold duplicate ingredients into this one, which survives.

    Everything pointing at a duplicate — recipe lines, a meal's loose
    ingredients, shopping-list lines and their contributions — is repointed
    here, so nothing loses track of why it is on the list. List lines that end
    up on the same (ingredient, unit) are combined, and a combined line is
    only still ticked off if both halves were.

    The keeper's aisle, staple flag and value tier are untouched: that
    curation is the household's, not the merge's. This is not reversible —
    the duplicate rows are deleted, not archived — so merge things that are
    genuinely the same food, not things bought together."""
    keeper = await db.get(Ingredient, ingredient_id)
    if keeper is None or keeper.household_id != user.household_id:
        raise HTTPException(status_code=404, detail="ingredient not found; list ingredients via GET /ingredients")
    duplicates = []
    for duplicate_id in payload.duplicate_ids:
        duplicate = await db.get(Ingredient, duplicate_id)
        if duplicate is None or duplicate.household_id != user.household_id:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"ingredient {duplicate_id} not found; merge only folds ingredients this household "
                    "owns — list them via GET /ingredients or GET /ingredients/duplicates"
                ),
            )
        duplicates.append(duplicate)
    try:
        absorbed = await merge_ingredients(db, user.household_id, keeper, duplicates)
    except MergeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return MergeOut(ingredient=ingredient_out(keeper), merged=absorbed)


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


@router.delete("/ingredients/{ingredient_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ingredient(ingredient_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    """Remove an ingredient nothing references any more — junk a bad parse
    left behind, a typo'd ad-hoc add (Q22).

    Refused while any recipe line, meal or shopping-list line (current or
    archived) still points at it: those rows are the record of why things are
    on the list. For a misparse or a duplicate of a real food,
    `POST /ingredients/{keeper_id}/merge` is usually the better tool — it
    repoints every reference onto the right ingredient and deletes this row in
    the same stroke. There is deliberately no automatic sweep of unreferenced
    ingredients: an unused row still carries the household's aisle, staple and
    value-tier curation (Q17), so deleting is always someone's decision."""
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None or ingredient.household_id != user.household_id:
        raise HTTPException(status_code=404, detail="ingredient not found; list ingredients via GET /ingredients")
    references = []
    for model, what in (
        (RecipeIngredient, "recipe line(s)"),
        (MealIngredient, "loose meal ingredient(s)"),
        (ListItem, "shopping-list line(s)"),
    ):
        result = await db.execute(select(func.count()).select_from(model).where(model.ingredient_id == ingredient.id))
        count = result.scalar_one()
        if count:
            references.append(f"{count} {what}")
    if references:
        raise HTTPException(
            status_code=409,
            detail=(
                f"ingredient '{ingredient.name}' is still referenced by {', '.join(references)}. "
                "If it is a misparse or a duplicate of another ingredient, POST /ingredients/{keeper_id}/merge "
                "repoints those references and deletes it in one step; otherwise edit the recipes "
                "(PATCH /recipes/{id}) and meals (PATCH /meals/{id}) that use it first."
            ),
        )
    await db.delete(ingredient)
    await db.commit()
