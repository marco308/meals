import uuid
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import nullsfirst, select

from app import limits
from app.deps import CurrentUser, DbSession
from app.models import MealRecipe, Recipe
from app.observability import log_event
from app.schemas.catalog import IngestIn, IngestOut, RecipeCreate, RecipeOut, RecipeSummary, RecipeUpdate, ReparseIn
from app.serializers import recipe_out, recipe_summary
from app.services import recipe_parser
from app.services.catalog import (
    create_recipe_from_payload,
    get_recipe,
    set_recipe_ingredients,
    update_recipe_from_payload,
)
from app.services.recipe_parser import NoRecipeFound, RecipeFetchError
from app.services.shopping import resync_meal_contributions

router = APIRouter(prefix="/recipes", tags=["recipes"])


async def _find_by_url(db: DbSession, household_id: uuid.UUID, url: str) -> Recipe | None:
    result = await db.execute(select(Recipe).where(Recipe.household_id == household_id, Recipe.source_url == url))
    return result.scalar_one_or_none()


async def _resync_meals_using(db: DbSession, household_id: uuid.UUID, recipe: Recipe) -> None:
    """Re-contribute every meal that holds this recipe, so the active shopping
    list never disagrees with the recipe its lines came from. Call after any
    change to a recipe's ingredients."""
    meal_links = await db.execute(select(MealRecipe).where(MealRecipe.recipe_id == recipe.id))
    for meal_link in meal_links.scalars().all():
        from app.routers.meals import get_meal

        meal = await get_meal(db, household_id, meal_link.meal_id)
        if meal is not None:
            await resync_meal_contributions(db, household_id, meal)


def _sort_order(sort: str) -> tuple:
    """Title is always the tiebreak so ordering is stable across requests."""
    if sort == "most_cooked":
        return (Recipe.times_cooked.desc(), Recipe.title)
    if sort == "least_recently_cooked":
        # Never cooked sorts first: "we've not had this in ages" includes
        # "we've never had this".
        return (nullsfirst(Recipe.last_cooked_at.asc()), Recipe.title)
    return (Recipe.title,)


@router.post("/ingest", response_model=IngestOut)
async def ingest_recipe_url(payload: IngestIn, user: CurrentUser, db: DbSession) -> IngestOut:
    """Submit a recipe URL. A URL already in the library returns the cached
    recipe instantly (parse once, reuse forever). New URLs are fetched and
    parsed from their schema.org/Recipe JSON-LD — no LLM involved. Only public
    http(s) pages are fetched: an address that isn't publicly routed (private,
    loopback, link-local, carrier-grade NAT) is refused rather than reached.
    Failure is a 422 either way — the page has no
    usable JSON-LD, or this server couldn't fetch it (bot-blocked, unreachable,
    not public) — and the detail tells the calling AI to read the page itself
    and submit the structured recipe via POST /recipes."""
    url = payload.url.strip()
    # The event logs the host, never the full URL: which *sites* parse, block,
    # or lack JSON-LD is the operational question, and full URLs are what the
    # household is having for dinner.
    host = urlparse(url).hostname
    cached = await _find_by_url(db, user.household_id, url)
    if cached is not None:
        log_event("recipe.ingested", outcome="cached", host=host, recipe_id=cached.id)
        return IngestOut(recipe=recipe_out(cached), cached=True)

    # Charged before the fetch and committed, because the bandwidth is spent
    # whether or not the page turns out to be readable — see limits.reserve_ingest.
    # A cached URL never reaches here, which is why the allowance can be small.
    await limits.reserve_ingest(db, user.household)

    try:
        html = await recipe_parser.fetch_page(url)
    except RecipeFetchError as exc:
        # 422, not 502: proxies in front of a deployment (Cloudflare) replace
        # origin 5xx bodies with their own error page, which would strip the
        # read-the-page-yourself guidance — and that guidance is the product.
        log_event("recipe.ingested", outcome="fetch_failed", host=host)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        parsed = recipe_parser.extract_recipe(html, url)
    except NoRecipeFound as exc:
        log_event("recipe.ingested", outcome="no_jsonld", host=host)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    from app.services.catalog import parsed_recipe_to_payload

    recipe_payload = parsed_recipe_to_payload(parsed)
    recipe = await create_recipe_from_payload(db, user.household_id, user.id, recipe_payload)
    recipe.parse_source = "jsonld"
    await db.commit()
    log_event("recipe.ingested", outcome="parsed", host=host, recipe_id=recipe.id)
    fresh = await get_recipe(db, user.household_id, recipe.id)
    assert fresh is not None
    return IngestOut(recipe=recipe_out(fresh), cached=False)


