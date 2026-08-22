import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def _default_tier() -> str:
    """The tier a new household starts on, read at insert time rather than at
    import: `DEFAULT_HOUSEHOLD_TIER` is a deployment's decision, and putting it
    here means every path that creates a household — registration, the invite
    flow, `app/provision.py`, the tests — honours it without knowing it exists.
    """
    return get_settings().default_household_tier


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

    # ------------------------------------------------------------- limits
    # Which set of numbers in app/limits.py applies to this household. The
    # default is "unlimited" and every household that predates the column has
    # it, so a self-hosted instance — and the family one — notices nothing.
    # `DEFAULT_HOUSEHOLD_TIER` decides what a *new* registration starts on,
    # which is how one deployment can run a free tier while the code stays the
    # same everywhere (planning/08-freemium.md §1).
    tier: Mapped[str] = mapped_column(String(20), default=_default_tier, server_default="unlimited")
    # The founding-price-for-life promise, stored rather than promised in a
    # document: what this household agreed to pay, in the smallest unit of the
    # currency, and when that was fixed. Unset until money is involved, which
    # for every self-hosted instance is forever.
    price_pence: Mapped[int | None] = mapped_column(Integer, default=None)
    price_currency: Mapped[str | None] = mapped_column(String(3), default=None)
    price_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # The URL-ingest quota's counter (limits.reserve_ingest). It has to be
    # stored rather than counted: recipes can be deleted, and a COUNT would
    # refund the quota every time one was, which is the loop the limit exists
    # to stop. `ingest_period_started_at` is the first instant of the calendar
    # month the count belongs to; a stale one reads as zero.
    ingest_period_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ingests_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # ------------------------------------------------------- entitlement
    # When the tier above stops applying, and where it came from (#99). Null
    # `paid_until` means "does not expire", which is what every self-hosted
    # household has and what a permanent comp gets: the tier simply stands.
    # With a date, `limits.effective_tier` drops the household back to the free
    # tier once it is past, plus the grace period — and drops it back only for
    # the purpose of *caps*. Nothing is deleted and nothing becomes unreadable
    # (planning/08-freemium.md §5), which is why lapsing lives in one derived
    # function rather than in a job that rewrites `tier`.
    paid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # 'comp' or the name of the processor the payment came through. Free text
    # rather than an enum for the same reason `tier` is: a value this build has
    # not heard of must never be an error.
    entitlement_source: Mapped[str | None] = mapped_column(String(40), default=None)
    # Why, in one line, for whoever is reading the list a year later: "early
    # supporter", "PikaPods", "found the backup bug".
    entitlement_note: Mapped[str | None] = mapped_column(String(200), default=None)
    # Dunning's two one-shot marks (§5: one email before expiry, one after).
    # Both are cleared whenever the entitlement is extended, so the next year
    # gets its own pair rather than being silently skipped.
    expiry_warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    lapse_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

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
