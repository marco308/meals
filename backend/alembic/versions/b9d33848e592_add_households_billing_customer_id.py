"""add households.billing_customer_id

Who a household is at the payment processor (issue #129). The webhook has it on
every subscription it verifies, and keeping it is what lets `POST /billing/portal`
open a customer's own portal instead of a login page that emails them a link.

Additive and nullable, so the outgoing container keeps working through a
start-first rollout: it does not read this column and nothing it writes needs it.

Autogenerate also offered a `ck_meal_recipe_scale` check constraint on
`meal_recipes`. That is left out on purpose — it is not part of this change, it
already exists where it matters, and adding it would rebuild that table under
SQLite's batch mode for nothing.

Revision ID: b9d33848e592
Revises: a6c2e9f14b37
Create Date: 2026-08-24 07:38:22.342319

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9d33848e592"
down_revision: str | Sequence[str] | None = "a6c2e9f14b37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.add_column(sa.Column("billing_customer_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.drop_column("billing_customer_id")
