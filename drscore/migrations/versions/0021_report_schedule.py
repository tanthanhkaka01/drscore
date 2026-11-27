"""drs_report_schedule: a report read again on a schedule, inside a time window

Owner, 2026-11-27 (ORD 485): reports that scan the production HRMS server must read it once a night
between 01:00 and 05:00 (+07), and never during the day. drscore read a source only when someone opened
a report whose snapshot had expired. A schedule now says when a report reads its source by itself (the
time-window fields of dbabrain: from_hour / to_hour, weekdays, repeat_interval ...), and live_runs
'window' keeps readers on the newest snapshot outside that window.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

import drscore.db.types

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None

ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
NOW = sa.text('(CURRENT_TIMESTAMP)')


def upgrade() -> None:
    op.create_table('drs_report_schedule',
    sa.Column('report_id', ID, nullable=False),
    sa.Column('time_window', JSON, nullable=False),
    sa.Column('live_runs', sa.String(length=10), server_default='always', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('last_started_at', drscore.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('last_finished_at', drscore.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('last_status', sa.String(length=20), nullable=True),
    sa.Column('last_rows', sa.Integer(), nullable=True),
    sa.Column('last_duration_ms', sa.Integer(), nullable=True),
    sa.Column('last_snapshot_id', sa.Integer(), nullable=True),
    sa.Column('last_error', sa.String(length=2000), nullable=True),
    sa.Column('lock_owner', sa.String(length=100), nullable=True),
    sa.Column('lock_until', drscore.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', drscore.db.types.UTCDateTime(timezone=True), server_default=NOW, nullable=False),
    sa.Column('updated_at', drscore.db.types.UTCDateTime(timezone=True), server_default=NOW, nullable=False),
    sa.Column('updated_by', sa.String(length=100), nullable=True),
    sa.CheckConstraint("live_runs IN ('always', 'window')", name=op.f('ck_drs_report_schedule_live_runs')),
    sa.CheckConstraint("last_status IS NULL OR last_status IN ('ok', 'error', 'running')",
                       name=op.f('ck_drs_report_schedule_last_status')),
    sa.ForeignKeyConstraint(['report_id'], ['drs_report.report_id'], ondelete='CASCADE',
                            name=op.f('fk_drs_report_schedule_report_id_drs_report')),
    sa.PrimaryKeyConstraint('report_id', name=op.f('pk_drs_report_schedule')),
    )


def downgrade() -> None:
    op.drop_table('drs_report_schedule')
