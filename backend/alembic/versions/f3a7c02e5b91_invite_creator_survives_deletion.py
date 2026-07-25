"""household_invites.created_by_user_id: CASCADE -> SET NULL

Revision ID: f3a7c02e5b91
Revises: d9e4b17c3a86
Create Date: 2026-07-25 21:40:00.000000

Decision Q20 (account deletion). The invite table's whole reason for keeping
redeemed rows is the record of who admitted whom; under ON DELETE CASCADE that
record vanished the moment the person who issued the invite deleted their
account. SET NULL keeps the row and blanks the reference, which needs the column
to be nullable.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a7c02e5b91'
down_revision: Union[str, Sequence[str], None] = 'd9e4b17c3a86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = 'household_invites_created_by_user_id_fkey'


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        # SQLite bakes foreign-key actions into the table DDL, so the constraint
        # can only change by rebuilding the table. Batch mode does that, and the
        # FK is respecified here because the rebuild reflects the *old* schema.
        with op.batch_alter_table('household_invites') as batch:
            batch.alter_column('created_by_user_id', existing_type=sa.Uuid(), nullable=True)
            batch.create_foreign_key(
                CONSTRAINT, 'users', ['created_by_user_id'], ['id'], ondelete='SET NULL'
            )
        return
    op.alter_column('household_invites', 'created_by_user_id', existing_type=sa.Uuid(), nullable=True)
    op.drop_constraint(CONSTRAINT, 'household_invites', type_='foreignkey')
    op.create_foreign_key(
        CONSTRAINT, 'household_invites', 'users', ['created_by_user_id'], ['id'], ondelete='SET NULL'
    )


def downgrade() -> None:
    """Downgrade schema.

    Rows whose creator has since been deleted have a NULL here and cannot
    satisfy the old NOT NULL, so they are removed — they are audit records of an
    account that no longer exists, and the pre-Q20 schema had no way to hold them.
    """
    op.execute(sa.text('DELETE FROM household_invites WHERE created_by_user_id IS NULL'))
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table('household_invites') as batch:
            batch.alter_column('created_by_user_id', existing_type=sa.Uuid(), nullable=False)
            batch.create_foreign_key(
                CONSTRAINT, 'users', ['created_by_user_id'], ['id'], ondelete='CASCADE'
            )
        return
    op.drop_constraint(CONSTRAINT, 'household_invites', type_='foreignkey')
    op.create_foreign_key(
        CONSTRAINT, 'household_invites', 'users', ['created_by_user_id'], ['id'], ondelete='CASCADE'
    )
    op.alter_column('household_invites', 'created_by_user_id', existing_type=sa.Uuid(), nullable=False)
