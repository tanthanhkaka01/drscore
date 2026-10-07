"""snapshot history: cleaning the cache moves snapshots, it never loses them

Owner decision 2026-10-06. The application moves snapshots from drs_report_snapshot into
drs_report_snapshot_history (INSERT ... SELECT, then DELETE, in one transaction). No trigger.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa

import drscore.db.types
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

def upgrade() -> None:
    with op.batch_alter_table('drs_report_snapshot', schema=None) as batch_op:
        batch_op.add_column(sa.Column('report_code', sa.String(length=50), nullable=True))

    op.create_table('drs_report_snapshot_history',
    sa.Column('snapshot_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=False, nullable=False),
    sa.Column('report_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('report_code', sa.String(length=50), nullable=True),
    sa.Column('cache_key', sa.String(length=64), nullable=False),
    sa.Column('params_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('columns_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('rows_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('byte_size', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('source_duration_ms', sa.Integer(), nullable=False),
    sa.Column('created_at', drscore.db.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('expires_at', drscore.db.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=100), nullable=True),
    sa.Column('archived_at', drscore.db.types.UTCDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('archive_reason', sa.String(length=10), nullable=False),
    sa.CheckConstraint("archive_reason IN ('EXPIRED', 'CLEARED')", name=op.f('ck_drs_report_snapshot_history_archive_reason')),
    sa.PrimaryKeyConstraint('snapshot_id', name=op.f('pk_drs_report_snapshot_history'))
    )
    with op.batch_alter_table('drs_report_snapshot_history', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_drs_report_snapshot_history_archived_at'), ['archived_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_drs_report_snapshot_history_report_code'), ['report_code'], unique=False)
        batch_op.create_index(batch_op.f('ix_drs_report_snapshot_history_report_id_cache_key'), ['report_id', 'cache_key'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('drs_report_snapshot_history', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_drs_report_snapshot_history_report_id_cache_key'))
        batch_op.drop_index(batch_op.f('ix_drs_report_snapshot_history_report_code'))
        batch_op.drop_index(batch_op.f('ix_drs_report_snapshot_history_archived_at'))
    op.drop_table('drs_report_snapshot_history')
    with op.batch_alter_table('drs_report_snapshot', schema=None) as batch_op:
        batch_op.drop_column('report_code')
