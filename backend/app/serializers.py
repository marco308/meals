"""Model → response-schema conversion, including display strings and aisle labels."""

from app.models import FreezerItem, Ingredient, ListItem, Meal, Plan, PlanMeal, Recipe, ShoppingList, Supermarket
from app.schemas.catalog import (
    IngredientOut,
    RecipeLineOut,
    RecipeOut,
    RecipeStepOut,
    RecipeSummary,
    StepIngredientOut,
)
from app.schemas.freezer import FreezerItemOut
from app.schemas.planning import MealOut, MealRecipeOut, PlanMealOut, PlanOut, PlanSummary
from app.schemas.shopping import ListItemOut, ShoppingListOut, SourceOut, SupermarketRef
from app.services import cook_steps
from app.services.aisles import AISLE_ORDER, AISLES, UNKNOWN_AISLE
from app.services.scaling import scaled_servings
from app.services.slots import meal_slots
from app.services.units import format_buy_quantity, format_cooking_quantity
from app.services.values import value_tier_label

_AISLE_LABELS = dict(AISLES)


def aisle_label(emoji: str) -> str:
    return _AISLE_LABELS.get(emoji, _AISLE_LABELS[UNKNOWN_AISLE])


def ingredient_out(ingredient: Ingredient) -> IngredientOut:
    return IngredientOut(
        id=ingredient.id,
        name=ingredient.name,
        aisle=ingredient.aisle,
        aisle_label=aisle_label(ingredient.aisle),
        is_staple=ingredient.is_staple,
        value_tier=ingredient.value_tier,
        value_tier_label=value_tier_label(ingredient.value_tier),
        value_note=ingredient.value_note,
    )


def _line_out(
    ingredient: Ingredient, quantity: float | None, unit: str | None, display_unit: str | None, raw: str | None
) -> RecipeLineOut:
    return RecipeLineOut(
        ingredient_id=ingredient.id,
        name=ingredient.name,
        aisle=ingredient.aisle,
        is_staple=ingredient.is_staple,
        value_tier=ingredient.value_tier,
        value_note=ingredient.value_note,
        quantity=quantity,
        unit=unit,
        display_unit=display_unit,
        display=format_cooking_quantity(quantity, unit, display_unit),
        raw=raw,
    )


def _cook_lines(recipe: Recipe) -> list[cook_steps.Line]:
    return [
        cook_steps.Line(
            link.ingredient_id,
            link.ingredient.name,
            format_cooking_quantity(link.quantity, link.unit, link.display_unit),
        )
        for link in recipe.ingredient_links
    ]


def recipe_fingerprint(recipe: Recipe) -> str:
    return cook_steps.fingerprint(recipe.instructions, [link.ingredient_id for link in recipe.ingredient_links])


def _steps_fields(recipe: Recipe) -> dict:
    """Cook mode's half of a recipe: the assistant's steps while they still
    describe this recipe, the matched ones otherwise."""
    lines = _cook_lines(recipe)
    current = recipe_fingerprint(recipe)
    has_ai = bool(recipe.cook_steps)
    fresh = has_ai and recipe.cook_steps_fingerprint == current
    steps = (
        cook_steps.stored_steps(recipe.cook_steps, lines)
        if fresh
        else cook_steps.matched_steps(recipe.instructions, lines)
    )
    return {
        "steps": [
            RecipeStepOut(
                number=number,
                section=step.section,
                text=step.text,
                spoken=step.spoken,
                ingredients=[
                    StepIngredientOut(ingredient_id=i.ingredient_id, name=i.name, amount=i.amount)
                    for i in step.ingredients
                ],
            )
            for number, step in enumerate(steps, start=1)
        ],
        "steps_source": "ai" if fresh else "matched",
        "steps_fingerprint": current,
        "steps_outdated": has_ai and not fresh,
        "steps_requested_at": recipe.cook_steps_requested_at,
    }


def recipe_out(recipe: Recipe) -> RecipeOut:
    return RecipeOut(
        id=recipe.id,
        title=recipe.title,
        source_url=recipe.source_url,
        servings=recipe.servings,
        prep_minutes=recipe.prep_minutes,
        cook_minutes=recipe.cook_minutes,
        image_url=recipe.image_url,
        instructions=recipe.instructions,
        tags=list(recipe.tags or []),
        parse_source=recipe.parse_source,
        edited=recipe.edited,
        created_at=recipe.created_at,
        updated_at=recipe.updated_at,
        times_cooked=recipe.times_cooked,
        last_cooked_at=recipe.last_cooked_at,
        ingredients=[
            _line_out(link.ingredient, link.quantity, link.unit, link.display_unit, link.raw_text)
            for link in recipe.ingredient_links
        ],
        **_steps_fields(recipe),
    )


def recipe_summary(recipe: Recipe) -> RecipeSummary:
    return RecipeSummary(
        id=recipe.id,
        title=recipe.title,
        source_url=recipe.source_url,
        servings=recipe.servings,
        prep_minutes=recipe.prep_minutes,
        cook_minutes=recipe.cook_minutes,
        image_url=recipe.image_url,
        tags=list(recipe.tags or []),
        times_cooked=recipe.times_cooked,
        last_cooked_at=recipe.last_cooked_at,
    )


