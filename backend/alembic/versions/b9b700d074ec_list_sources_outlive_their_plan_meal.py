"""list_item_sources outlive their plan-meal: SET NULL, meal_name, ad_hoc

An archived shopping list is the record of a shop that happened, but taking a
meal off its plan afterwards cascaded into it: every line the meal had
contributed to lost those sources, and with them its quantity. The link now
goes to NULL instead, and the row keeps its quantity and a copy of the meal's
name, as `cooked_events` keeps its own.

A NULL `plan_meal_id` used to *mean* ad hoc, so that becomes a column of its
own, backfilled by exactly that rule while it still holds.

Additive, so the outgoing container keeps working through a start-first
rollout (checked against Postgres): it reads neither column, what it inserts
still satisfies both, and its plan-meal deletes now blank the link rather than
cascade. What it writes in those few seconds is labelled less well. An ad-hoc
add takes the server default, so the apps show it as a meal's line and offer no
delete button for it; the server would still delete it, since that is decided
by the plan-meal link rather than by this flag. A meal's contribution gets no
meal_name, which only shows if its plan-meal is later removed.

Revision ID: b9b700d074ec
Revises: ef71d71574d8
Create Date: 2026-09-25 09:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9b700d074ec"
# After the billing migration that landed first: two revisions sharing a parent
# are two heads, and `alembic upgrade head` on boot refuses to pick one.
down_revision: str | Sequence[str] | None = "ef71d71574d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The initial schema left this key unnamed, so Postgres named it after the column.
CONSTRAINT = "list_item_sources_plan_meal_id_fkey"
# SQLite reflects an unnamed key with no name at all, and batch mode can only
# drop a constraint it can name, so the rebuild is handed a convention to name
# it by. That is the name the convention gives it.
SQLITE_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
SQLITE_REFLECTED = "fk_list_item_sources_plan_meal_id_plan_meals"

SOURCES = sa.table(
    "list_item_sources",
    sa.column("plan_meal_id", sa.Uuid()),
    sa.column("ad_hoc", sa.Boolean()),
    sa.column("meal_name", sa.String()),
)
PLAN_MEALS = sa.table("plan_meals", sa.column("id", sa.Uuid()), sa.column("meal_id", sa.Uuid()))
MEALS = sa.table("meals", sa.column("id", sa.Uuid()), sa.column("name", sa.String()))


def _new_columns() -> list[sa.Column]:
    return [
        sa.Column("ad_hoc", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("meal_name", sa.String(length=300), nullable=True),
    ]


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        # SQLite bakes foreign-key actions into the table DDL, so the constraint
        # can only change by rebuilding the table, which batch mode does.
        with op.batch_alter_table("list_item_sources", naming_convention=SQLITE_NAMING) as batch:
            for column in _new_columns():
                batch.add_column(column)
            batch.drop_constraint(SQLITE_REFLECTED, type_="foreignkey")
            batch.create_foreign_key(CONSTRAINT, "plan_meals", ["plan_meal_id"], ["id"], ondelete="SET NULL")
    else:
        for column in _new_columns():
            op.add_column("list_item_sources", column)
        op.drop_constraint(CONSTRAINT, "list_item_sources", type_="foreignkey")
        op.create_foreign_key(
            CONSTRAINT, "list_item_sources", "plan_meals", ["plan_meal_id"], ["id"], ondelete="SET NULL"
        )

    # Until now a meal's contributions went with its plan-meal, so every row
    # without one is an ad-hoc add and every other row can still reach its meal.
    op.execute(SOURCES.update().where(SOURCES.c.plan_meal_id.is_(None)).values(ad_hoc=sa.true()))
    meal_name = (
        sa.select(MEALS.c.name)
        .select_from(PLAN_MEALS.join(MEALS, MEALS.c.id == PLAN_MEALS.c.meal_id))
        .where(PLAN_MEALS.c.id == SOURCES.c.plan_meal_id)
        .scalar_subquery()
    )
    op.execute(SOURCES.update().where(SOURCES.c.plan_meal_id.is_not(None)).values(meal_name=meal_name))


def downgrade() -> None:
    """A contribution that outlived its plan-meal has no place in the old
    schema, where no plan-meal meant ad hoc, so it goes: the old cascade would
    have deleted it when its plan-meal went."""
    op.execute(SOURCES.delete().where(SOURCES.c.plan_meal_id.is_(None), SOURCES.c.ad_hoc == sa.false()))
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("list_item_sources") as batch:
            batch.drop_constraint(CONSTRAINT, type_="foreignkey")
            # Batch mode cannot put the key back unnamed, as it started, so it
            # gets the name upgrade() looks for, and a second upgrade finds it.
            batch.create_foreign_key(SQLITE_REFLECTED, "plan_meals", ["plan_meal_id"], ["id"], ondelete="CASCADE")
            batch.drop_column("meal_name")
            batch.drop_column("ad_hoc")
        return
    op.drop_constraint(CONSTRAINT, "list_item_sources", type_="foreignkey")
    op.create_foreign_key(CONSTRAINT, "list_item_sources", "plan_meals", ["plan_meal_id"], ["id"], ondelete="CASCADE")
    op.drop_column("list_item_sources", "meal_name")
    op.drop_column("list_item_sources", "ad_hoc")
