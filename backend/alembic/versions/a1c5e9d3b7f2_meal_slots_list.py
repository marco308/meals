"""a meal can fill more than one slot

`meals.slots` is the list ("breakfast or lunch"); `meals.slot` stays and keeps
its first entry, so builds already on phones read and write what they always
did and the outgoing container keeps working through a start-first rollout.
Backfilled from `slot`, so every existing meal keeps the one slot it had.

Revision ID: a1c5e9d3b7f2
Revises: c4e8a1f2d9b3
Create Date: 2026-09-27 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c5e9d3b7f2"
down_revision: str | Sequence[str] | None = "c4e8a1f2d9b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meals", sa.Column("slots", sa.JSON(), nullable=False, server_default="[]"))
    meals = sa.table("meals", sa.column("id", sa.Uuid()), sa.column("slot", sa.String()), sa.column("slots", sa.JSON()))
    bind = op.get_bind()
    for meal_id, slot in bind.execute(sa.select(meals.c.id, meals.c.slot).where(meals.c.slot.is_not(None))).all():
        bind.execute(meals.update().where(meals.c.id == meal_id).values(slots=[slot]))


def downgrade() -> None:
    op.drop_column("meals", "slots")
