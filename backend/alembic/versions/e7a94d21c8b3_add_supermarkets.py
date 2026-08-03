"""add supermarkets

Revision ID: e7a94d21c8b3
Revises: b8f4c6d2e701
Create Date: 2026-08-03 09:00:00.000000

Per-supermarket aisle orders: a household saves the walking order per store
and marks one active; the active order drives the shopping-list sort and
GET /aisles. Additive only — no existing table changes, and with no rows the
built-in order applies exactly as before, so old and new code coexist during
the rollout.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a94d21c8b3'
down_revision: Union[str, Sequence[str], None] = 'b8f4c6d2e701'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'supermarkets',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('household_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('aisle_order', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        # No ondelete on household_id, like every other household-scoped table:
        # deletion order is handled explicitly in services/accounts.py.
        sa.ForeignKeyConstraint(['household_id'], ['households.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('household_id', 'name', name='uq_supermarket_household_name'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('supermarkets')
