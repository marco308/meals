import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    raw_text: Mapped[str | None] = mapped_column(String(500), default=None)
    position: Mapped[int] = mapped_column(Integer, default=0)

    recipe: Mapped[Recipe] = relationship(back_populates="ingredient_links")
    ingredient: Mapped[Ingredient] = relationship(lazy="selectin")
