import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.catalog import RecipeLineOut, RecipeSummary
from app.schemas.common import IngredientLineIn


class MealCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    slot: str | None = Field(default=None, max_length=30)
    recipe_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    loose_ingredients: list[IngredientLineIn] = Field(default_factory=list, max_length=50)


class MealUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    slot: str | None = Field(default=None, max_length=30)
    recipe_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)
    loose_ingredients: list[IngredientLineIn] | None = Field(default=None, max_length=50)


class MealOut(BaseModel):
    id: uuid.UUID
    name: str
    slot: str | None
    recipes: list[RecipeSummary]
    loose_ingredients: list[RecipeLineOut]
    created_at: datetime


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
