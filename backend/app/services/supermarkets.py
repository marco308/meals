"""Per-supermarket aisle orders — the household's own store walks.

The built-in order in `services/aisles.py` is the default. A household can
save one order per supermarket and mark one *active*; the active order drives
the shopping-list sort and `GET /aisles`. That endpoint is also how the iOS
app learns the order (it refetches on every list load and sorts locally), so
no client needs to know supermarkets exist to honour one.

A supermarket may also say where it is (Q25), so a phone can notice it is
standing in one and sort by that store's walk for itself. The match happens
on the device; the server only ever holds the store's coordinates.
"""

import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Supermarket
from app.services.aisles import AISLE_EMOJIS, AISLE_ORDER

# How close counts as "in the store" when a household hasn't said: a big
# supermarket's car park, not the next street. The bounds keep a typo from
# claiming a whole town, or a radius smaller than a phone's GPS error.
DEFAULT_RADIUS_M = 150
MIN_RADIUS_M = 50
MAX_RADIUS_M = 1000


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
    # Counted once, not rescanned per entry: order.count() in a loop was
    # quadratic, 15 seconds of the event loop for a 40,000-entry list.
    duplicates = sorted(emoji for emoji, seen in Counter(order).items() if seen > 1)
    if duplicates:
        return f"aisle(s) {' '.join(duplicates)} listed more than once; each aisle appears at most once"
    return None


def effective_radius(market: Supermarket) -> int | None:
    """The radius a client should match with; None when the store has no location."""
    if market.latitude is None or market.longitude is None:
        return None
    return market.radius_m if market.radius_m is not None else DEFAULT_RADIUS_M


HALF_LOCATION_DETAIL = (
    "send latitude and longitude together: a store's location needs both. To forget where it is, send both as null"
)


def is_half_location(latitude: float | None, longitude: float | None) -> bool:
    return (latitude is None) != (longitude is None)
