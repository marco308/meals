"""add the billing event ledger

Revision ID: 508d35134cdc
Revises: 74e2494dfabf
Create Date: 2026-08-22 23:58:00.000000

Issue #99 / planning/08-freemium.md §2. One row per webhook the processor sent,
written before it is acted on.

This table is what makes the webhook idempotent, and idempotence is what stops a
retry granting a second year. Processors retry on any non-2xx — Lemon Squeezy
three times with backoff, Paddle for longer — so a blip between "entitlement
granted" and "200 returned" is not a rare case, it is the expected one.

`(processor, event_id)` is unique because that pair is the identity of an event.
Paddle sends an `event_id`; Lemon Squeezy sends none, so the id stored for it is
a digest of the raw body, which makes an identical retry land on the same row.

`outcome` and `detail` are kept for the same reason the cooked history is: when
somebody asks why their household expired on a date nobody chose, this is the
answer. It stays small — one row per payment event per household per year — so
there is nothing to prune.

New table only, so there is nothing for the outgoing container to trip over
during a start-first rollout.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '508d35134cdc'
down_revision: Union[str, Sequence[str], None] = '74e2494dfabf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('billing_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('processor', sa.String(length=40), nullable=False),
    sa.Column('event_id', sa.String(length=120), nullable=False),
    sa.Column('event_type', sa.String(length=80), nullable=False),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.Column('household_id', sa.Uuid(), nullable=True),
    sa.Column('detail', sa.String(length=300), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['household_id'], ['households.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('processor', 'event_id', name='uq_billing_event')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('billing_events')
