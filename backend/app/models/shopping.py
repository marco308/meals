import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.catalog import Ingredient, Recipe
from app.models.planning import PlanMeal
from app.models.users import utcnow


class Supermarket(Base):
    """A store the household shops at, with its own aisle walking order.

    `aisle_order` is a sequence of aisle emojis; the *active* supermarket's
    order is what the shopping list sorts by and what GET /aisles returns
    (which is also how the iOS app learns it — it refetches /aisles and sorts
    locally). No active supermarket means the built-in order in
    `services/aisles.py`. An order saved before a new aisle joined the
    vocabulary simply gains it at the end (`effective_aisle_order`)."""

    __tablename__ = "supermarkets"
    __table_args__ = (UniqueConstraint("household_id", "name", name="uq_supermarket_household_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("households.id"))
    name: Mapped[str] = mapped_column(String(120))
    aisle_order: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ShoppingList(Base):
    """One live list per household (decision Q1), archived on completion."""

    __tablename__ = "shopping_lists"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("households.id"))
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active | archived
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    items: Mapped[list["ListItem"]] = relationship(
        back_populates="shopping_list", cascade="all, delete-orphan", lazy="selectin"
    )


class ListItem(Base):
    """One line on the list. Identity is (list, ingredient, unit): a second
    recipe needing onions merges into the same line; onions-by-weight and
    onions-by-count stay separate lines (exact-unit merging, decision Q2).

    The id may be supplied by the client (offline-first iOS creates items with
    no signal and syncs later); `updated_at` supports last-write-wins."""

    __tablename__ = "list_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    list_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_lists.id", ondelete="CASCADE"))
    ingredient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingredients.id"))
    unit: Mapped[str | None] = mapped_column(String(50), default=None)
    checked: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)  # "already have it"
    # Staple marked "I'm low" during the pre-shop staples check: that one item
    # joins the main list even though staples are hidden by default. Shop
    # state, like checked — archiving the list retires it.
    staple_needed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    shopping_list: Mapped[ShoppingList] = relationship(back_populates="items")
    ingredient: Mapped[Ingredient] = relationship(lazy="selectin")
    sources: Mapped[list["ListItemSource"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def quantity(self) -> float | None:
        """Total of all contributions; None when no source states an amount."""
        amounts = [s.quantity for s in self.sources if s.quantity is not None]
        return round(sum(amounts), 3) if amounts else None


class ListItemSource(Base):
    """Provenance: why an item is on the list (guiding principle 3). Each row
    is one contribution — from a planned meal's recipe, a meal's loose
    ingredient, or (plan_meal_id NULL) an ad-hoc addition like milk. Removing
    a meal from the plan deletes its contributions and never touches ad-hoc
    rows."""

    __tablename__ = "list_item_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("list_items.id", ondelete="CASCADE"))
    plan_meal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan_meals.id", ondelete="CASCADE"), default=None
    )
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recipes.id", ondelete="SET NULL"), default=None)
    quantity: Mapped[float | None] = mapped_column(Float, default=None)
    # Client-supplied dedup key for ad-hoc adds: a retried POST with the same
    # id is a no-op, which offline sync and retrying AIs both rely on.
    client_key: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[ListItem] = relationship(back_populates="sources")
    plan_meal: Mapped[PlanMeal | None] = relationship(lazy="selectin")
    recipe: Mapped[Recipe | None] = relationship(lazy="selectin")
