"""decimal sort_order for reports and parameters; is_active on parameters; fuller access log

Owner decisions 2026-10-06: the display order of reports and parameters is a decimal (an item can
be put between two others); a parameter can be switched off without deleting it; every view of a
report is logged in full (page opened, view, browser, request id).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ('drs_report', 'drs_report_param'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column('sort_order', existing_type=sa.Integer(), type_=sa.Numeric(10, 2),
                                  existing_nullable=False, existing_server_default=sa.text('0'),
                                  postgresql_using='sort_order::numeric(10,2)')
    with op.batch_alter_table('drs_report_param', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False))
    with op.batch_alter_table('drs_access_log', schema=None) as batch_op:
        batch_op.add_column(sa.Column('view_key', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('user_agent', sa.String(length=400), nullable=True))
        batch_op.add_column(sa.Column('request_id', sa.String(length=32), nullable=True))
        batch_op.drop_constraint(op.f('ck_drs_access_log_action'), type_='check')
        batch_op.create_check_constraint('action', "action IN ('LOGIN', 'LOGOUT', 'OPEN', 'VIEW', 'RUN', "
                                                   "'EXPORT_XLSX', 'EXPORT_CSV', 'EXPORT_TXT', 'BI_TOKEN')")


def downgrade() -> None:
    op.execute("DELETE FROM drs_access_log WHERE action = 'OPEN'")
    with op.batch_alter_table('drs_access_log', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_drs_access_log_action'), type_='check')
        batch_op.create_check_constraint('action', "action IN ('LOGIN', 'LOGOUT', 'VIEW', 'RUN', "
                                                   "'EXPORT_XLSX', 'EXPORT_CSV', 'EXPORT_TXT', 'BI_TOKEN')")
        batch_op.drop_column('request_id')
        batch_op.drop_column('user_agent')
        batch_op.drop_column('view_key')
    with op.batch_alter_table('drs_report_param', schema=None) as batch_op:
        batch_op.drop_column('is_active')
    for table in ('drs_report', 'drs_report_param'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column('sort_order', existing_type=sa.Numeric(10, 2), type_=sa.Integer(),
                                  existing_nullable=False, existing_server_default=sa.text('0'),
                                  postgresql_using='round(sort_order)::integer')
