"""email verification and the reaping warning

Two nullable columns, both additive, so the outgoing container keeps working
through a start-first rollout: it reads neither.

- `users.email_verified_at`: when the address was confirmed by a code sent to it
  (Q25). **Every existing account is backfilled as verified**, at its creation
  date. Each was made by somebody the operator knew or has been using the server
  already, and leaving them unverified would lock inviting and URL ingest on
  iPhone builds that have no screen to enter a code on.
- `households.reap_warned_at`: when a household that never began was told it
  would be reaped (`services/reaping.py`). Not backfilled: nobody has been told.

Revision ID: c4e8a1f2d9b3
Revises: 67a229a2837f
Create Date: 2026-09-27 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e8a1f2d9b3"
down_revision: str | Sequence[str] | None = "67a229a2837f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reap_warned_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE users SET email_verified_at = created_at"))


def downgrade() -> None:
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.drop_column("reap_warned_at")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("email_verified_at")
