"""must_change_password on users: the default administrator starts with a known password

Owner decision 2026-10-07: a DRS database without an administrator gets the administrator
admin / admin, who must choose another password at the first sign-in.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


# Not a batch operation: a batch rebuilds the table on SQLite, and the rebuilt drs_user came out
# without AUTOINCREMENT, so a deleted user's id was given to the next user (spec 8.1, rule 3).
# Both engines add and drop a column in place.

def upgrade() -> None:
    op.add_column('drs_user', sa.Column('must_change_password', sa.Boolean(), server_default=sa.false(),
                                        nullable=False))


def downgrade() -> None:
    op.drop_column('drs_user', 'must_change_password')
