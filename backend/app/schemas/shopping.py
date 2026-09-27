import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

from app.schemas.common import IngredientLineIn
from app.services.aisles import AISLE_EMOJIS
from app.services.supermarkets import MAX_RADIUS_M, MIN_RADIUS_M


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


Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
RadiusM = Annotated[int, Field(ge=MIN_RADIUS_M, le=MAX_RADIUS_M)]


class SupermarketOut(BaseModel):
    id: uuid.UUID
    name: str
    aisle_order: list[str]  # the complete walk, first aisle to last
    is_active: bool  # the active supermarket's order sorts the list and GET /aisles
    created_at: datetime
    # Where the store is, for a phone to match on-device; all null when unset.
    # radius_m is the effective radius, the default filled in.
    latitude: float | None = None
    longitude: float | None = None
    radius_m: int | None = None


class SupermarketCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # Aisle emojis first-to-last as walked; omitted aisles keep their usual
    # place at the end. Default: the built-in store-walking order. Each aisle
    # may appear once, so a longer list is refused before anything reads it.
    aisle_order: list[str] | None = Field(default=None, max_length=len(AISLE_EMOJIS))
    is_active: bool = False
    # Where the store is (both or neither). Never where a user is: the apps
    # set this from a place search or the web's "where I am now".
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    radius_m: RadiusM | None = None  # how close counts as "in the store"; null = the default


class SupermarketUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    aisle_order: list[str] | None = Field(default=None, max_length=len(AISLE_EMOJIS))
    is_active: bool | None = None  # true sorts the list for this store; false falls back to the built-in order
    # Send latitude and longitude together; both null clears the location
    # (and its radius). A field left out is left alone.
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    radius_m: RadiusM | None = None


class ArchiveOut(BaseModel):
    archived_list_id: uuid.UUID
    new_list_id: uuid.UUID


class SuggestionsOut(BaseModel):
    detail: str
    suggestions: list[str] = Field(default_factory=list)
