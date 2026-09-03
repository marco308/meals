"""Freezer stock (decision Q24): a running tab of what is in the freezer, in
batches, each linked to the meal or recipe it came from when it came from the
app and free text when it didn't.

Nothing here touches the plan or the shopping list. Freezing a batch is a
statement about the freezer, not about the week, and eating from it is not a
cooking — the cooked record was written when the batch was made.
"""

import uuid

from fastapi import APIRouter, HTTPException, status

from app.deps import CurrentUser, DbSession
from app.routers.meals import get_meal
from app.schemas.freezer import FreezerAddIn, FreezerItemOut, FreezerItemUpdate, FreezerOut, FreezerTakeIn
from app.serializers import freezer_item_out
from app.services.catalog import get_recipe
from app.services.freezer import add_batch, get_item, list_freezer, take_portions

router = APIRouter(prefix="/freezer", tags=["freezer"])

NOT_FOUND = "that batch is not in the freezer; list what is via GET /freezer"


@router.get("", response_model=FreezerOut)
async def get_freezer(user: CurrentUser, db: DbSession) -> FreezerOut:
    """What is in the freezer, oldest batch first — that is the one to eat
    next. Each batch says how many portions are left and, when it came from
    the app, which meal or recipe it was; `total_portions` is the whole
    freezer in one number."""
    items = await list_freezer(db, user.household_id)
    return FreezerOut(items=[freezer_item_out(item) for item in items], total_portions=sum(i.portions for i in items))


@router.post("", response_model=FreezerItemOut, status_code=status.HTTP_201_CREATED)
async def add_to_freezer(payload: FreezerAddIn, user: CurrentUser, db: DbSession) -> FreezerItemOut:
    """Put a batch in the freezer. Name it one way: `meal_id` for a meal
    (the batch takes the meal's name), `recipe_id` for a recipe (the title),
    or `label` for anything that did not come through the app — "mum's
    lasagne", "chicken stock". `portions` is how many are going in
    (default 1); `frozen_on` defaults to today.

    Every call adds a new batch, even for a dish already in there: two
    batches frozen a month apart are two things to eat oldest-first, and
    GET /freezer totals them. To correct a count use PATCH /freezer/{item_id}."""
    if payload.meal_id is not None:
        meal = await get_meal(db, user.household_id, payload.meal_id)
        if meal is None:
            raise HTTPException(status_code=422, detail=f"meal {payload.meal_id} not found; list meals via GET /meals")
        label, meal_id, recipe_id = meal.name, meal.id, None
    elif payload.recipe_id is not None:
        recipe = await get_recipe(db, user.household_id, payload.recipe_id)
        if recipe is None:
            raise HTTPException(
                status_code=422, detail=f"recipe {payload.recipe_id} not found; browse the library via GET /recipes"
            )
        label, meal_id, recipe_id = recipe.title, None, recipe.id
    else:
        assert payload.label is not None  # the schema validator guarantees exactly one
        if not payload.label.strip():
            raise HTTPException(status_code=422, detail="label cannot be blank; say what the batch is")
        label, meal_id, recipe_id = payload.label, None, None
    item = await add_batch(
        db,
        user.household_id,
        label=label,
        portions=payload.portions,
        meal_id=meal_id,
        recipe_id=recipe_id,
        note=payload.note,
        frozen_on=payload.frozen_on,
        user_id=user.id,
    )
    await db.commit()
    return freezer_item_out(item)


@router.patch("/{item_id}", response_model=FreezerItemOut)
async def update_freezer_item(
    item_id: uuid.UUID, payload: FreezerItemUpdate, user: CurrentUser, db: DbSession
) -> FreezerItemOut:
    """Correct a batch: set `portions` outright (a recount), rename it, change
    the note or the date it went in. To eat from it use
    POST /freezer/{item_id}/take instead, which also clears an emptied batch."""
    item = await get_item(db, user.household_id, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if payload.label is not None:
        if not payload.label.strip():
            raise HTTPException(status_code=422, detail="label cannot be blank; say what the batch is")
        item.label = payload.label.strip()
    if payload.portions is not None:
        item.portions = payload.portions
    if "note" in payload.model_fields_set:
        item.note = (payload.note or "").strip() or None
    if payload.frozen_on is not None:
        item.frozen_on = payload.frozen_on
    await db.commit()
    return freezer_item_out(item)


@router.post("/{item_id}/take", response_model=FreezerItemOut)
async def take_from_freezer(
    item_id: uuid.UUID, payload: FreezerTakeIn, user: CurrentUser, db: DbSession
) -> FreezerItemOut:
    """Take portions out to eat (default 1). The reply is the batch with what
    is left; when that reaches 0 the batch is gone from the freezer and the
    reply is the last you will hear of it. Asking for more than is there
    takes what is there — it is how "we finished the chilli" is said."""
    item = await get_item(db, user.household_id, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    snapshot = freezer_item_out(item)  # taken before the row may be deleted
    remaining = await take_portions(db, item, payload.portions)
    await db.commit()
    return snapshot.model_copy(update={"portions": remaining})


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_freezer(item_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    """Take a whole batch out, whatever is left of it — binned, given away, or
    never there. Nothing else is touched."""
    item = await get_item(db, user.household_id, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    await db.delete(item)
    await db.commit()
