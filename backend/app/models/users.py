import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Household(Base):
    """A household: one recipe library, plan and shopping list, shared by every
    user in it (decision Q16). Since Q19 a registration creates its own
    household and users join an existing one by invite, so a server holds many
    — every query must still filter on `household_id`.

    Since Q23 one of its members is the **lead**: the account a subscription
    belongs to, and the only one who may invite or remove people. Everything
    about the food stays equal between members."""

    __tablename__ = "households"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), default="Home")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Nullable for two reasons that are both mechanical rather than a claim that
    # a household may have no lead: the household row is inserted before the
    # user that will lead it exists, and SET NULL keeps a household deletable
    # while its rows are removed in `services/accounts.py`'s explicit order.
    # "Exactly one lead, always" is held by that module, not by the schema.
    # `use_alter` because this closes a cycle: a household points at its lead,
    # and every user points at their household. SQLAlchemy cannot sort the two
    # tables for CREATE without it, and the tests build their schema from the
    # models rather than from the migrations.
    lead_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", use_alter=True, name="households_lead_user_id_fkey"),
        default=None,
    )

    # Two foreign keys now join these tables (a user's household, a household's
    # lead), so both relationships have to say which one they travel.
    users: Mapped[list["User"]] = relationship(back_populates="household", foreign_keys="User.household_id")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("households.id"))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    household: Mapped[Household] = relationship(back_populates="users", lazy="selectin", foreign_keys=[household_id])
    tokens: Mapped[list["AuthToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AuthToken(Base):
    """Opaque bearer tokens, stored hashed. kind='session' for app logins,
    kind='api' for the per-user PATs that AI clients use (decision Q7/Q15)."""

    __tablename__ = "auth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="session")  # session | api
    label: Mapped[str | None] = mapped_column(String(200), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    user: Mapped[User] = relationship(back_populates="tokens", lazy="selectin")


class HouseholdInvite(Base):
    """A single-use code that lets one more person register into an existing
    household (decision Q19). Stored hashed like an auth token, so a leaked
    database row can't be redeemed.

    A redeemed invite is kept rather than deleted: `accepted_by_user_id` is the
    only record of who let whom in, which is worth having when the household is
    the whole authorisation boundary."""

    __tablename__ = "household_invites"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"))
    # Always set when an invite is created; nullable only so that deleting the
    # inviter (Q20) blanks this rather than cascading the row away. Losing the
    # record of who admitted whom the moment they leave would defeat the point
    # of keeping redeemed invites at all.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    household: Mapped[Household] = relationship(lazy="selectin")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id], lazy="selectin")
