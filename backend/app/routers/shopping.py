import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from app.deps import CurrentUser, DbSession
from app.models import ListItem, ShoppingList
from app.schemas.shopping import AdhocItemIn, ArchiveOut, ListItemOut, ListItemUpdate, ShoppingListOut
from app.serializers import list_item_out, shopping_list_out
from app.services.shopping import add_adhoc_item, archive_and_replace, get_active_list, get_list_full, is_adhoc_only
from app.services.supermarkets import effective_aisle_order, get_active_supermarket

router = APIRouter(prefix="/shopping-list", tags=["shopping-list"])


@router.get("", response_model=ShoppingListOut)
async def get_shopping_list(
    user: CurrentUser,
    db: DbSession,
    include_staples: bool = Query(default=False, description="Reveal staple items for a pre-shop staples check"),
    include_excluded: bool = Query(default=False, description="Reveal items marked 'already have it'"),
) -> ShoppingListOut:
    """The live shopping list, sorted in store-walking order (aisle emoji,
    then name). Staples and 'already have it' items are hidden by default;
    hidden_staples says how many are waiting for a staples check. A staple
    marked staple_needed ("I'm low") stays on the list in its aisle.

    The walk follows the household's active supermarket when one is set
    (`supermarket` names it — see /supermarkets); otherwise the built-in
    order."""
    active = await get_active_list(db, user.household_id)
    await db.commit()  # persist the list if it was just created
    full = await get_list_full(db, active.id)
    market = await get_active_supermarket(db, user.household_id)
    return shopping_list_out(
        full,
        include_staples=include_staples,
        include_excluded=include_excluded,
        supermarket=market,
        aisle_sequence=effective_aisle_order(market.aisle_order) if market else None,
    )


@router.post("/items", response_model=ListItemOut, status_code=status.HTTP_201_CREATED)
async def add_item(payload: AdhocItemIn, user: CurrentUser, db: DbSession, response: Response) -> ListItemOut:
    """Fast ad-hoc add — the milk-is-out case. Merges into an existing line
    when the ingredient and unit match. Supply a client-generated id to make
    the call idempotent (safe retries, offline sync); a replayed id returns
    200 with the existing line."""
    active = await get_active_list(db, user.household_id)
    item, created = await add_adhoc_item(db, user.household_id, active, payload)
    await db.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
    result = await db.execute(select(ListItem).where(ListItem.id == item.id).execution_options(populate_existing=True))
    return list_item_out(result.scalar_one())


@router.patch("/items/{item_id}", response_model=ListItemOut)
async def update_item(item_id: uuid.UUID, payload: ListItemUpdate, user: CurrentUser, db: DbSession) -> ListItemOut:
    """Check items off while shopping, mark 'already have it' (excluded —
    hidden from this shop without losing why it was needed), or mark a staple
    as needed during a staples check (staple_needed — that one staple joins
    the main list; false hides it again)."""
    item = await _get_item(db, user.household_id, item_id)
    if payload.checked is not None:
        item.checked = payload.checked
    if payload.excluded is not None:
        item.excluded = payload.excluded
    if payload.staple_needed is not None:
        item.staple_needed = payload.staple_needed
    await db.commit()
    result = await db.execute(select(ListItem).where(ListItem.id == item.id).execution_options(populate_existing=True))
    return list_item_out(result.scalar_one())


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    """Delete an ad-hoc line. Lines that a planned meal needs cannot be
    deleted — remove the meal from the plan, or mark the line excluded."""
    item = await _get_item(db, user.household_id, item_id)
    if not is_adhoc_only(item):
        meals = sorted({s.plan_meal.meal.name for s in item.sources if s.plan_meal is not None})
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{item.ingredient.name}' is needed by: {', '.join(meals)}. Remove those meals from the plan "
                "(DELETE /plans/{plan_id}/meals/{plan_meal_id}) or mark the item excluded "
                '(PATCH /shopping-list/items/{id} {"excluded": true})'
            ),
        )
    await db.delete(item)
    await db.commit()


@router.post("/archive", response_model=ArchiveOut)
async def archive_shopping_list(user: CurrentUser, db: DbSession) -> ArchiveOut:
    """Finish the shop: archive the current list and start a fresh empty one."""
    active = await get_active_list(db, user.household_id)
    fresh = await archive_and_replace(db, active)
    await db.commit()
    return ArchiveOut(archived_list_id=active.id, new_list_id=fresh.id)


class ArchivedListSummary(BaseModel):
    id: uuid.UUID
    created_at: datetime
    archived_at: datetime | None
    item_count: int


@router.get("/archived", response_model=list[ArchivedListSummary])
async def list_archived(user: CurrentUser, db: DbSession) -> list[ArchivedListSummary]:
    result = await db.execute(
        select(ShoppingList)
        .where(ShoppingList.household_id == user.household_id, ShoppingList.status == "archived")
        .order_by(ShoppingList.archived_at.desc())
    )
    return [
        ArchivedListSummary(
            id=lst.id, created_at=lst.created_at, archived_at=lst.archived_at, item_count=len(lst.items)
        )
        for lst in result.scalars()
    ]


async def _get_item(db: DbSession, household_id: uuid.UUID, item_id: uuid.UUID) -> ListItem:
    result = await db.execute(
        select(ListItem)
        .join(ShoppingList, ListItem.list_id == ShoppingList.id)
        .where(ListItem.id == item_id, ShoppingList.household_id == household_id)
        .execution_options(populate_existing=True)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="list item not found; fetch current items via GET /shopping-list")
    return item
