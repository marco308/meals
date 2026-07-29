import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import IngredientLineIn
from app.services.aisles import AISLE_EMOJIS, is_valid_aisle
from app.services.values import VALUE_TIER_HINT, is_valid_value_tier


def _check_value_tier(value: str | None) -> str | None:
    """Shared by every schema that accepts a tier so the hint is identical."""
    if value is not None and not is_valid_value_tier(value):
        raise ValueError(f"unknown value tier '{value}'; {VALUE_TIER_HINT}")
    return value


class IngredientOut(BaseModel):
    id: uuid.UUID
    name: str
    aisle: str
    aisle_label: str
    is_staple: bool
    value_tier: str  # premium | budget | any — is the posh version worth it?
    value_tier_label: str
    value_note: str | None  # the household's reason, e.g. "cheap ones go bitter"


class IngredientUpdate(BaseModel):
    aisle: str | None = None
    is_staple: bool | None = None
    value_tier: str | None = None
    value_note: str | None = Field(default=None, max_length=200)

    @field_validator("aisle")
    @classmethod
    def _check_aisle(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_aisle(value):
            raise ValueError(f"unknown aisle '{value}'; valid aisles are: {' '.join(AISLE_EMOJIS)}")
        return value

    @field_validator("value_tier")
    @classmethod
    def _check_tier(cls, value: str | None) -> str | None:
        return _check_value_tier(value)


class MergeIn(BaseModel):
    duplicate_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


class MergeOut(BaseModel):
    ingredient: IngredientOut  # the survivor
    merged: int  # how many duplicates were folded into it


class DuplicateGroup(BaseModel):
    canonical_name: str
    keeper: IngredientOut  # the suggested survivor — merge the rest into this one
    duplicates: list[IngredientOut]


class UnfoldedIngredient(BaseModel):
    """One ingredient whose stored name is not its canonical form, with no
    twin to merge it with."""

    ingredient: IngredientOut
    canonical_name: str


class DuplicatesOut(BaseModel):
    groups: list[DuplicateGroup]
    unfolded: list[UnfoldedIngredient]


class RecipeLineOut(BaseModel):
    ingredient_id: uuid.UUID
    name: str
    aisle: str
    is_staple: bool
    value_tier: str
    value_note: str | None
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
    # Carried on the summary too so a library listing can show thumbnails
    # without a detail fetch per row.
    image_url: str | None
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
