"""add location to supermarkets

Revision ID: c4e7a2d91b30
Revises: 67a229a2837f
Create Date: 2026-09-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e7a2d91b30'
down_revision: Union[str, Sequence[str], None] = '67a229a2837f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('supermarkets', sa.Column('latitude', sa.Float(), nullable=True))
    op.add_column('supermarkets', sa.Column('longitude', sa.Float(), nullable=True))
    op.add_column('supermarkets', sa.Column('radius_m', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('supermarkets') as batch_op:
        batch_op.drop_column('radius_m')
        batch_op.drop_column('longitude')
        batch_op.drop_column('latitude')
