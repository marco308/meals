"""Per-supermarket aisle orders — the household's own store walks.

The built-in order in `services/aisles.py` is the default. A household can
save one order per supermarket and mark one *active*; the active order drives
the shopping-list sort and `GET /aisles`. That endpoint is also how the iOS
app learns the order (it refetches on every list load and sorts locally), so
no client needs to know supermarkets exist to honour one.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Supermarket
from app.services.aisles import AISLE_EMOJIS, AISLE_ORDER


async def get_active_supermarket(db: AsyncSession, household_id: uuid.UUID) -> Supermarket | None:
    result = await db.execute(
        select(Supermarket)
        .where(Supermarket.household_id == household_id, Supermarket.is_active == True)  # noqa: E712
        .order_by(Supermarket.created_at)
    )
    return result.scalars().first()


def effective_aisle_order(stored: list[str] | None) -> list[str]:
    """A complete aisle sequence from a stored (possibly stale) one.

    Emojis that left the vocabulary are dropped; aisles the row predates are
    appended in built-in order — so adding an aisle to `AISLES` never breaks
    a saved supermarket, it just walks the new aisle last until re-saved."""
    if not stored:
        return list(AISLE_EMOJIS)
    known: list[str] = []
    for emoji in stored:
        if emoji in AISLE_ORDER and emoji not in known:
            known.append(emoji)
    return known + [emoji for emoji in AISLE_EMOJIS if emoji not in known]


def invalid_aisle_order_detail(order: list[str]) -> str | None:
    """Why an aisle_order can't be saved, phrased for the calling AI; None when fine."""
    unknown = [emoji for emoji in order if emoji not in AISLE_ORDER]
    if unknown:
        return (
            f"unknown aisle(s) {' '.join(unknown)}; valid aisles: {' '.join(AISLE_EMOJIS)}. "
            "List them first-to-last as you walk the store — any you leave out keep "
            "their usual place at the end"
        )
    duplicates = sorted({emoji for emoji in order if order.count(emoji) > 1})
    if duplicates:
        return f"aisle(s) {' '.join(duplicates)} listed more than once; each aisle appears at most once"
    return None
