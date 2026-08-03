"""Per-supermarket aisle orders.

Each household can save the aisle walking order for the stores it actually
shops at and mark one *active*. The active order is what `GET /shopping-list`
sorts by and what `GET /aisles` returns — clients (including iOS, which
refetches `/aisles` every list load) follow along without knowing
supermarkets exist. No active supermarket = the built-in order.
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update

from app.deps import CurrentUser, DbSession
from app.models import Supermarket
from app.schemas.shopping import SupermarketCreate, SupermarketOut, SupermarketUpdate
from app.services.supermarkets import effective_aisle_order, invalid_aisle_order_detail

router = APIRouter(prefix="/supermarkets", tags=["supermarkets"])


def _out(market: Supermarket) -> SupermarketOut:
    return SupermarketOut(
        id=market.id,
        name=market.name,
        aisle_order=effective_aisle_order(market.aisle_order),
        is_active=market.is_active,
        created_at=market.created_at,
    )


@router.get("", response_model=list[SupermarketOut])
async def list_supermarkets(user: CurrentUser, db: DbSession) -> list[SupermarketOut]:
    """The household's supermarkets and their saved aisle walking orders.

    The `is_active` one decides the order the shopping list is sorted in and
    the order `GET /aisles` returns; when none is active the built-in
    store-walking order applies."""
    result = await db.execute(
        select(Supermarket).where(Supermarket.household_id == user.household_id).order_by(Supermarket.created_at)
    )
    return [_out(market) for market in result.scalars()]


@router.post("", response_model=SupermarketOut, status_code=status.HTTP_201_CREATED)
async def create_supermarket(payload: SupermarketCreate, user: CurrentUser, db: DbSession) -> SupermarketOut:
    """Save a supermarket and, optionally, its aisle walking order.

    `aisle_order` is aisle emojis first-to-last as you walk that store; any
    you leave out keep their usual place at the end, and omitting it entirely
    starts from the built-in order. `is_active: true` makes it the order the
    shopping list sorts in right away."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="supermarket name cannot be blank")
    existing = await _find_by_name(db, user.household_id, name)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"a supermarket called '{existing.name}' already exists; change its order with "
                f"PATCH /supermarkets/{existing.id}, or pick another name"
            ),
        )
    if payload.aisle_order is not None:
        problem = invalid_aisle_order_detail(payload.aisle_order)
        if problem is not None:
            raise HTTPException(status_code=422, detail=problem)
    market = Supermarket(
        household_id=user.household_id,
        name=name,
        aisle_order=payload.aisle_order or [],
        is_active=payload.is_active,
    )
    if payload.is_active:
        await _deactivate_all(db, user.household_id)
    db.add(market)
    await db.commit()
    return _out(market)


@router.patch("/{supermarket_id}", response_model=SupermarketOut)
async def update_supermarket(
    supermarket_id: uuid.UUID, payload: SupermarketUpdate, user: CurrentUser, db: DbSession
) -> SupermarketOut:
    """Rename a supermarket, replace its aisle walking order, or switch the
    active store: `{"is_active": true}` sorts the shopping list for this one
    ("we're at Aldi today"), `{"is_active": false}` goes back to the built-in
    order."""
    market = await _get(db, user.household_id, supermarket_id)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="supermarket name cannot be blank")
        clash = await _find_by_name(db, user.household_id, name)
        if clash is not None and clash.id != market.id:
            raise HTTPException(
                status_code=409, detail=f"a supermarket called '{clash.name}' already exists; pick another name"
            )
        market.name = name
    if payload.aisle_order is not None:
        problem = invalid_aisle_order_detail(payload.aisle_order)
        if problem is not None:
            raise HTTPException(status_code=422, detail=problem)
        market.aisle_order = payload.aisle_order
    if payload.is_active is not None:
        if payload.is_active:
            # keep= skips this row: bulk-updating it while its ORM attribute
            # still reads True would leave the flag flipped off in the database
            # with no ORM change event to flip it back.
            await _deactivate_all(db, user.household_id, keep=market.id)
        market.is_active = payload.is_active
    await db.commit()
    return _out(market)


@router.delete("/{supermarket_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supermarket(supermarket_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    """Delete a saved supermarket. If it was the active one the shopping list
    goes back to the built-in store-walking order."""
    market = await _get(db, user.household_id, supermarket_id)
    await db.delete(market)
    await db.commit()


async def _get(db: DbSession, household_id: uuid.UUID, supermarket_id: uuid.UUID) -> Supermarket:
    market = await db.get(Supermarket, supermarket_id)
    if market is None or market.household_id != household_id:
        raise HTTPException(status_code=404, detail="supermarket not found; list them via GET /supermarkets")
    return market


async def _find_by_name(db: DbSession, household_id: uuid.UUID, name: str) -> Supermarket | None:
    # Case-insensitive: "tesco" next to "Tesco" would be two stores nobody wanted.
    result = await db.execute(select(Supermarket).where(Supermarket.household_id == household_id))
    folded = name.lower()
    for market in result.scalars():
        if market.name.lower() == folded:
            return market
    return None


async def _deactivate_all(db: DbSession, household_id: uuid.UUID, keep: uuid.UUID | None = None) -> None:
    query = update(Supermarket).where(
        Supermarket.household_id == household_id,
        Supermarket.is_active == True,  # noqa: E712
    )
    if keep is not None:
        query = query.where(Supermarket.id != keep)
    await db.execute(query.values(is_active=False))
