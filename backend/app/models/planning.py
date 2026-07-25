import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.catalog import Ingredient, Recipe
from app.models.users import utcnow


class Meal(Base):
    """The unit of planning: zero or more recipes plus loose ingredients
    (the cottage-pie-with-peas-and-carrots case). Slot groups meals in the
    plan view but is never tied to a specific day."""

    __tablename__ = "meals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("households.id"))
    name: Mapped[str] = mapped_column(String(300), index=True)
    slot: Mapped[str | None] = mapped_column(String(30), default=None)  # dinner | lunch | breakfast | ...
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Denormalised read model over cooked_events — see CookedEvent.
    times_cooked: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_cooked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    recipe_links: Mapped[list["MealRecipe"]] = relationship(
        back_populates="meal", cascade="all, delete-orphan", lazy="selectin"
    )
    ingredient_links: Mapped[list["MealIngredient"]] = relationship(
        back_populates="meal", cascade="all, delete-orphan", lazy="selectin"
    )


class MealRecipe(Base):
    """A recipe's place in a meal, and how much of it (decision Q18).

    `scale` lives here rather than on the recipe or the meal: a meal can be
    "×2 the curry, ×1 the rice", and scaling the recipe itself would leak into
    every other meal using it.
    """

    __tablename__ = "meal_recipes"
    __table_args__ = (
        UniqueConstraint("meal_id", "recipe_id", name="uq_meal_recipe"),
        CheckConstraint("scale > 0 AND scale <= 20", name="ck_meal_recipe_scale"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    meal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meals.id", ondelete="CASCADE"))
    recipe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"))
    scale: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")

    meal: Mapped[Meal] = relationship(back_populates="recipe_links")
    recipe: Mapped[Recipe] = relationship(lazy="selectin")


class MealIngredient(Base):
    """A loose ingredient attached directly to a meal, no recipe needed."""

    __tablename__ = "meal_ingredients"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    meal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meals.id", ondelete="CASCADE"))
    ingredient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingredients.id"))
    quantity: Mapped[float | None] = mapped_column(Float, default=None)
    unit: Mapped[str | None] = mapped_column(String(50), default=None)

    meal: Mapped[Meal] = relationship(back_populates="ingredient_links")
    ingredient: Mapped[Ingredient] = relationship(lazy="selectin")


class Plan(Base):
    """A weekly-ish pool of meal options (decision Q4): a loose label like
    'w/c 20 July', archivable, copyable — never a per-day schedule."""

    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("households.id"))
    label: Mapped[str] = mapped_column(String(200))
    starts_on: Mapped[date | None] = mapped_column(Date, default=None)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active | archived
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    meal_links: Mapped[list["PlanMeal"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin", order_by="PlanMeal.created_at"
    )


class CookedEvent(Base):
    """One append-only row per thing cooked, per cooking.

    Marking a plan-meal cooked writes a `meal`-subject row plus one
    `recipe`-subject row for every recipe the meal held *at that moment* — so a
    later edit to the meal never rewrites history. Every foreign key is
    nullable and SET NULL on delete, and the names are denormalised, so the
    record survives deleting the plan, the plan-meal, the meal, or the recipe:
    the count is the whole point, and it must not be destroyed by tidying up.
    """

    __tablename__ = "cooked_events"
    __table_args__ = (
        Index("ix_cooked_events_recipe", "recipe_id", "subject"),
        Index("ix_cooked_events_meal", "meal_id", "subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("households.id"), index=True)
    subject: Mapped[str] = mapped_column(String(10))  # meal | recipe
    meal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("meals.id", ondelete="SET NULL"), default=None)
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recipes.id", ondelete="SET NULL"), default=None)
    plan_meal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan_meals.id", ondelete="SET NULL"), default=None
    )
    meal_name: Mapped[str | None] = mapped_column(String(300), default=None)
    recipe_title: Mapped[str | None] = mapped_column(String(300), default=None)
    # How much of the recipe was cooked (Q18). Part of "what was actually
    # eaten", so it is captured here rather than read back off the live link.
    scale: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    cooked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlanMeal(Base):
    __tablename__ = "plan_meals"
    __table_args__ = (UniqueConstraint("plan_id", "meal_id", name="uq_plan_meal"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"))
    meal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meals.id", ondelete="CASCADE"))
    cooked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    plan: Mapped[Plan] = relationship(back_populates="meal_links")
    meal: Mapped[Meal] = relationship(lazy="selectin")
