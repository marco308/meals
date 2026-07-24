"""add staple_needed to list items

Revision ID: e3d1f8c27a54
Revises: b56507787d79
Create Date: 2026-07-24 23:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3d1f8c27a54'
down_revision: Union[str, Sequence[str], None] = 'b56507787d79'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'list_items',
        sa.Column('staple_needed', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('list_items', 'staple_needed')
