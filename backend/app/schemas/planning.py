import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.schemas.catalog import RecipeLineOut, RecipeSummary
from app.schemas.common import IngredientLineIn
from app.services.scaling import MAX_SCALE

_BOTH_RECIPE_FIELDS = (
    "send either recipe_ids or recipes, not both — recipes is the same list "
    "with a per-recipe scale, so [{recipe_id, scale}] replaces recipe_ids"
)

_BOTH_AMOUNT_FIELDS = (
    "send either scale or servings for a recipe, not both — they are two ways "
    "of saying the same thing, and servings only works when the recipe says "
    "how many it serves"
)


class MealRecipeIn(BaseModel):
    """A recipe in a meal, at a multiple of its own quantities (Q18). scale=2
    doubles what that recipe puts on the shopping list; other meals using the
    same recipe are unaffected.

    `servings` says the same thing in portions instead of multiples: a recipe
    that serves 4, asked for 6, is stored as scale 1.5. It needs the recipe's
    own `servings` to divide by, so a recipe without one is a 422 telling you
    to set it or send `scale`."""

    recipe_id: uuid.UUID
    scale: float = Field(default=1.0, gt=0, le=MAX_SCALE)
    servings: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def _one_amount_field(self) -> "MealRecipeIn":
        if self.servings is not None and "scale" in self.model_fields_set:
            raise ValueError(_BOTH_AMOUNT_FIELDS)
        return self


def _resolve_recipes(
    recipe_ids: list[uuid.UUID] | None, recipes: list[MealRecipeIn] | None
) -> list[MealRecipeIn] | None:
    """The two ways of naming a meal's recipes, collapsed to one. `recipe_ids`
    is the original shape and still means "all at ×1" — it must keep working
    for clients older than scaling."""
    if recipes is not None:
        return recipes
    if recipe_ids is not None:
        return [MealRecipeIn(recipe_id=recipe_id) for recipe_id in recipe_ids]
    return None


class MealCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    slot: str | None = Field(default=None, max_length=30)
    recipe_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    recipes: list[MealRecipeIn] | None = Field(default=None, max_length=20)
    loose_ingredients: list[IngredientLineIn] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _one_recipe_field(self) -> "MealCreate":
        if self.recipes is not None and "recipe_ids" in self.model_fields_set:
            raise ValueError(_BOTH_RECIPE_FIELDS)
        return self

    @property
    def resolved_recipes(self) -> list[MealRecipeIn]:
        return _resolve_recipes(self.recipe_ids, self.recipes) or []


class MealUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    slot: str | None = Field(default=None, max_length=30)
    recipe_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)
    recipes: list[MealRecipeIn] | None = Field(default=None, max_length=20)
    loose_ingredients: list[IngredientLineIn] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def _one_recipe_field(self) -> "MealUpdate":
        if self.recipes is not None and self.recipe_ids is not None:
            raise ValueError(_BOTH_RECIPE_FIELDS)
        return self

    @property
    def resolved_recipes(self) -> list[MealRecipeIn] | None:
        return _resolve_recipes(self.recipe_ids, self.recipes)


class MealRecipeOut(RecipeSummary):
    """A recipe as it sits in a meal. `scale` is additive — a client that
    doesn't know about it reads the recipe exactly as before.

    `scaled_servings` is how many this meal's share of the recipe feeds, i.e.
    the inherited `servings` (the recipe's own, unchanged) times the scale,
    or null when the recipe doesn't say. It is deliberately *not* called
    `servings`: that name is already taken by the recipe's own figure, and
    redefining it would be exactly the meaning change the client contract
    forbids."""

    scale: float = 1.0
    scaled_servings: int | None = None


class MealOut(BaseModel):
    id: uuid.UUID
    name: str
    slot: str | None
    recipes: list[MealRecipeOut]
    loose_ingredients: list[RecipeLineOut]
    created_at: datetime
    times_cooked: int
    last_cooked_at: datetime | None


class MealSummary(BaseModel):
    id: uuid.UUID
    name: str
    slot: str | None


class PlanCreate(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    starts_on: date | None = None
    copy_from_plan_id: uuid.UUID | None = None


class PlanMealOut(BaseModel):
    id: uuid.UUID  # plan-meal link id — used to remove the meal or mark it cooked
    meal: MealOut
    cooked_at: datetime | None


class PlanOut(BaseModel):
    id: uuid.UUID
    label: str
    starts_on: date | None
    status: str
    created_at: datetime
    archived_at: datetime | None
    meals: list[PlanMealOut]


class PlanSummary(BaseModel):
    id: uuid.UUID
    label: str
    starts_on: date | None
    status: str
    meal_count: int


class AddMealIn(BaseModel):
    meal_id: uuid.UUID
