"""superset_username on users: the Superset account of a DRS user whose name differs

Owner, 2026-10-15: a designer signs in to DRS and to Superset with two accounts, so the BI page
cannot ask for the same name on both; the administrator links them once on the user.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-15
"""

from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


# Not a batch operation (see 0008: a batch rebuilds drs_user on SQLite without AUTOINCREMENT). A
# unique index, not a constraint: both engines create it in place, and allow many empty values.

def upgrade() -> None:
    op.add_column('drs_user', sa.Column('superset_username', sa.String(length=100), nullable=True))
    op.create_index(op.f('ix_drs_user_superset_username'), 'drs_user', ['superset_username'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_drs_user_superset_username'), table_name='drs_user')
    op.drop_column('drs_user', 'superset_username')
