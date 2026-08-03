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
    value_tier: str  # premium | budget | any — decide at the shelf, not at home
    value_tier_label: str
    value_note: str | None
    quantity: float | None
    unit: str | None
    display: str
    checked: bool
    excluded: bool
    staple_needed: bool  # staple marked "I'm low" — shown on the main list this shop
    sources: list[SourceOut]
    updated_at: datetime


class ListItemUpdate(BaseModel):
    checked: bool | None = None
    excluded: bool | None = None
    staple_needed: bool | None = None


class SupermarketRef(BaseModel):
    id: uuid.UUID
    name: str


class ShoppingListOut(BaseModel):
    id: uuid.UUID
    status: str
    created_at: datetime
    archived_at: datetime | None
    items: list[ListItemOut]  # sorted in store-walking order (aisle, then name)
    hidden_staples: int  # staples not shown — list them with ?include_staples=true, surface one via staple_needed
    supermarket: SupermarketRef | None = None  # whose aisle order the sort follows; null = the built-in order


class SupermarketOut(BaseModel):
    id: uuid.UUID
    name: str
    aisle_order: list[str]  # the complete walk, first aisle to last
    is_active: bool  # the active supermarket's order sorts the list and GET /aisles
    created_at: datetime


class SupermarketCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # Aisle emojis first-to-last as walked; omitted aisles keep their usual
    # place at the end. Default: the built-in store-walking order.
    aisle_order: list[str] | None = None
    is_active: bool = False


class SupermarketUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    aisle_order: list[str] | None = None
    is_active: bool | None = None  # true sorts the list for this store; false falls back to the built-in order


class ArchiveOut(BaseModel):
    archived_list_id: uuid.UUID
    new_list_id: uuid.UUID


class SuggestionsOut(BaseModel):
    detail: str
    suggestions: list[str] = Field(default_factory=list)
