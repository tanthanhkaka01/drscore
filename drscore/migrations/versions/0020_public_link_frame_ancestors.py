"""drs_public_link.frame_ancestors: the other sites that may show a link in a frame

Owner, 2026-11-11 (ORD 437): the HRMS report links are to be shown inside other sites' pages (an
iframe), "*" for any site or a list such as https://hrms.example.com - set per link on the
Public links page. Empty (every link made before) = this portal only, as until now.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-11
"""

from alembic import op
import sqlalchemy as sa

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('drs_public_link', sa.Column('frame_ancestors', sa.String(1000), nullable=True))


def downgrade() -> None:
    if op.get_bind().dialect.name == 'sqlite':  # rebuilt, keeping AUTOINCREMENT (see 0015)
        with op.batch_alter_table('drs_public_link', recreate='always',
                                  table_kwargs={'sqlite_autoincrement': True}) as batch_op:
            batch_op.drop_column('frame_ancestors')
    else:
        op.drop_column('drs_public_link', 'frame_ancestors')
