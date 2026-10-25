"""add display_unit to recipe and meal lines

Metric underneath, spoons on top (decision Q28). A nullable `display_unit`
("tsp" or "tbsp") on recipe and meal-ingredient lines remembers how an ml
amount was written, so it reads back as a spoon. Additive only, so the outgoing
container keeps serving through a start-first rollout: it reads neither.

Recipe lines already stored are backfilled from their own text: an ml amount
whose raw line says "tsp" or "tablespoons" gets that spoon. Meal lines keep no
raw text, and the rendering rule covers them without a hint.

Revision ID: b8d4f2a61e07
Revises: c7e3a9f04d12
Create Date: 2026-10-25 14:00:00.000000

"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d4f2a61e07"
down_revision: str | Sequence[str] | None = "c7e3a9f04d12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A frozen copy of units.spoon_hint's reading of raw text: a migration must not
# change meaning when the app's code does.
_SPOON = re.compile(r"\b(tsps?|teaspoons?|tbsps?|tablespoons?)\b", re.IGNORECASE)


def _spoon(raw: str) -> str | None:
    found = _SPOON.search(raw)
    if not found:
        return None
    return "tsp" if found.group(1).lower().startswith(("tsp", "tea")) else "tbsp"


def upgrade() -> None:
    op.add_column("recipe_ingredients", sa.Column("display_unit", sa.String(length=10), nullable=True))
    op.add_column("meal_ingredients", sa.Column("display_unit", sa.String(length=10), nullable=True))

    lines = sa.table(
        "recipe_ingredients",
        sa.column("id", sa.Uuid()),
        sa.column("unit", sa.String()),
        sa.column("raw_text", sa.String()),
        sa.column("display_unit", sa.String()),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(lines.c.id, lines.c.raw_text).where(lines.c.unit == "ml", lines.c.raw_text.is_not(None))
    )
    for line_id, raw in rows.all():
        spoon = _spoon(raw)
        if spoon is not None:
            bind.execute(sa.update(lines).where(lines.c.id == line_id).values(display_unit=spoon))


def downgrade() -> None:
    op.drop_column("meal_ingredients", "display_unit")
    op.drop_column("recipe_ingredients", "display_unit")
