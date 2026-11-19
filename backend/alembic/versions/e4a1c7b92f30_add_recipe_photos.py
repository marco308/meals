"""add recipe photos

A household may upload one photo per recipe (issue #280, decision Q32), stored
here rather than on a disk so the backups carry it. A new table and nothing
else, so the outgoing container keeps serving through a start-first rollout:
it never reads it.

Revision ID: e4a1c7b92f30
Revises: 37f6e1113037
Create Date: 2026-11-19 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4a1c7b92f30"
down_revision: str | Sequence[str] | None = "37f6e1113037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recipe_photos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"]),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recipe_id"),
        sa.UniqueConstraint("token"),
    )
    with op.batch_alter_table("recipe_photos", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_recipe_photos_household_id"), ["household_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("recipe_photos", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_recipe_photos_household_id"))
    op.drop_table("recipe_photos")
