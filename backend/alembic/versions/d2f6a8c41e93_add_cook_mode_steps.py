"""add cook mode steps and the household's assistant webhook

Cook mode (decision Q29). Recipes gain the steps an assistant wrote for them,
the fingerprint of the method and ingredients those steps were written
against, and when the assistant was last asked. Households gain a webhook for
their own assistant, its token and an auto-prepare switch. Additive only, so
the outgoing container keeps serving through a start-first rollout: it reads
none of them.

Revision ID: d2f6a8c41e93
Revises: b8d4f2a61e07
Create Date: 2026-10-25 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2f6a8c41e93"
down_revision: str | Sequence[str] | None = "b8d4f2a61e07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("recipes", sa.Column("cook_steps", sa.JSON(), nullable=True))
    op.add_column("recipes", sa.Column("cook_steps_fingerprint", sa.String(length=32), nullable=True))
    op.add_column("recipes", sa.Column("cook_steps_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("households", sa.Column("assistant_webhook_url", sa.String(length=500), nullable=True))
    op.add_column("households", sa.Column("assistant_webhook_token", sa.String(length=500), nullable=True))
    op.add_column(
        "households",
        sa.Column("assistant_auto_prepare", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("households", "assistant_auto_prepare")
    op.drop_column("households", "assistant_webhook_token")
    op.drop_column("households", "assistant_webhook_url")
    op.drop_column("recipes", "cook_steps_requested_at")
    op.drop_column("recipes", "cook_steps_fingerprint")
    op.drop_column("recipes", "cook_steps")
