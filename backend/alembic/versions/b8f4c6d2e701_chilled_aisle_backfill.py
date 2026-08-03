"""chilled aisle backfill

Revision ID: b8f4c6d2e701
Revises: f3a7c02e5b91
Create Date: 2026-08-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8f4c6d2e701'
down_revision: Union[str, Sequence[str], None] = 'f3a7c02e5b91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ingredients = sa.table(
    "ingredients",
    sa.column("name", sa.String),
    sa.column("aisle", sa.String),
)

# The exact canonical names the new ❄️ keyword table covers. Frozen into the
# migration (not imported from app.services.aisles) so later table edits can't
# change what this revision did. Only ❓ rows move — a ❓ here just means the
# old table had no entry, so nobody's own tagging is overridden.
_CHILLED_NAMES = (
    "houmous",
    "hummus",
    "tzatziki",
    "taramasalata",
    "guacamole",
    "dip",
    "fresh pasta",
    "tortellini",
    "ravioli",
    "fresh soup",
    "quiche",
    "coleslaw",
    "potato salad",
    "falafel",
    "pate",
    "pâté",
    "sausage roll",
)


def upgrade() -> None:
    """Re-file ❓ ingredients the new ❄️ Chilled keywords now cover."""
    op.execute(
        _ingredients.update()
        .where(_ingredients.c.aisle == "❓")
        .where(_ingredients.c.name.in_(_CHILLED_NAMES))
        .values(aisle="❄️")
    )


def downgrade() -> None:
    """❄️ isn't in the older vocabulary, so every chilled row returns to ❓."""
    op.execute(_ingredients.update().where(_ingredients.c.aisle == "❄️").values(aisle="❓"))
