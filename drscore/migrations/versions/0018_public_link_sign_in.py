"""drs_public_link.is_public: a link may require a sign-in and the reader's own rights

Owner, 2026-10-26 (ORD 402): every report has its own address that opens it at once, showing the
newest data; per link, "public" on (anybody with the address, as today) or off (the sign-in page
first, then only a reader with rights on the report). The links made before stay public.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-26
"""

from alembic import op
import sqlalchemy as sa

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('drs_public_link', sa.Column('is_public', sa.Boolean(), server_default=sa.true(), nullable=False))


def downgrade() -> None:
    # a link that asked for a sign-in must not become public: switched off
    op.execute("UPDATE drs_public_link SET is_active = false WHERE is_public = false")
    if op.get_bind().dialect.name == 'sqlite':  # rebuilt, keeping AUTOINCREMENT (see 0015)
        with op.batch_alter_table('drs_public_link', recreate='always',
                                  table_kwargs={'sqlite_autoincrement': True}) as batch_op:
            batch_op.drop_column('is_public')
    else:
        op.drop_column('drs_public_link', 'is_public')
