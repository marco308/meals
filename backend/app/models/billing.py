import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.users import utcnow


class BillingEvent(Base):
    """One webhook the processor sent, recorded before it is acted on.

    This is the idempotency ledger, and it is the reason a retried webhook
    cannot grant a second year. Processors retry on any non-2xx (Lemon Squeezy
    three times with backoff, Paddle for longer), and a network blip between
    "entitlement granted" and "200 returned" is exactly the case that would
    otherwise double-count.

    Kept rather than pruned: when somebody asks why their household expired on a
    date nobody chose, this table is the answer, and it is small — one row per
    payment event per household per year.
    """

    __tablename__ = "billing_events"
    __table_args__ = (UniqueConstraint("processor", "event_id", name="uq_billing_event"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    processor: Mapped[str] = mapped_column(String(40))
    #: The processor's own id where it sends one (Paddle's `event_id`), and a
    #: digest of the raw body where it does not (Lemon Squeezy sends no id, so
    #: an identical retry hashes the same and is caught the same way).
    event_id: Mapped[str] = mapped_column(String(120))
    #: What the processor called it, kept verbatim for anyone reading this back.
    event_type: Mapped[str] = mapped_column(String(80))
    #: What this server made of it: granted | renewed | revoked | ignored |
    #: orphan | refused. Not an enum, for the same reason no other vocabulary
    #: here is one.
    outcome: Mapped[str] = mapped_column(String(20))
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("households.id", ondelete="SET NULL"), default=None
    )
    #: Why, when the outcome was not a clean one. This is what an operator woken
    #: by the alert actually reads.
    detail: Mapped[str | None] = mapped_column(String(300), default=None)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
