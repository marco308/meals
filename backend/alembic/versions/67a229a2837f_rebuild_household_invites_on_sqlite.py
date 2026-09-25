"""household_invites on SQLite: drop the CASCADE key f3a7c02e5b91 left behind

f3a7c02e5b91 moved household_invites.created_by_user_id from ON DELETE CASCADE
to SET NULL, so that deleting an account keeps the record of who it admitted
(Q20). On Postgres it dropped the old key by name. On SQLite the old key had no
name, so the batch rebuild had nothing to drop it by: it added the SET NULL key
beside it, and every SQLite database migrated since has carried both. SQLite
runs both actions, so deleting a user there still deleted every invite they had
created, while Postgres kept them.

Nothing saw it, because the tests build their schema with create_all and CI
only migrates Postgres. tests/unit/test_migrations.py now migrates a SQLite
file and compares its foreign keys with the models'.

This rebuilds the table on SQLite from the definition below instead of by
reflection, since reflection is what carried the stray key forward, with
exactly the keys the model declares. Postgres is already right and is left
alone.

Revision ID: 67a229a2837f
Revises: b9b700d074ec
Create Date: 2026-09-25 08:06:49.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "67a229a2837f"
# Chained after the list-source migration, the head main reached while this
# was in review: two revisions sharing a down_revision are two heads, and
# `alembic upgrade head` on boot refuses to pick one.
down_revision: str | Sequence[str] | None = "b9b700d074ec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _household_invites() -> sa.Table:
    """The table as d9e4b17c3a86 created it and f3a7c02e5b91 meant to leave it.
    The index is declared on the table rather than as `index=True`, because
    batch mode only recreates indexes that are declared that way."""
    return sa.Table(
        "household_invites",
        sa.MetaData(),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="household_invites_created_by_user_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["accepted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.Index("ix_household_invites_code_hash", "code_hash", unique=True),
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    # Rows are copied across as they are: every one satisfies the new keys,
    # since they are the old ones minus a CASCADE.
    with op.batch_alter_table("household_invites", recreate="always", copy_from=_household_invites()):
        pass


def downgrade() -> None:
    """Nothing to undo. The table before this revision was an accident rather
    than a schema anybody chose, and what this leaves is what f3a7c02e5b91
    meant, which is also what its own downgrade expects to find."""