def meal_out(meal: Meal) -> MealOut:
    return MealOut(
        id=meal.id,
        name=meal.name,
        slot=slots[0] if (slots := meal_slots(meal)) else None,
        slots=slots,
        recipes=[
            MealRecipeOut(
                **recipe_summary(link.recipe).model_dump(),
                scale=link.scale,
                scaled_servings=scaled_servings(link.recipe.servings, link.scale),
            )
            for link in meal.recipe_links
        ],
        loose_ingredients=[
            _line_out(link.ingredient, link.quantity, link.unit, link.display_unit, None)
            for link in meal.ingredient_links
        ],
        created_at=meal.created_at,
        times_cooked=meal.times_cooked,
        last_cooked_at=meal.last_cooked_at,
    )


def plan_meal_out(plan_meal: PlanMeal) -> PlanMealOut:
    return PlanMealOut(id=plan_meal.id, meal=meal_out(plan_meal.meal), cooked_at=plan_meal.cooked_at)


def plan_out(plan: Plan) -> PlanOut:
    return PlanOut(
        id=plan.id,
        label=plan.label,
        starts_on=plan.starts_on,
        status=plan.status,
        created_at=plan.created_at,
        archived_at=plan.archived_at,
        # Skip links whose meal is gone. Deleting a meal now clears its plan
        # links explicitly, but a database that predates that (or one restored
        # from a SQLite dev file, where the FK cascade never fired) can still
        # hold an orphan — and a whole plan screen failing over one dead row is
        # a much worse outcome than the row quietly not being listed.
        meals=[plan_meal_out(link) for link in _menu_order(plan.meal_links) if link.meal is not None],
    )


def _menu_order(links: list[PlanMeal]) -> list[PlanMeal]:
    """What is left to cook first, in the order it was added, then what has
    been cooked, most recent last. Done here so every client gets it, the
    phones already installed included: iOS and the web keep this order within
    each slot rather than sorting on their own."""
    to_cook = [link for link in links if link.cooked_at is None]
    cooked = sorted((link for link in links if link.cooked_at is not None), key=lambda link: link.cooked_at)
    return to_cook + cooked


def plan_summary(plan: Plan) -> PlanSummary:
    return PlanSummary(
        id=plan.id,
        label=plan.label,
        starts_on=plan.starts_on,
        status=plan.status,
        meal_count=len(plan.meal_links),
    )


def list_item_out(item: ListItem) -> ListItemOut:
    sources = [
        SourceOut(
            ad_hoc=source.ad_hoc,
            meal_id=source.plan_meal.meal_id if source.plan_meal is not None else None,
            # The meal's name as it is now while it is on a plan, and as it was
            # once its plan-meal has gone (a line on an archived list).
            meal_name=source.plan_meal.meal.name if source.plan_meal is not None else source.meal_name,
            recipe_id=source.recipe.id if source.recipe is not None else None,
            recipe_title=source.recipe.title if source.recipe is not None else None,
            quantity=source.quantity,
        )
        for source in item.sources
    ]
    return ListItemOut(
        id=item.id,
        ingredient_id=item.ingredient_id,
        name=item.ingredient.name,
        aisle=item.ingredient.aisle,
        aisle_label=aisle_label(item.ingredient.aisle),
        is_staple=item.ingredient.is_staple,
        value_tier=item.ingredient.value_tier,
        value_tier_label=value_tier_label(item.ingredient.value_tier),
        value_note=item.ingredient.value_note,
        quantity=item.quantity,
        unit=item.unit,
        display=format_buy_quantity(item.quantity, item.unit),
        checked=item.checked,
        excluded=item.excluded,
        staple_needed=item.staple_needed,
        sources=sources,
        updated_at=item.updated_at,
    )


def shopping_list_out(
    shopping_list: ShoppingList,
    include_staples: bool,
    include_excluded: bool,
    supermarket: Supermarket | None = None,
    aisle_sequence: list[str] | None = None,
) -> ShoppingListOut:
    visible: list[ListItem] = []
    hidden_staples = 0
    for item in shopping_list.items:
        if item.ingredient.is_staple and not include_staples and not item.staple_needed:
            hidden_staples += 1
            continue
        if item.excluded and not include_excluded:
            continue
        visible.append(item)
    order = AISLE_ORDER if aisle_sequence is None else {emoji: i for i, emoji in enumerate(aisle_sequence)}
    visible.sort(key=lambda item: (order.get(item.ingredient.aisle, len(order)), item.ingredient.name))
    return ShoppingListOut(
        id=shopping_list.id,
        status=shopping_list.status,
        created_at=shopping_list.created_at,
        archived_at=shopping_list.archived_at,
        items=[list_item_out(item) for item in visible],
        hidden_staples=hidden_staples,
        supermarket=SupermarketRef(id=supermarket.id, name=supermarket.name) if supermarket else None,
    )


def freezer_item_out(item: FreezerItem) -> FreezerItemOut:
    return FreezerItemOut(
        id=item.id,
        label=item.label,
        meal_id=item.meal_id,
        recipe_id=item.recipe_id,
        portions=item.portions,
        note=item.note,
        frozen_on=item.frozen_on,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )
