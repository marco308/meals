import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import IngredientLineIn
from app.services.aisles import AISLE_EMOJIS, is_valid_aisle


class IngredientOut(BaseModel):
    id: uuid.UUID
    name: str
    aisle: str
    aisle_label: str
    is_staple: bool


class IngredientUpdate(BaseModel):
    aisle: str | None = None
    is_staple: bool | None = None

    @field_validator("aisle")
    @classmethod
    def _check_aisle(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_aisle(value):
            raise ValueError(f"unknown aisle '{value}'; valid aisles are: {' '.join(AISLE_EMOJIS)}")
        return value


class RecipeLineOut(BaseModel):
    ingredient_id: uuid.UUID
    name: str
    aisle: str
    is_staple: bool
    quantity: float | None
    unit: str | None
    display: str
    raw: str | None


class RecipeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    source_url: str | None = Field(default=None, max_length=1000)
    servings: int | None = Field(default=None, ge=1, le=100)
    prep_minutes: int | None = Field(default=None, ge=0)
    cook_minutes: int | None = Field(default=None, ge=0)
    image_url: str | None = Field(default=None, max_length=1000)
    instructions: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)
    parse_source: Literal["manual", "ai"] = "manual"
    ingredients: list[IngredientLineIn] = Field(default_factory=list, max_length=100)


class RecipeUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    servings: int | None = Field(default=None, ge=1, le=100)
    prep_minutes: int | None = Field(default=None, ge=0)
    cook_minutes: int | None = Field(default=None, ge=0)
    image_url: str | None = Field(default=None, max_length=1000)
    instructions: str | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    ingredients: list[IngredientLineIn] | None = Field(default=None, max_length=100)


class RecipeOut(BaseModel):
    id: uuid.UUID
    title: str
    source_url: str | None
    servings: int | None
    prep_minutes: int | None
    cook_minutes: int | None
    image_url: str | None
    instructions: str | None
    tags: list[str]
    parse_source: str
    edited: bool
    created_at: datetime
    updated_at: datetime
    times_cooked: int
    last_cooked_at: datetime | None
    ingredients: list[RecipeLineOut]


class RecipeSummary(BaseModel):
    id: uuid.UUID
    title: str
    source_url: str | None
    servings: int | None
    prep_minutes: int | None
    cook_minutes: int | None
    tags: list[str]
    times_cooked: int
    last_cooked_at: datetime | None


class IngestIn(BaseModel):
    url: str = Field(min_length=1, max_length=1000)

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value


class IngestOut(BaseModel):
    recipe: RecipeOut
    cached: bool  # True when the URL was already in the library (parse once, reuse forever)
