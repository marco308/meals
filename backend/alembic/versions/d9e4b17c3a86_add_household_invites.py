"""add household_invites

Revision ID: d9e4b17c3a86
Revises: c4f2a8b19d63
Create Date: 2026-07-25 20:10:00.000000

Decision Q19: a registration creates its own household and joining an existing
one needs an invite. Additive only — existing households and their users are
untouched, so the deployed instance keeps working exactly as it did for everyone
who already has an account.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e4b17c3a86'
# Chained after the recipe-scale migration (the current head) rather than
# alongside it: two revisions sharing a down_revision are two alembic heads, and
# `alembic upgrade head` — which the container runs at startup — refuses to guess.
down_revision: Union[str, Sequence[str], None] = 'c4f2a8b19d63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'household_invites',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('household_id', sa.Uuid(), nullable=False),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('accepted_by_user_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['household_id'], ['households.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['accepted_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    # Unique: the code hash is what /auth/register looks an invite up by, so a
    # collision would be an ambiguous household.
    op.create_index(
        op.f('ix_household_invites_code_hash'), 'household_invites', ['code_hash'], unique=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_household_invites_code_hash'), table_name='household_invites')
    op.drop_table('household_invites')
