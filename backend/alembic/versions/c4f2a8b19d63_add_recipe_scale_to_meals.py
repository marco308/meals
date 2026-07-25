"""add per-meal recipe scale

Revision ID: c4f2a8b19d63
Revises: a7c31f4d90e2
Create Date: 2026-07-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f2a8b19d63'
# Single chain, always: two heads take the API down on deploy (see the head
# count test in tests/test_migrations.py).
down_revision: Union[str, Sequence[str], None] = 'a7c31f4d90e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Existing links are ×1, which is exactly what every client meant before
    # scaling existed.
    op.add_column(
        'meal_recipes',
        sa.Column('scale', sa.Float(), nullable=False, server_default='1.0'),
    )
    op.add_column(
        'cooked_events',
        sa.Column('scale', sa.Float(), nullable=False, server_default='1.0'),
    )
    # SQLite can't ALTER in a CHECK constraint, and batch_alter_table would
    # rebuild the table; the bound is enforced by the Pydantic schema on the
    # way in, so this is belt-and-braces on Postgres only.
    if op.get_bind().dialect.name == 'postgresql':
        op.create_check_constraint(
            'ck_meal_recipe_scale', 'meal_recipes', 'scale > 0 AND scale <= 20'
        )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == 'postgresql':
        op.drop_constraint('ck_meal_recipe_scale', 'meal_recipes', type_='check')
    op.drop_column('cooked_events', 'scale')
    op.drop_column('meal_recipes', 'scale')
