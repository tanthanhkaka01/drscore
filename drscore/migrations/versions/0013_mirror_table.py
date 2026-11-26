"""mirrors: copies of source tables in the DRS database, refreshed on a schedule

Owner, 2026-10-22: heavy report queries must not run on the production ERP. Each source table a
report needs is copied, one table at a time, into the mirror schema; reports read the copy.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

import drscore.db.types

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None

ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
NOW = sa.text('(CURRENT_TIMESTAMP)')


def upgrade() -> None:
    op.create_table('drs_mirror_table',
    sa.Column('mirror_id', ID, autoincrement=True, nullable=False),
    sa.Column('mirror_code', sa.String(length=100), nullable=False),
    sa.Column('datasource_id', ID, nullable=False),
    sa.Column('source_object', sa.String(length=300), nullable=False),
    sa.Column('columns_json', JSON, nullable=True),
    sa.Column('where_sql', sa.Text(), nullable=True),
    sa.Column('target_table', sa.String(length=63), nullable=False),
    sa.Column('key_columns_json', JSON, nullable=True),
    sa.Column('mode', sa.String(length=20), server_default='full', nullable=False),
    sa.Column('watermark_column', sa.String(length=128), nullable=True),
    sa.Column('watermark_value', sa.String(length=100), nullable=True),
    sa.Column('lookback_seconds', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('interval_minutes', sa.Integer(), server_default=sa.text('60'), nullable=False),
    sa.Column('full_refresh_hours', sa.Integer(), nullable=True),
    sa.Column('run_hours', sa.String(length=100), nullable=True),
    sa.Column('full_hours', sa.String(length=100), nullable=True),
    sa.Column('read_uncommitted', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('last_started_at', drscore.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('last_finished_at', drscore.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('last_full_at', drscore.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('last_status', sa.String(length=20), nullable=True),
    sa.Column('last_rows', sa.Integer(), nullable=True),
    sa.Column('last_duration_ms', sa.Integer(), nullable=True),
    sa.Column('last_error', sa.String(length=2000), nullable=True),
    sa.Column('lock_owner', sa.String(length=100), nullable=True),
    sa.Column('lock_until', drscore.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', drscore.db.types.UTCDateTime(timezone=True), server_default=NOW, nullable=False),
    sa.Column('updated_at', drscore.db.types.UTCDateTime(timezone=True), server_default=NOW, nullable=False),
    sa.CheckConstraint("mode IN ('full', 'incremental')", name=op.f('ck_drs_mirror_table_mode')),
    sa.CheckConstraint("last_status IS NULL OR last_status IN ('ok', 'error', 'running')",
                       name=op.f('ck_drs_mirror_table_last_status')),
    sa.ForeignKeyConstraint(['datasource_id'], ['drs_datasource.datasource_id'],
                            name=op.f('fk_drs_mirror_table_datasource_id_drs_datasource')),
    sa.PrimaryKeyConstraint('mirror_id', name=op.f('pk_drs_mirror_table')),
    sa.UniqueConstraint('mirror_code', name=op.f('uq_drs_mirror_table_mirror_code')),
    sqlite_autoincrement=True
    )


def downgrade() -> None:
    op.drop_table('drs_mirror_table')
