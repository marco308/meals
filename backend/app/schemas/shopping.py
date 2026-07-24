import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import IngredientLineIn


class AdhocItemIn(IngredientLineIn):
    """Ad-hoc addition (the milk-is-out case). Clients may supply their own
    item id — offline-first iOS creates the item locally and syncs later, and
    a retrying AI won't double-add (idempotent by id)."""

    id: uuid.UUID | None = None


class SourceOut(BaseModel):
    ad_hoc: bool
    meal_id: uuid.UUID | None
    meal_name: str | None
    recipe_id: uuid.UUID | None
    recipe_title: str | None
    quantity: float | None


class ListItemOut(BaseModel):
    id: uuid.UUID
    ingredient_id: uuid.UUID
    name: str
    aisle: str
    aisle_label: str
    is_staple: bool
    quantity: float | None
    unit: str | None
    display: str
    checked: bool
    excluded: bool
    sources: list[SourceOut]
    updated_at: datetime


class ListItemUpdate(BaseModel):
    checked: bool | None = None
    excluded: bool | None = None


class ShoppingListOut(BaseModel):
    id: uuid.UUID
    status: str
    created_at: datetime
    archived_at: datetime | None
    items: list[ListItemOut]  # sorted in store-walking order (aisle, then name)
    hidden_staples: int  # staple items not shown — reveal with ?include_staples=true for a staples check


class ArchiveOut(BaseModel):
    archived_list_id: uuid.UUID
    new_list_id: uuid.UUID


class SuggestionsOut(BaseModel):
    detail: str
    suggestions: list[str] = Field(default_factory=list)
