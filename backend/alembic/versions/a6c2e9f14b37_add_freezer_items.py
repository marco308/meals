"""add freezer items

Revision ID: a6c2e9f14b37
Revises: 508d35134cdc
Create Date: 2026-09-03 10:00:00.000000

Freezer stock (decision Q24): one row per batch in the freezer, linked to the
meal or recipe it came from when it came from the app, free text otherwise.
Additive only — a new table and nothing else changes, so the outgoing code
keeps serving through the rollout.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a6c2e9f14b37'
down_revision: Union[str, Sequence[str], None] = '508d35134cdc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'freezer_items',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('household_id', sa.Uuid(), nullable=False),
        sa.Column('label', sa.String(length=300), nullable=False),
        sa.Column('meal_id', sa.Uuid(), nullable=True),
        sa.Column('recipe_id', sa.Uuid(), nullable=True),
        sa.Column('portions', sa.Integer(), nullable=False),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('frozen_on', sa.Date(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('portions > 0', name='ck_freezer_item_portions'),
        # No ondelete on household_id, like every other household-scoped table:
        # deletion order is handled explicitly in services/accounts.py. The
        # meal and recipe links let go rather than take the batch with them —
        # the food is still in the freezer whatever happened to the library.
        sa.ForeignKeyConstraint(['household_id'], ['households.id']),
        sa.ForeignKeyConstraint(['meal_id'], ['meals.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['recipe_id'], ['recipes.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_freezer_items_household_id'), 'freezer_items', ['household_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_freezer_items_household_id'), table_name='freezer_items')
    op.drop_table('freezer_items')
