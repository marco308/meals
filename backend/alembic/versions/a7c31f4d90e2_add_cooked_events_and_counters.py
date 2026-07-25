"""add cooked_events and the times_cooked counters

Revision ID: a7c31f4d90e2
Revises: e3d1f8c27a54
Create Date: 2026-07-25 09:10:00.000000

Backfills from the existing PlanMeal.cooked_at timestamps so history already on
the deployed instance survives the move to an events table.
"""
import uuid
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c31f4d90e2'
down_revision: Union[str, Sequence[str], None] = 'e3d1f8c27a54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COOKED_EVENTS = sa.table(
    'cooked_events',
    sa.column('id', sa.Uuid()),
    sa.column('household_id', sa.Uuid()),
    sa.column('subject', sa.String()),
    sa.column('meal_id', sa.Uuid()),
    sa.column('recipe_id', sa.Uuid()),
    sa.column('plan_meal_id', sa.Uuid()),
    sa.column('meal_name', sa.String()),
    sa.column('recipe_title', sa.String()),
    sa.column('cooked_at', sa.DateTime(timezone=True)),
    sa.column('created_by', sa.Uuid()),
    sa.column('created_at', sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'cooked_events',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('household_id', sa.Uuid(), nullable=False),
        sa.Column('subject', sa.String(length=10), nullable=False),
        sa.Column('meal_id', sa.Uuid(), nullable=True),
        sa.Column('recipe_id', sa.Uuid(), nullable=True),
        sa.Column('plan_meal_id', sa.Uuid(), nullable=True),
        sa.Column('meal_name', sa.String(length=300), nullable=True),
        sa.Column('recipe_title', sa.String(length=300), nullable=True),
        sa.Column('cooked_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['household_id'], ['households.id'], ),
        sa.ForeignKeyConstraint(['meal_id'], ['meals.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['recipe_id'], ['recipes.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['plan_meal_id'], ['plan_meals.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cooked_events_household_id', 'cooked_events', ['household_id'])
    op.create_index('ix_cooked_events_meal', 'cooked_events', ['meal_id', 'subject'])
    op.create_index('ix_cooked_events_recipe', 'cooked_events', ['recipe_id', 'subject'])

    for table in ('recipes', 'meals'):
        op.add_column(
            table, sa.Column('times_cooked', sa.Integer(), nullable=False, server_default='0')
        )
        op.add_column(table, sa.Column('last_cooked_at', sa.DateTime(timezone=True), nullable=True))

    _backfill()


def _as_uuid(value):
    """Raw SQL hands back native UUIDs on Postgres and hex strings on SQLite;
    sa.Uuid bind processing only accepts the former."""
    if isinstance(value, str):
        return uuid.UUID(value)
    return value


def _as_datetime(value):
    """Same story for timestamps: Postgres returns datetimes, SQLite strings."""
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _backfill() -> None:
    """Recreate the event log from PlanMeal.cooked_at, then set the counters.

    Only meals still reachable from a plan-meal can be recovered — that's
    exactly the history the old model was capable of holding.
    """
    bind = op.get_bind()
    cooked = bind.execute(
        sa.text(
            """
            SELECT pm.id AS plan_meal_id, pm.cooked_at, m.id AS meal_id, m.name AS meal_name,
                   m.household_id
            FROM plan_meals pm
            JOIN meals m ON m.id = pm.meal_id
            WHERE pm.cooked_at IS NOT NULL
            """
        )
    ).mappings().all()
    if not cooked:
        return

    recipe_rows = bind.execute(
        sa.text(
            """
            SELECT mr.meal_id, mr.recipe_id, r.title
            FROM meal_recipes mr
            JOIN recipes r ON r.id = mr.recipe_id
            """
        )
    ).mappings().all()
    by_meal: dict = {}
    for row in recipe_rows:
        by_meal.setdefault(_as_uuid(row['meal_id']), []).append(row)

    events = []
    for row in cooked:
        meal_id = _as_uuid(row['meal_id'])
        cooked_at = _as_datetime(row['cooked_at'])
        common = {
            'household_id': _as_uuid(row['household_id']),
            'meal_id': meal_id,
            'plan_meal_id': _as_uuid(row['plan_meal_id']),
            'meal_name': row['meal_name'],
            'cooked_at': cooked_at,
            'created_by': None,
            'created_at': cooked_at,
        }
        events.append({'id': uuid.uuid4(), 'subject': 'meal', 'recipe_id': None, 'recipe_title': None, **common})
        for recipe in by_meal.get(meal_id, []):
            events.append(
                {
                    'id': uuid.uuid4(),
                    'subject': 'recipe',
                    'recipe_id': _as_uuid(recipe['recipe_id']),
                    'recipe_title': recipe['title'],
                    **common,
                }
            )
    op.bulk_insert(COOKED_EVENTS, events)

    for table, subject, key in (('meals', 'meal', 'meal_id'), ('recipes', 'recipe', 'recipe_id')):
        bind.execute(
            sa.text(
                f"""
                UPDATE {table} SET
                    times_cooked = (
                        SELECT COUNT(*) FROM cooked_events e
                        WHERE e.{key} = {table}.id AND e.subject = '{subject}'
                    ),
                    last_cooked_at = (
                        SELECT MAX(e.cooked_at) FROM cooked_events e
                        WHERE e.{key} = {table}.id AND e.subject = '{subject}'
                    )
                """
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in ('recipes', 'meals'):
        op.drop_column(table, 'last_cooked_at')
        op.drop_column(table, 'times_cooked')
    op.drop_index('ix_cooked_events_recipe', table_name='cooked_events')
    op.drop_index('ix_cooked_events_meal', table_name='cooked_events')
    op.drop_index('ix_cooked_events_household_id', table_name='cooked_events')
    op.drop_table('cooked_events')
