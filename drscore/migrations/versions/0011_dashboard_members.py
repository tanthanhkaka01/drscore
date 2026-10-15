"""dashboard on the datasets of several reports: members and the parameters on the form

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-14
"""

from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    op.create_table('drs_report_bi_member',
        sa.Column('report_id', ID, nullable=False),
        sa.Column('member_report_id', ID, nullable=False),
        sa.Column('dataset_id', ID, nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.ForeignKeyConstraint(['report_id'], ['drs_report.report_id'], name=op.f('fk_drs_report_bi_member_report_id_drs_report'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['member_report_id'], ['drs_report.report_id'], name=op.f('fk_drs_report_bi_member_member_report_id_drs_report'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('report_id', 'member_report_id', name=op.f('pk_drs_report_bi_member')),
    )
    op.create_table('drs_report_bi_param',
        sa.Column('report_id', ID, nullable=False),
        sa.Column('member_report_id', ID, nullable=False),
        sa.Column('param_name', sa.String(length=50), nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.ForeignKeyConstraint(['report_id'], ['drs_report.report_id'], name=op.f('fk_drs_report_bi_param_report_id_drs_report'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['member_report_id'], ['drs_report.report_id'], name=op.f('fk_drs_report_bi_param_member_report_id_drs_report'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('report_id', 'member_report_id', 'param_name', name=op.f('pk_drs_report_bi_param')),
    )


def downgrade() -> None:
    op.drop_table('drs_report_bi_param')
    op.drop_table('drs_report_bi_member')
