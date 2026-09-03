import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.users import utcnow


class FreezerItem(Base):
    """One batch in the freezer: a label, how many portions are left, and when
    it went in (decision Q24).

    A batch rather than a running total per dish, because two batches of the
    same chilli frozen a month apart are two things to eat oldest-first, and a
    single merged count would lose the date that says which is which. Clients
    group by label when they want the total.

    `label` is always populated and is the record: `meal_id` / `recipe_id` say
    where the batch came from when it came from the app, and both are
    `SET NULL` on delete so tidying the library never empties a freezer. Free
    text (`meal_id` and `recipe_id` both null) is for the things that went in
    without passing through a plan — half a lasagne from a friend, the
    leftover stock. `portions` is what is left, not what went in: taking one
    out decrements it and the row goes when it reaches zero, so the table is
    what is *in* the freezer and nothing else.
    """

    __tablename__ = "freezer_items"
    __table_args__ = (CheckConstraint("portions > 0", name="ck_freezer_item_portions"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("households.id"), index=True)
    label: Mapped[str] = mapped_column(String(300))
    meal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("meals.id", ondelete="SET NULL"), default=None)
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recipes.id", ondelete="SET NULL"), default=None)
    portions: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str | None] = mapped_column(String(300), default=None)
    frozen_on: Mapped[date] = mapped_column(Date)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
