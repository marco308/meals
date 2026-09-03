import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

#: One batch is one cooking; nobody freezes five hundred portions of anything.
MAX_PORTIONS = 500


class FreezerAddIn(BaseModel):
    """Put a batch in the freezer. Name it **one** way: `meal_id` (a meal from
    GET /meals — the batch takes the meal's name), `recipe_id` (a recipe from
    GET /recipes — it takes the title), or `label` (free text, for what went in
    without passing through the plan). `portions` is how many are going in."""

    meal_id: uuid.UUID | None = None
    recipe_id: uuid.UUID | None = None
    label: str | None = Field(default=None, min_length=1, max_length=300)
    portions: int = Field(default=1, ge=1, le=MAX_PORTIONS)
    note: str | None = Field(default=None, max_length=300)  # "the spicy batch", "for the kids"
    frozen_on: date | None = None  # defaults to today

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "FreezerAddIn":
        given = [name for name in ("meal_id", "recipe_id", "label") if getattr(self, name) is not None]
        if len(given) != 1:
            raise ValueError(
                "say what the batch is in exactly one way: meal_id (from GET /meals), recipe_id "
                "(from GET /recipes), or label (free text for something that did not come from the app)"
            )
        return self


class FreezerItemUpdate(BaseModel):
    """Correct a batch: set the portions left outright, rename it, change the
    note or the date. To eat from it use POST /freezer/{item_id}/take, which
    also clears the batch when the last portion goes."""

    label: str | None = Field(default=None, min_length=1, max_length=300)
    portions: int | None = Field(default=None, ge=1, le=MAX_PORTIONS)
    note: str | None = Field(default=None, max_length=300)
    frozen_on: date | None = None


class FreezerTakeIn(BaseModel):
    portions: int = Field(default=1, ge=1, le=MAX_PORTIONS)


class FreezerItemOut(BaseModel):
    id: uuid.UUID
    label: str
    meal_id: uuid.UUID | None  # the meal it came from, while that meal still exists
    recipe_id: uuid.UUID | None  # the recipe it came from, while that recipe still exists
    portions: int  # left in the freezer; 0 only in the reply to a take that emptied the batch
    note: str | None
    frozen_on: date
    created_at: datetime
    updated_at: datetime


class FreezerOut(BaseModel):
    items: list[FreezerItemOut]  # oldest batch first — eat that one
    total_portions: int
