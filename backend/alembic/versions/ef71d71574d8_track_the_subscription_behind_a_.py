"""track the subscription behind a household's entitlement

Four columns on `households`, all nullable and all additive, so the outgoing
container keeps working through a start-first rollout: it reads none of them.

- `billing_subscription_id` and `billing_subscription_state`: which subscription
  at the processor the entitlement follows, and whether it will renew. Without
  them a webhook about *any* subscription could grant or end this household's,
  and nothing could tell a live subscription from one already cancelled.
- `billing_event_at`: the processor's time for the newest event applied, so a
  retry from before a cancellation cannot grant the year back.
- `billing_user_id`: whose card it is. The portal is theirs alone, and they may
  not walk away from a subscription that will charge them again.

The payer is backfilled to the lead for every household that has paid, because
until now only the lead could open a checkout. The subscription columns are not
backfilled: nothing this database holds says which subscription it was, so a
household that paid before this revision stays untracked until its next event
names its subscription (`services/billing.py` adopts it then).

Revision ID: ef71d71574d8
Revises: b9d33848e592
Create Date: 2026-09-25 08:07:48.144419

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ef71d71574d8"
down_revision: str | Sequence[str] | None = "b9d33848e592"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL_PAYER = sa.text(
    """
    UPDATE households
       SET billing_user_id = lead_user_id
     WHERE billing_customer_id IS NOT NULL
        OR entitlement_source IN ('stripe', 'paddle', 'lemonsqueezy')
    """
)


def upgrade() -> None:
    # Batch mode rebuilds the table on SQLite, which cannot add a REFERENCES
    # clause to an existing one, and issues plain ALTERs on Postgres.
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.add_column(sa.Column("billing_subscription_id", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("billing_subscription_state", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("billing_event_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("billing_user_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "households_billing_user_id_fkey", "users", ["billing_user_id"], ["id"], ondelete="SET NULL"
        )
    op.execute(BACKFILL_PAYER)


def downgrade() -> None:
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.drop_constraint("households_billing_user_id_fkey", type_="foreignkey")
        batch_op.drop_column("billing_user_id")
        batch_op.drop_column("billing_event_at")
        batch_op.drop_column("billing_subscription_state")
        batch_op.drop_column("billing_subscription_id")
