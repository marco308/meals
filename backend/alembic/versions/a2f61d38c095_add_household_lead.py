"""add households.lead_user_id

Revision ID: a2f61d38c095
Revises: e7a94d21c8b3
Create Date: 2026-08-18 12:20:00.000000

Decision Q23 (household admin). A household now names one member as its lead:
the account a subscription belongs to, and the only one who may invite or
remove people. Nothing about the food changes — every member still does
everything to recipes, plans and lists.

The column is nullable in the schema but never NULL in practice: a household
row is inserted before the user who will lead it exists, and SET NULL is what
lets `services/accounts.py` delete a household's users before the household
itself. "Exactly one lead, always" is an invariant that module holds.

Existing households backfill to their earliest user, who is by construction the
person whose registration created the household (Q19) — every other member
arrived later, through an invite.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2f61d38c095'
down_revision: Union[str, Sequence[str], None] = 'e7a94d21c8b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# One row per household: its earliest user by created_at, with the id as a
# tie-break so a household whose members were created in the same transaction
# still resolves to exactly one lead rather than an arbitrary one.
BACKFILL = sa.text(
    """
    UPDATE households
       SET lead_user_id = (
           SELECT u.id FROM users u
            WHERE u.household_id = households.id
            ORDER BY u.created_at, u.id
            LIMIT 1
       )
    """
)


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite cannot add a column with a REFERENCES clause to an existing table,
    # so the constraint arrives with a table rebuild; Postgres takes it inline.
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        op.add_column('households', sa.Column('lead_user_id', sa.Uuid(), nullable=True))
        with op.batch_alter_table('households') as batch:
            batch.create_foreign_key(
                'households_lead_user_id_fkey', 'users', ['lead_user_id'], ['id'], ondelete='SET NULL'
            )
    else:
        op.add_column('households', sa.Column('lead_user_id', sa.Uuid(), nullable=True))
        op.create_foreign_key(
            'households_lead_user_id_fkey', 'households', 'users', ['lead_user_id'], ['id'], ondelete='SET NULL'
        )
    op.execute(BACKFILL)


def downgrade() -> None:
    """Downgrade schema.

    Dropping the column loses which member was the lead. Nothing else depends
    on it, so a household simply goes back to having no distinguished member.
    """
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table('households') as batch:
            batch.drop_constraint('households_lead_user_id_fkey', type_='foreignkey')
            batch.drop_column('lead_user_id')
        return
    op.drop_constraint('households_lead_user_id_fkey', 'households', type_='foreignkey')
    op.drop_column('households', 'lead_user_id')