@router.post("", response_model=RecipeOut, status_code=status.HTTP_201_CREATED)
async def create_recipe(payload: RecipeCreate, user: CurrentUser, db: DbSession, response: Response) -> RecipeOut:
    """Create a recipe: manual entry (no URL) or a structured recipe an AI
    parsed from a page (set parse_source='ai' and include source_url).
    Submitting a source_url that already exists returns the stored recipe
    unchanged with 200 — retries never duplicate, and human edits are never
    clobbered."""
    if payload.source_url is not None:
        existing = await _find_by_url(db, user.household_id, payload.source_url)
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return recipe_out(existing)
    recipe = await create_recipe_from_payload(db, user.household_id, user.id, payload)
    await db.commit()
    if payload.parse_source == "ai" and payload.source_url is not None:
        # The other half of the ingest funnel: /recipes/ingest 422'd on this
        # page and an AI read it itself, as the error told it to.
        log_event(
            "recipe.ingested", outcome="ai_parsed", host=urlparse(payload.source_url).hostname, recipe_id=recipe.id
        )
    fresh = await get_recipe(db, user.household_id, recipe.id)
    assert fresh is not None
    return recipe_out(fresh)


@router.get("", response_model=list[RecipeSummary])
async def list_recipes(
    user: CurrentUser,
    db: DbSession,
    search: str | None = Query(default=None, max_length=300),
    tag: str | None = Query(default=None, max_length=50),
    max_total_minutes: int | None = Query(default=None, ge=1),
    sort: Literal["title", "most_cooked", "least_recently_cooked"] = Query(
        default="title",
        description=(
            "title (default), most_cooked ('our regulars'), or least_recently_cooked "
            "('what haven't we had in ages' — never-cooked recipes come first)"
        ),
    ),
) -> list[RecipeSummary]:
    query = select(Recipe).where(Recipe.household_id == user.household_id).order_by(*_sort_order(sort))
    if search:
        query = query.where(Recipe.title.ilike(f"%{search}%"))
    result = await db.execute(query)
    recipes = list(result.scalars())
    if tag:
        recipes = [r for r in recipes if any(t.lower() == tag.lower() for t in (r.tags or []))]
    if max_total_minutes is not None:
        recipes = [r for r in recipes if ((r.prep_minutes or 0) + (r.cook_minutes or 0)) <= max_total_minutes]
    return [recipe_summary(recipe) for recipe in recipes]


@router.get("/{recipe_id}", response_model=RecipeOut)
async def get_recipe_detail(recipe_id: uuid.UUID, user: CurrentUser, db: DbSession) -> RecipeOut:
    recipe = await get_recipe(db, user.household_id, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="recipe not found; browse the library via GET /recipes")
    return recipe_out(recipe)


@router.patch("/{recipe_id}", response_model=RecipeOut)
async def update_recipe(recipe_id: uuid.UUID, payload: RecipeUpdate, user: CurrentUser, db: DbSession) -> RecipeOut:
    """Correct a parsed recipe. Edits mark the recipe as human-edited so
    nothing automated overwrites them. If the recipe is part of a meal on an
    active plan, the shopping list is re-synced to the new ingredients."""
    recipe = await get_recipe(db, user.household_id, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="recipe not found; browse the library via GET /recipes")

    updates = payload.model_dump(exclude_unset=True, exclude={"ingredients"})
    for field, value in updates.items():
        setattr(recipe, field, value)
    ingredients_changed = payload.ingredients is not None
    if payload.ingredients is not None:
        await set_recipe_ingredients(db, recipe, payload.ingredients)
    recipe.edited = True
    await db.flush()

    if ingredients_changed:
        await _resync_meals_using(db, user.household_id, recipe)
    await db.commit()
    fresh = await get_recipe(db, user.household_id, recipe.id)
    assert fresh is not None
    return recipe_out(fresh)


