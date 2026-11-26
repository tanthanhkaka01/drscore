"""drs_user.session_days: a sign-in of this user lasts that many days instead of the [session] limits

Owner, 2026-10-26 (ORD 402): the viewers of a dashboard sign in once and stay signed in for 30
days, the browser closed and the computer restarted in between; administrators and designers keep
the [session] limits of app.toml.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-26
"""

from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('drs_user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('session_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('drs_user', schema=None) as batch_op:
        batch_op.drop_column('session_days')
