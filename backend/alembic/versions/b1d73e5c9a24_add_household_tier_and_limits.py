"""add households.tier, its price snapshot and the ingest counter

Revision ID: b1d73e5c9a24
Revises: a2f61d38c095
Create Date: 2026-08-21 18:10:00.000000

Issue #94 / planning/08-freemium.md. A household now says which set of limits
applies to it, and carries the two pieces of state those limits need that
cannot be derived from the rest of the schema.

Everything here is additive and every existing row keeps behaving exactly as it
did: `tier` backfills to 'unlimited', which app/limits.py resolves to no caps
at all, so the family instance and every self-hosted one notice nothing. A
deployment that never sets `LIMITS_PROFILE` never reads these columns.

- `tier` — 'unlimited' | 'free' | 'paid'. A plain string rather than an enum
  because the client contract is additive-only and a new tier must never be a
  decode error, on the server or in a client.
- `price_pence` / `price_currency` / `price_set_at` — the founding-price-for-life
  promise as a stored fact rather than a sentence in a document. Null until
  somebody pays, which on a self-hosted instance is never.
- `ingest_period_started_at` / `ingests_used` — the URL-ingest quota's counter.
  It has to be stored rather than counted: recipes can be deleted, and counting
  rows would refund the quota every time one was.

Safe under the start-first rollout in CLAUDE.md: the outgoing container keeps
serving while this runs, and adding nullable columns (plus two with constant
server defaults) takes nothing away from the code still reading the table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1d73e5c9a24'
down_revision: Union[str, Sequence[str], None] = 'a2f61d38c095'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # No foreign keys and constant defaults, so SQLite takes these with a plain
    # ALTER TABLE and needs no batch rebuild.
    op.add_column(
        'households',
        sa.Column('tier', sa.String(length=20), nullable=False, server_default='unlimited'),
    )
    op.add_column('households', sa.Column('price_pence', sa.Integer(), nullable=True))
    op.add_column('households', sa.Column('price_currency', sa.String(length=3), nullable=True))
    op.add_column('households', sa.Column('price_set_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('households', sa.Column('ingest_period_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'households',
        sa.Column('ingests_used', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Downgrade schema.

    Dropping these loses which tier a household was on and what it agreed to
    pay — which is a support conversation, not a data loss, because no food is
    stored here. Everything goes back to unlimited, which is where it started.
    """
    op.drop_column('households', 'ingests_used')
    op.drop_column('households', 'ingest_period_started_at')
    op.drop_column('households', 'price_set_at')
    op.drop_column('households', 'price_currency')
    op.drop_column('households', 'price_pence')
    op.drop_column('households', 'tier')