@router.post("/{recipe_id}/reparse", response_model=RecipeOut)
async def reparse_recipe(recipe_id: uuid.UUID, payload: ReparseIn, user: CurrentUser, db: DbSession) -> RecipeOut:
    """Re-read this recipe from its `source_url` and replace what the page
    says: title, servings, times, image, instructions, tags and ingredients.

    Parse once, reuse forever (Q3) is the default because pages rarely change
    and re-fetching every time would be slow and rude. This is the exception,
    for when the source has actually been corrected — so it is never automatic,
    only something you ask for.

    The recipe keeps its id, so every meal and shopping-list line pointing at
    it still does, and any meal on the active plan re-syncs to the new
    ingredients. It also keeps its `source_url` and its cooked history, and
    your ingredients keep their aisles, staple flags and value tiers — that
    curation lives on the ingredient, not on the recipe line.

    **A recipe you have edited here is refused with a 409**, because the page
    would silently overwrite your corrections. Send `{"force": true}` to
    re-parse it anyway; it then counts as a clean parse again, not an edited
    recipe. Failures leave the stored recipe exactly as it was, and read like
    ingestion's: a 422 telling you to read the page yourself and
    `PATCH /recipes/{id}` if this server can't."""
    recipe = await get_recipe(db, user.household_id, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="recipe not found; browse the library via GET /recipes")
    if not recipe.source_url:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{recipe.title}' has no source_url, so there is no page to re-read — it was entered "
                "by hand or submitted without one. Edit it with PATCH /recipes/{recipe_id} instead."
            ),
        )
    if recipe.edited and not payload.force:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{recipe.title}' has been edited here, and re-parsing would replace those corrections "
                "with whatever the page says now. Confirm with the household first, then send "
                '{"force": true} to re-parse anyway.'
            ),
        )

    host = urlparse(recipe.source_url).hostname
    # A re-parse is the same outbound fetch as an ingest, so it costs the same
    # allowance. Metering only /recipes/ingest would leave the limit one POST
    # away from being bypassed: store any URL as a recipe, then re-parse it.
    await limits.reserve_ingest(db, user.household)

    # Nothing is written until the parse succeeds, so a page that has gone
    # away or lost its JSON-LD leaves the stored recipe exactly as it was.
    try:
        html = await recipe_parser.fetch_page(recipe.source_url)
    except RecipeFetchError as exc:
        log_event("recipe.reparsed", outcome="fetch_failed", host=host, recipe_id=recipe.id)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        parsed = recipe_parser.extract_recipe(html, recipe.source_url)
    except NoRecipeFound as exc:
        log_event("recipe.reparsed", outcome="no_jsonld", host=host, recipe_id=recipe.id)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    from app.services.catalog import parsed_recipe_to_payload

    await update_recipe_from_payload(db, recipe, parsed_recipe_to_payload(parsed))
    recipe.parse_source = "jsonld"
    # The stored recipe is the page again, so it is no longer "edited here" —
    # whatever edits there were are what force: true just discarded.
    recipe.edited = False
    await db.flush()
    await _resync_meals_using(db, user.household_id, recipe)
    await db.commit()
    log_event("recipe.reparsed", outcome="parsed", host=host, recipe_id=recipe.id)
    fresh = await get_recipe(db, user.household_id, recipe.id)
    assert fresh is not None
    return recipe_out(fresh)


@router.delete("/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recipe(recipe_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    recipe = await get_recipe(db, user.household_id, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="recipe not found; browse the library via GET /recipes")
    used_by = await db.execute(select(MealRecipe).where(MealRecipe.recipe_id == recipe.id))
    if used_by.first() is not None:
        raise HTTPException(
            status_code=409,
            detail="recipe is used by one or more meals; remove it from those meals first (PATCH /meals/{id})",
        )
    await db.delete(recipe)
    await db.commit()
