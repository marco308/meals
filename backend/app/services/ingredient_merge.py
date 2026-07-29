"""Folding duplicate ingredient rows together (decision Q21).

`ingredient_names.canonical_ingredient_name` stops *new* duplicates arriving.
This is the other half: finding the ones already in the household's catalogue
and collapsing them, because an ingredient row is an identity that recipes,
meals and shopping-list lines all point at — it cannot simply be renamed away.

Detection is deliberately narrow. It reports only ingredients whose names fold
to the same canonical form, which is a fact rather than a guess. Anything
looser ("garlic" ⊂ "garlic bread") would propose merges that silently change
what someone buys, so the judgement calls stay with the household's AI, which
can call `merge_ingredients` on anything the table was too cautious to claim.
"""

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ingredient, ListItem, ListItemSource, MealIngredient, RecipeIngredient
from app.services.ingredient_names import canonical_ingredient_name


class MergeError(ValueError):
    """The merge was refused; the message says what to do instead."""


async def find_duplicate_groups(db: AsyncSession, household_id: uuid.UUID) -> list[list[Ingredient]]:
    """Groups of two or more ingredients that fold to the same canonical name,
    keeper first. The keeper is the row already stored under the canonical
    name if there is one, else the oldest — merging into the oldest keeps the
    aisle and value tier somebody has most likely already curated."""
    result = await db.execute(
        select(Ingredient).where(Ingredient.household_id == household_id).order_by(Ingredient.created_at)
    )
    by_canonical: dict[str, list[Ingredient]] = defaultdict(list)
    for ingredient in result.scalars():
        by_canonical[canonical_ingredient_name(ingredient.name) or ingredient.name].append(ingredient)

    groups = []
    for canonical, members in by_canonical.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda i: (i.name != canonical, i.created_at))
        groups.append(members)
    groups.sort(key=lambda group: group[0].name)
    return groups


async def find_unfolded(db: AsyncSession, household_id: uuid.UUID) -> list[tuple[Ingredient, str]]:
    """Ingredients stored under a name that predates the folding rules and has
    no duplicate to merge with — "garlic cloves" when there is no "garlic".
    Reported as `(ingredient, canonical name)` so a client can offer the
    rename, which it performs as a merge into the canonical name."""
    result = await db.execute(
        select(Ingredient).where(Ingredient.household_id == household_id).order_by(Ingredient.name)
    )
    ingredients = list(result.scalars())
    names = {ingredient.name for ingredient in ingredients}
    unfolded = []
    for ingredient in ingredients:
        canonical = canonical_ingredient_name(ingredient.name)
        if canonical and canonical != ingredient.name and canonical not in names:
            unfolded.append((ingredient, canonical))
    return unfolded


async def merge_ingredients(
    db: AsyncSession, household_id: uuid.UUID, keeper: Ingredient, duplicates: list[Ingredient]
) -> int:
    """Repoint every reference from `duplicates` onto `keeper` and delete them.

    Returns the number of ingredients absorbed. The keeper's own aisle, staple
    flag and value tier are left exactly as they are — those are the
    household's curation (Q17) and the merge has no opinion about them.
    """
    absorbed = 0
    for duplicate in duplicates:
        if duplicate.household_id != household_id:
            raise MergeError("ingredient not found in this household")
        if duplicate.id == keeper.id:
            continue
        await _repoint(db, RecipeIngredient, duplicate.id, keeper.id)
        await _repoint(db, MealIngredient, duplicate.id, keeper.id)
        await _merge_list_items(db, duplicate.id, keeper.id)
        await db.delete(duplicate)
        absorbed += 1
    await db.flush()
    return absorbed


async def _repoint(db: AsyncSession, model: type, from_id: uuid.UUID, to_id: uuid.UUID) -> None:
    result = await db.execute(select(model).where(model.ingredient_id == from_id))
    for row in result.scalars():
        row.ingredient_id = to_id
    await db.flush()


async def _merge_list_items(db: AsyncSession, from_id: uuid.UUID, to_id: uuid.UUID) -> None:
    """Move the duplicate's list lines onto the keeper, collapsing the ones
    that now collide on (list, ingredient, unit) — the same identity the rest
    of the shopping code merges on.

    Contributions move rather than being recomputed, so the line still knows
    *why* it is there (guiding principle 3) and its quantity stays the sum of
    its sources. Shop state is combined pessimistically: a line is only still
    ticked off if both halves were, so a need that was outstanding on either
    side comes back to the top of the list rather than being quietly bought.
    """
    moving = await db.execute(
        select(ListItem).where(ListItem.ingredient_id == from_id).execution_options(populate_existing=True)
    )
    for item in moving.scalars().all():
        existing = await db.execute(
            select(ListItem)
            .where(
                ListItem.list_id == item.list_id,
                ListItem.ingredient_id == to_id,
                ListItem.unit == item.unit,
                ListItem.id != item.id,
            )
            .execution_options(populate_existing=True)
        )
        target = existing.scalars().first()
        if target is None:
            item.ingredient_id = to_id
            continue
        target.checked = target.checked and item.checked
        target.excluded = target.excluded and item.excluded
        target.staple_needed = target.staple_needed or item.staple_needed
        sources = await db.execute(select(ListItemSource).where(ListItemSource.item_id == item.id))
        for source in sources.scalars().all():
            source.item_id = target.id
        await db.flush()
        # The collection was loaded before its rows moved away; without this
        # the delete cascades them straight back out again.
        await db.refresh(item, ["sources"])
        await db.delete(item)
    await db.flush()
