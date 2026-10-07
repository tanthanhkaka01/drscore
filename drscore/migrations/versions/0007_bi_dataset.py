"""bi dataset: which snapshot of a BI report is loaded in its dataset table

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa

import drscore.db.types

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('drs_bi_dataset',
    sa.Column('report_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('table_name', sa.String(length=128), nullable=False),
    sa.Column('snapshot_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('loaded_at', drscore.db.types.UTCDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['report_id'], ['drs_report.report_id'], name=op.f('fk_drs_bi_dataset_report_id_drs_report'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('report_id', name=op.f('pk_drs_bi_dataset'))
    )


def downgrade() -> None:
    op.drop_table('drs_bi_dataset')
