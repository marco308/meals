"""Model → response-schema conversion, including display strings and aisle labels."""

from app.models import Ingredient, ListItem, Meal, Plan, PlanMeal, Recipe, ShoppingList, Supermarket
from app.schemas.catalog import IngredientOut, RecipeLineOut, RecipeOut, RecipeSummary
from app.schemas.planning import MealOut, MealRecipeOut, PlanMealOut, PlanOut, PlanSummary
from app.schemas.shopping import ListItemOut, ShoppingListOut, SourceOut, SupermarketRef
from app.services.aisles import AISLE_ORDER, AISLES, UNKNOWN_AISLE
from app.services.units import format_buy_quantity, format_quantity
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


def _line_out(ingredient: Ingredient, quantity: float | None, unit: str | None, raw: str | None) -> RecipeLineOut:
    return RecipeLineOut(
        ingredient_id=ingredient.id,
        name=ingredient.name,
        aisle=ingredient.aisle,
        is_staple=ingredient.is_staple,
        value_tier=ingredient.value_tier,
        value_note=ingredient.value_note,
        quantity=quantity,
        unit=unit,
        display=format_quantity(quantity, unit),
        raw=raw,
    )


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
            _line_out(link.ingredient, link.quantity, link.unit, link.raw_text) for link in recipe.ingredient_links
        ],
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
        slot=meal.slot,
        recipes=[
            MealRecipeOut(**recipe_summary(link.recipe).model_dump(), scale=link.scale) for link in meal.recipe_links
        ],
        loose_ingredients=[
            _line_out(link.ingredient, link.quantity, link.unit, None) for link in meal.ingredient_links
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
        meals=[plan_meal_out(link) for link in plan.meal_links if link.meal is not None],
    )


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
            ad_hoc=source.plan_meal_id is None,
            meal_id=source.plan_meal.meal_id if source.plan_meal is not None else None,
            meal_name=source.plan_meal.meal.name if source.plan_meal is not None else None,
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
