"""Freezer stock: what is in there, in batches (decision Q24).

A batch is a label, the portions left of it, and the date it went in. Rows
come and go with the food: adding a batch inserts one, taking the last
portion deletes it, so `SELECT * FROM freezer_items` *is* the freezer. There is
no history — what was eaten from the freezer is not something anyone asked to
know, and the cooked record already says what was made.

The label is denormalised on purpose. The meal or recipe a batch came from is
kept as a link so a client can jump to it, but the food does not stop being in
the freezer because the recipe was deleted, which is why both links are
`SET NULL` and the name lives here.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import limits
from app.models import FreezerItem


def today() -> date:
    return datetime.now(UTC).date()


async def list_freezer(db: AsyncSession, household_id: uuid.UUID) -> list[FreezerItem]:
    """Oldest batch first: the one to eat next is at the top."""
    result = await db.execute(
        select(FreezerItem)
        .where(FreezerItem.household_id == household_id)
        .order_by(FreezerItem.frozen_on, FreezerItem.created_at, FreezerItem.id)
    )
    return list(result.scalars())


async def get_item(db: AsyncSession, household_id: uuid.UUID, item_id: uuid.UUID) -> FreezerItem | None:
    item = await db.get(FreezerItem, item_id)
    if item is None or item.household_id != household_id:
        return None
    return item


async def add_batch(
    db: AsyncSession,
    household_id: uuid.UUID,
    *,
    label: str,
    portions: int,
    meal_id: uuid.UUID | None = None,
    recipe_id: uuid.UUID | None = None,
    note: str | None = None,
    frozen_on: date | None = None,
    user_id: uuid.UUID | None = None,
) -> FreezerItem:
    """One new batch. Never merges into an existing one: two batches of the
    same dish are two things with two dates, and the client shows the total."""
    await limits.enforce(db, household_id, "freezer_items")
    item = FreezerItem(
        household_id=household_id,
        label=label.strip(),
        meal_id=meal_id,
        recipe_id=recipe_id,
        portions=portions,
        note=(note or "").strip() or None,
        frozen_on=frozen_on or today(),
        created_by=user_id,
    )
    db.add(item)
    await db.flush()
    return item


async def take_portions(db: AsyncSession, item: FreezerItem, portions: int) -> int:
    """Eat `portions` from a batch and return how many are left.

    Asking for more than there is takes what there is — "we finished the
    chilli" when one portion was left is an answer, not a mistake — and a batch
    with nothing left is deleted rather than kept at zero, so the table only
    ever holds food that is actually in the freezer.
    """
    remaining = max(item.portions - portions, 0)
    if remaining == 0:
        await db.delete(item)
    else:
        item.portions = remaining
    await db.flush()
    return remaining
