"""a household's token for the freezer label printer

Nullable and additive: every household has no printer until somebody pastes a
token in Settings, and the outgoing container never reads the column.

Revision ID: d5b2e8f1a9c4
Revises: a1c5e9d3b7f2
Create Date: 2026-10-01 08:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5b2e8f1a9c4"
down_revision: str | Sequence[str] | None = "a1c5e9d3b7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("households", sa.Column("label_printer_token", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("households", "label_printer_token")
