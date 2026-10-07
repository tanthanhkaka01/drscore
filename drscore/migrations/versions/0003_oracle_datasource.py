"""oracle datasources: allow db_type 'oracle'

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""

from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('drs_datasource', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_drs_datasource_db_type'), type_='check')
        batch_op.create_check_constraint(
            'db_type', "db_type IN ('mssql', 'oracle', 'postgresql', 'sqlite')")


def downgrade() -> None:
    with op.batch_alter_table('drs_datasource', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_drs_datasource_db_type'), type_='check')
        batch_op.create_check_constraint('db_type', "db_type IN ('mssql', 'postgresql', 'sqlite')")
