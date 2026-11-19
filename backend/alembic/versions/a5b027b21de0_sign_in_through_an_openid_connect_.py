"""sign in through an OpenID Connect provider

Revision ID: a5b027b21de0
Revises: e4a1c7b92f30
Create Date: 2026-11-19 21:51:13.253520

Decision Q33 (issue #93). Two new tables and one loosened column, all additive,
so the outgoing container keeps working through a start-first rollout: it reads
neither table, and no account without a password can exist until the new code
has made one.

- `user_identities`: which account at the operator's provider signs in as
  which user, by `(issuer, subject)`.
- `oidc_flows`: one trip to the provider and back, alive for minutes.
- `users.password_hash` becomes nullable: an account made through the provider
  has never had a password.

On SQLite the last of these rebuilds `users`. The migration connection runs
with foreign keys off (only the app's engine turns them on), so dropping the old
table cascades into nothing.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a5b027b21de0'
down_revision: Union[str, Sequence[str], None] = 'e4a1c7b92f30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('oidc_flows',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('state_hash', sa.String(length=64), nullable=False),
    sa.Column('browser_hash', sa.String(length=64), nullable=False),
    sa.Column('intent', sa.String(length=20), nullable=False),
    sa.Column('nonce', sa.String(length=64), nullable=False),
    sa.Column('code_verifier', sa.String(length=128), nullable=False),
    sa.Column('redirect_uri', sa.String(length=500), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('invite_code_hash', sa.String(length=64), nullable=True),
    sa.Column('household_name', sa.String(length=200), nullable=True),
    sa.Column('payload', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('handoff_hash', sa.String(length=64), nullable=True),
    sa.Column('result_user_id', sa.Uuid(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('error_status', sa.Integer(), nullable=True),
    sa.Column('notice', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['result_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('oidc_flows', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_oidc_flows_handoff_hash'), ['handoff_hash'], unique=True)
        batch_op.create_index(batch_op.f('ix_oidc_flows_state_hash'), ['state_hash'], unique=True)

    op.create_table('user_identities',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('issuer', sa.String(length=500), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('issuer', 'subject', name='uq_user_identities_issuer_subject'),
    sa.UniqueConstraint('user_id', 'issuer', name='uq_user_identities_user_issuer')
    )
    with op.batch_alter_table('user_identities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_identities_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('password_hash',
               existing_type=sa.VARCHAR(length=200),
               nullable=True)



def downgrade() -> None:
    """Downgrade schema.

    Refuses while any account has no password: the old schema cannot hold one,
    and the only way to make it fit would be to delete people or invent a
    password for them. Each can add one with a reset code first, or be deleted
    on purpose."""
    passwordless = op.get_bind().execute(sa.text('SELECT COUNT(*) FROM users WHERE password_hash IS NULL')).scalar()
    if passwordless:
        raise RuntimeError(
            f'{passwordless} account(s) sign in only through the identity provider and have no password, which '
            'the schema before this revision cannot hold. Have each set one (a password reset does it) or delete '
            'them, then downgrade again.'
        )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('password_hash',
               existing_type=sa.VARCHAR(length=200),
               nullable=False)

    with op.batch_alter_table('user_identities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_identities_user_id'))

    op.drop_table('user_identities')
    with op.batch_alter_table('oidc_flows', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oidc_flows_state_hash'))
        batch_op.drop_index(batch_op.f('ix_oidc_flows_handoff_hash'))

    op.drop_table('oidc_flows')
