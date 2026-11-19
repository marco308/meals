import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    exists,
)
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from app.database import Base
from app.models.users import utcnow
from app.services.aisles import UNKNOWN_AISLE
from app.services.values import DEFAULT_VALUE_TIER


class Ingredient(Base):
    """A canonical grocery item shared across recipes. Name is the canonical
    key ('chopped tomatoes' from two recipes is one ingredient); the aisle
    emoji drives shopping-list sort order; staples are hidden from the list by
    default (decision Q5); the value tier says whether the posh version is
    worth it (decision Q17)."""

    __tablename__ = "ingredients"
    __table_args__ = (UniqueConstraint("household_id", "name", name="uq_ingredient_household_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("households.id"))
    name: Mapped[str] = mapped_column(String(200), index=True)
    aisle: Mapped[str] = mapped_column(String(10), default=UNKNOWN_AISLE)
    is_staple: Mapped[bool] = mapped_column(Boolean, default=False)
    value_tier: Mapped[str] = mapped_column(String(10), default=DEFAULT_VALUE_TIER)  # premium | budget | any
    value_note: Mapped[str | None] = mapped_column(String(200), default=None)  # why — "the cheap one goes bitter"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Recipe(Base):
    """A structured, cached representation of a recipe. source_url is the
    cache key — the same URL is never parsed twice (guiding principle 2).
    `edited` marks human corrections so a re-parse never clobbers them."""

    __tablename__ = "recipes"
    __table_args__ = (UniqueConstraint("household_id", "source_url", name="uq_recipe_household_url"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("households.id"))
    title: Mapped[str] = mapped_column(String(300), index=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), default=None)
    servings: Mapped[int | None] = mapped_column(Integer, default=None)
    prep_minutes: Mapped[int | None] = mapped_column(Integer, default=None)
    cook_minutes: Mapped[int | None] = mapped_column(Integer, default=None)
    image_url: Mapped[str | None] = mapped_column(String(1000), default=None)
    instructions: Mapped[str | None] = mapped_column(Text, default=None)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # Cook mode (Q29): steps an assistant wrote for this recipe, and the
    # fingerprint of the method and ingredients they were written against.
    # Stale once those change, and then the matched steps are served instead.
    cook_steps: Mapped[list | None] = mapped_column(JSON, default=None)
    cook_steps_fingerprint: Mapped[str | None] = mapped_column(String(32), default=None)
    # When the household's assistant was last asked to write them
    # (services/assistant.py): what the web app's "asked" state and the
    # per-household rate limit are read from.
    cook_steps_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    parse_source: Mapped[str] = mapped_column(String(20), default="manual")  # jsonld | ai | manual
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    # Denormalised read model over cooked_events (never incremented blindly —
    # always recomputed from the events, so the two can't drift).
    times_cooked: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_cooked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    ingredient_links: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="RecipeIngredient.position",
    )


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"))
    ingredient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingredients.id"))
    quantity: Mapped[float | None] = mapped_column(Float, default=None)
    unit: Mapped[str | None] = mapped_column(String(50), default=None)
    # How the recipe wrote an ml amount, "tsp" or "tbsp" (Q28): rendering only.
    display_unit: Mapped[str | None] = mapped_column(String(10), default=None)
    raw_text: Mapped[str | None] = mapped_column(String(500), default=None)
    position: Mapped[int] = mapped_column(Integer, default=0)

    recipe: Mapped[Recipe] = relationship(back_populates="ingredient_links")
    ingredient: Mapped[Ingredient] = relationship(lazy="selectin")


class RecipePhoto(Base):
    """A photo the household uploaded for a recipe (issue #280, Q32): the JPEG
    itself, already stripped of its metadata (`services/photos.py`).

    In the database rather than on a disk, so the nightly `pg_dump` (or the
    SQLite file of a one-container install) carries it with no second backup
    path, and deleting a recipe, a household or an account takes it along.
    It is its own table so that nothing that reads a recipe ever loads the
    bytes: there is deliberately no relationship from `Recipe`, only the
    `image_uploaded` flag below.

    `token` is the unguessable name it is served under, `GET /photos/{token}.jpg`,
    because an image view cannot send an Authorization header. A replacement is
    a new row with a new token, so the URL changes and no cache needs busting.
    """

    __tablename__ = "recipe_photos"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("households.id"), index=True)
    # One photo per recipe, and it goes with the recipe.
    recipe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), unique=True)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# Whether the recipe's picture is one the household uploaded, rather than a
# link to one hosted elsewhere. A correlated EXISTS on the unique recipe_id, so
# every read of a recipe (the library listing included) knows without loading
# a photo or keeping a second copy of the answer that could drift.
Recipe.image_uploaded = column_property(
    exists().where(RecipePhoto.recipe_id == Recipe.id).correlate_except(RecipePhoto),
    deferred=False,
)
