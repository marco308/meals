"""add household entitlement expiry, source and dunning marks

Revision ID: 74e2494dfabf
Revises: b1d73e5c9a24
Create Date: 2026-08-22 23:24:04.173917

Issue #99 / planning/08-freemium.md §2 and §5. `households.tier` (added in
b1d73e5c9a24) says which set of limits applies; these say until when, where it
came from, and what has already been said about it.

- `paid_until` — when the tier stops applying. **Null means never**, which is
  what every existing row gets and what a standing comp gets, so nothing about
  this is visible on a self-hosted instance. `app/limits.effective_tier` reads
  a household past this date, plus `ENTITLEMENT_GRACE_DAYS`, as `free`; the
  stored tier is deliberately left alone, because §5 promises nothing is
  deleted and a renewal should be one column rather than a reconstruction.
- `entitlement_source` — 'comp', or the name of the processor a payment came
  through. A plain string for the same reason `tier` is one: a value a future
  build introduces must never be an error in an older one.
- `entitlement_note` — one line of why, for whoever reads the list in a year.
- `expiry_warned_at` / `lapse_notified_at` — dunning's two one-shot marks, so
  the email before expiry and the one after each go exactly once. Cleared
  whenever the expiry moves, so next year gets its own pair.

Everything here is nullable with no default and nothing reads it unless a
deployment sets an entitlement, so every existing row keeps behaving exactly as
it did. Safe under the start-first rollout in CLAUDE.md: adding nullable columns
takes nothing away from the outgoing container still serving from this table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '74e2494dfabf'
down_revision: Union[str, Sequence[str], None] = 'b1d73e5c9a24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Autogenerate also proposed creating ck_meal_recipe_scale on meal_recipes.
    # That constraint already exists (c4f2a8b19d63); SQLite reflection simply
    # cannot see it, so it is a false positive and is deliberately not here.
    with op.batch_alter_table('households', schema=None) as batch_op:
        batch_op.add_column(sa.Column('paid_until', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('entitlement_source', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('entitlement_note', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('expiry_warned_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('lapse_notified_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('households', schema=None) as batch_op:
        batch_op.drop_column('lapse_notified_at')
        batch_op.drop_column('expiry_warned_at')
        batch_op.drop_column('entitlement_note')
        batch_op.drop_column('entitlement_source')
        batch_op.drop_column('paid_until')
