"""datasource: the full connection settings of a DBA tool

Owner decision 2026-10-06: drs_datasource keeps everything needed to connect, as Navicat or
DBeaver do: description, SQL Server instance, authentication method, SSL mode, trust of the server
certificate, connect timeout, default schema, Oracle service name or SID.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

NEW = ('description', 'instance_name', 'oracle_connect_by', 'default_schema', 'auth_method', 'ssl_mode',
       'trust_server_certificate', 'connect_timeout_seconds')


def upgrade() -> None:
    with op.batch_alter_table('drs_datasource', schema=None) as batch_op:
        batch_op.add_column(sa.Column('description', sa.String(length=1000), nullable=True))
        batch_op.add_column(sa.Column('instance_name', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('oracle_connect_by', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('default_schema', sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column('auth_method', sa.String(length=10), server_default='password', nullable=False))
        batch_op.add_column(sa.Column('ssl_mode', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('trust_server_certificate', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('connect_timeout_seconds', sa.Integer(), nullable=True))
        batch_op.create_check_constraint('auth_method', "auth_method IN ('password', 'windows', 'none')")
        batch_op.create_check_constraint('ssl_mode', "ssl_mode IS NULL OR ssl_mode IN ('disable', 'prefer', 'require', 'verify')")
        batch_op.create_check_constraint('oracle_connect_by', "oracle_connect_by IS NULL OR oracle_connect_by IN ('service_name', 'sid')")
    # A source without a password (SQLite) was 'password' by default: mark it 'none'.
    op.execute("UPDATE drs_datasource SET auth_method = 'none' WHERE db_type = 'sqlite'")


def downgrade() -> None:
    with op.batch_alter_table('drs_datasource', schema=None) as batch_op:
        for name in ('oracle_connect_by', 'ssl_mode', 'auth_method'):
            batch_op.drop_constraint(op.f(f'ck_drs_datasource_{name}'), type_='check')
        for column in reversed(NEW):
            batch_op.drop_column(column)
