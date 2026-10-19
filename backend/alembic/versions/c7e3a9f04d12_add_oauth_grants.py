"""add oauth grants

OAuth for the remote MCP (issue #8, decision Q27). A new table, `oauth_grants`,
one row per connection a person approves, and one nullable column on
`auth_tokens` naming the connection an access or refresh token belongs to.
Additive only, so the outgoing container keeps serving through a start-first
rollout: it reads neither.

Revision ID: c7e3a9f04d12
Revises: d5b2e8f1a9c4
Create Date: 2026-10-19 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e3a9f04d12"
down_revision: str | Sequence[str] | None = "d5b2e8f1a9c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_name", sa.String(length=200), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("resource", sa.String(length=500), nullable=False),
        sa.Column("scope", sa.String(length=200), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("code_challenge", sa.String(length=128), nullable=False),
        sa.Column("code_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_oauth_grants_code_hash"), "oauth_grants", ["code_hash"], unique=True)
    op.create_index(op.f("ix_oauth_grants_user_id"), "oauth_grants", ["user_id"], unique=False)
    # Batch mode rebuilds the table on SQLite, which cannot add a REFERENCES
    # clause to an existing one, and issues plain ALTERs on Postgres.
    with op.batch_alter_table("auth_tokens", schema=None) as batch_op:
        batch_op.add_column(sa.Column("grant_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "auth_tokens_grant_id_fkey", "oauth_grants", ["grant_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_index(batch_op.f("ix_auth_tokens_grant_id"), ["grant_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("auth_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_auth_tokens_grant_id"))
        batch_op.drop_constraint("auth_tokens_grant_id_fkey", type_="foreignkey")
        batch_op.drop_column("grant_id")
    op.drop_index(op.f("ix_oauth_grants_user_id"), table_name="oauth_grants")
    op.drop_index(op.f("ix_oauth_grants_code_hash"), table_name="oauth_grants")
    op.drop_table("oauth_grants")
