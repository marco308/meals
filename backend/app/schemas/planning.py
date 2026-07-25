import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.schemas.catalog import RecipeLineOut, RecipeSummary
from app.schemas.common import IngredientLineIn

_BOTH_RECIPE_FIELDS = (
    "send either recipe_ids or recipes, not both — recipes is the same list "
    "with a per-recipe scale, so [{recipe_id, scale}] replaces recipe_ids"
)


class MealRecipeIn(BaseModel):
    """A recipe in a meal, at a multiple of its own quantities (Q18). scale=2
    doubles what that recipe puts on the shopping list; other meals using the
    same recipe are unaffected."""

    recipe_id: uuid.UUID
    scale: float = Field(default=1.0, gt=0, le=20)


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
    doesn't know about it reads the recipe exactly as before."""

    scale: float = 1.0


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
