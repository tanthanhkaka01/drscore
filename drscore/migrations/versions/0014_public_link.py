"""public links: one report opened without signing in, by a secret URL

Owner, 2026-10-21: the president's report opens from a link pasted into a browser, with no login,
on a desk screen or a wall TV.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

import drscore.db.types

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None

ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
NOW = sa.text('(CURRENT_TIMESTAMP)')


def upgrade() -> None:
    op.create_table('drs_public_link',
    sa.Column('link_id', ID, autoincrement=True, nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('token_hint', sa.String(length=8), nullable=False),
    sa.Column('report_id', ID, nullable=False),
    sa.Column('run_as_user_id', ID, nullable=False),
    sa.Column('title', sa.String(length=200), nullable=True),
    sa.Column('params_json', JSON, nullable=True),
    sa.Column('editable_json', JSON, nullable=True),
    sa.Column('view_key', sa.String(length=10), server_default='bi', nullable=False),
    sa.Column('refresh_seconds', sa.Integer(), nullable=True),
    sa.Column('expires_at', drscore.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('created_by', sa.String(length=100), nullable=False),
    sa.Column('created_at', drscore.db.types.UTCDateTime(timezone=True), server_default=NOW, nullable=False),
    sa.Column('last_used_at', drscore.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('use_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.CheckConstraint("view_key IN ('bi', 'grid')", name=op.f('ck_drs_public_link_view_key')),
    sa.ForeignKeyConstraint(['report_id'], ['drs_report.report_id'],
                            name=op.f('fk_drs_public_link_report_id_drs_report'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_as_user_id'], ['drs_user.user_id'],
                            name=op.f('fk_drs_public_link_run_as_user_id_drs_user'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('link_id', name=op.f('pk_drs_public_link')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_drs_public_link_token_hash')),
    sqlite_autoincrement=True
    )


def downgrade() -> None:
    op.drop_table('drs_public_link')
