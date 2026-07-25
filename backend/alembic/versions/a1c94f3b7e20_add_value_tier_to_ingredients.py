"""add value tier to ingredients

Revision ID: a1c94f3b7e20
Revises: e3d1f8c27a54
Create Date: 2026-07-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c94f3b7e20'
down_revision: Union[str, Sequence[str], None] = 'e3d1f8c27a54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'ingredients',
        sa.Column('value_tier', sa.String(length=10), nullable=False, server_default='any'),
    )
    op.add_column('ingredients', sa.Column('value_note', sa.String(length=200), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ingredients', 'value_note')
    op.drop_column('ingredients', 'value_tier')
