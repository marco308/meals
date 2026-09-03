from datetime import datetime

from pydantic import BaseModel, Field


class SubscriptionOut(BaseModel):
    """What this household's subscription is doing, on a server that has one."""

    tier: str = Field(description="The stored tier. What it actually buys you is in GET /limits.")
    state: str = Field(
        description=(
            "'permanent' (no expiry, which is every self-hosted household and a standing comp), "
            "'paid' (in date), 'grace' (past expiry, caps not yet re-applied) or 'lapsed'."
        )
    )
    paid_until: datetime | None = Field(description="When this runs out. Null means it never does.")
    grace_ends_at: datetime | None = Field(
        description="When the free tier's caps come back after expiry. Nothing is ever deleted at either point."
    )
    source: str | None = Field(description="Where the entitlement came from: 'comp', or the processor's name.")
    price_pence: int | None = Field(
        description="What this household agreed to pay, snapshotted when it started. A founding price stays this."
    )
    price_currency: str | None = Field(description="Currency of price_pence.")
    offer_price_pence: int | None = Field(
        description="What a new subscription would cost here, or null on a server that has not said."
    )
    offer_price_currency: str | None = Field(description="Currency of offer_price_pence.")
    manage_url: str | None = Field(
        description=(
            "This server's configured fallback page, if it has one. Prefer POST /billing/portal, which mints "
            "a one-time session straight into this household's own portal where the processor supports it."
        )
    )
    can_manage: bool = Field(
        description=(
            "Whether POST /billing/portal has anywhere to send this household. False for one that never "
            "paid: a link to a login page they have no account on is not a feature."
        )
    )
    can_checkout: bool = Field(
        description=(
            "Whether a subscription can be started from here right now: false on a server that sells nothing, "
            "and false for a household that already has one."
        )
    )


class CheckoutOut(BaseModel):
    """Where to send somebody to pay."""

    url: str = Field(description="The processor's hosted checkout. Single-use, and bound to this household.")
