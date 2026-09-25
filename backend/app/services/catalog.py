"""Ingredient and recipe persistence helpers shared by routers."""

import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import limits
from app.models import Ingredient, Recipe, RecipeIngredient
from app.schemas.catalog import MAX_RECIPE_LINES, MAX_RECIPE_MINUTES, MAX_SERVINGS, RecipeCreate
from app.schemas.common import IngredientLineIn
from app.services.aisles import guess_aisle
from app.services.ingredient_names import canonical_ingredient_name
from app.services.recipe_parser import SUBMIT_IT_YOURSELF, NoRecipeFound, ParsedRecipe


async def get_or_create_ingredient(
    db: AsyncSession, household_id: uuid.UUID, name: str, *, count_against_limits: bool = True
) -> Ingredient:
    """Ingredient names are the canonical key: 'chopped tomatoes' from two
    recipes resolves to one ingredient. New ingredients get a best-effort
    aisle from the built-in lookup table.

    Every write path — JSON-LD ingest, an AI's POST /recipes, a loose meal
    ingredient, an ad-hoc list add — lands here, which is why the name folding
    (Q21) belongs here and nowhere else: 'mint leaves' and 'mint' resolve to
    one ingredient however they arrived.

    It is also why the ingredient limit is applied here, and why the ad-hoc list
    add is the one caller that passes `count_against_limits=False`: adding milk
    to the shopping list must never be refused (planning/08-freemium.md §5). The
    offline queue replays through that endpoint and iOS drops any op the server
    rejects, so a limit there would delete what someone typed in a supermarket
    rather than merely capping them (Q11)."""
    # The fallback matters for names that fold to nothing (","): a punctuation
    # ingredient is bad data, an unnamed one breaks every client that shows it.
    canonical = canonical_ingredient_name(name) or " ".join(name.lower().split())
    result = await db.execute(
        select(Ingredient).where(Ingredient.household_id == household_id, Ingredient.name == canonical)
    )
    ingredient = result.scalar_one_or_none()
    if ingredient is None:
        if count_against_limits:
            await limits.enforce(db, household_id, "ingredients")
        ingredient = Ingredient(household_id=household_id, name=canonical, aisle=guess_aisle(canonical))
        db.add(ingredient)
        await db.flush()
    return ingredient


async def create_recipe_from_payload(
    db: AsyncSession,
    household_id: uuid.UUID,
    user_id: uuid.UUID | None,
    payload: RecipeCreate,
) -> Recipe:
    await limits.enforce(db, household_id, "recipes")
    recipe = Recipe(
        household_id=household_id,
        title=payload.title.strip(),
        source_url=payload.source_url,
        servings=payload.servings,
        prep_minutes=payload.prep_minutes,
        cook_minutes=payload.cook_minutes,
        image_url=payload.image_url,
        instructions=payload.instructions,
        tags=payload.tags,
        parse_source=payload.parse_source,
        created_by=user_id,
    )
    db.add(recipe)
    await db.flush()
    await set_recipe_ingredients(db, recipe, payload.ingredients)
    return recipe


async def update_recipe_from_payload(db: AsyncSession, recipe: Recipe, payload: RecipeCreate) -> None:
    """Replace a recipe's parsed content in place (issue #54).

    In place, because the recipe's id is what meals and their shopping-list
    contributions point at — re-parsing into a new row would strand every one
    of them. So everything that isn't the page's to say survives: the id, the
    `source_url` cache key (Q3), and the cooked history.
    """
    recipe.title = payload.title.strip()
    recipe.servings = payload.servings
    recipe.prep_minutes = payload.prep_minutes
    recipe.cook_minutes = payload.cook_minutes
    recipe.image_url = payload.image_url
    recipe.instructions = payload.instructions
    recipe.tags = payload.tags
    await set_recipe_ingredients(db, recipe, payload.ingredients)
    await db.flush()


async def set_recipe_ingredients(db: AsyncSession, recipe: Recipe, lines: list[IngredientLineIn]) -> None:
    existing = await db.execute(select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id))
    for link in existing.scalars():
        await db.delete(link)
    for position, line in enumerate(lines):
        ingredient = await get_or_create_ingredient(db, recipe.household_id, line.name)
        db.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                ingredient_id=ingredient.id,
                quantity=line.quantity,
                unit=line.unit,
                raw_text=line.raw,
                position=position,
            )
        )
    await db.flush()


def parsed_recipe_to_payload(parsed: ParsedRecipe) -> RecipeCreate:
    """Convert our JSON-LD parser's output into the same payload shape AI
    clients submit, so both ingestion paths share one code path.

    Everything in `parsed` is the page's say-so, so it is fitted to what a
    recipe can hold rather than validated and refused: a yield of "500 ml" is
    no serving count, and the 101st line is one too many. Whatever is refused
    even so becomes NoRecipeFound, the 422 that tells the caller to read the
    page itself, rather than a 500 on an ingest that has already been charged.
    """
    lines = []
    for item in parsed.ingredients[:MAX_RECIPE_LINES]:
        name = _storable(item.name.strip() or item.raw.strip())[:200]
        raw = _storable(item.raw)[:500]
        try:
            line = IngredientLineIn(name=name, quantity=item.quantity, unit=item.unit, raw=raw)
        except ValueError:
            # Parser output that fails the convention degrades to an
            # unquantified line rather than failing the whole ingest.
            try:
                line = IngredientLineIn(name=name, raw=raw)
            except ValueError:
                continue  # nothing left to call it by: a line that names nothing is no ingredient
        lines.append(line)
    try:
        return RecipeCreate(
            title=_storable(parsed.title).strip()[:300] or "Untitled recipe",
            source_url=parsed.source_url,
            servings=_within(parsed.servings, 1, MAX_SERVINGS),
            prep_minutes=_within(parsed.prep_minutes, 0, MAX_RECIPE_MINUTES),
            cook_minutes=_within(parsed.cook_minutes, 0, MAX_RECIPE_MINUTES),
            image_url=_storable(parsed.image_url)[:1000] if parsed.image_url else None,
            instructions=_storable(parsed.instructions) if parsed.instructions else None,
            tags=[tag for tag in (_storable(tag)[:50] for tag in parsed.tags) if tag],
            parse_source="manual",  # overwritten to 'jsonld' by the ingest router
            ingredients=lines,
        )
    except ValidationError as exc:
        problem = exc.errors()[0]
        where = ".".join(str(part) for part in problem["loc"])
        raise NoRecipeFound(
            f"this page's recipe could not be stored as it stands ({where}: {problem['msg']}); {SUBMIT_IT_YOURSELF}"
        ) from exc


def _within(value: int | None, low: int, high: int) -> int | None:
    """`value` when a recipe could hold it, else nothing: out of range is not
    something to round to the nearest allowed number."""
    return value if value is not None and low <= value <= high else None


def _storable(text: str) -> str:
    """Text a database will store and JSON can carry. A page's JSON-LD can
    escape a NUL, which Postgres refuses in a text column, or half a
    surrogate pair, which UTF-8 can't encode at all; neither is ever part of a
    recipe, and either one used to fail the ingest at the insert."""
    return text.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


async def get_recipe(db: AsyncSession, household_id: uuid.UUID, recipe_id: uuid.UUID) -> Recipe | None:
    result = await db.execute(
        select(Recipe)
        .where(Recipe.household_id == household_id, Recipe.id == recipe_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()
