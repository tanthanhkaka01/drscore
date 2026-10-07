"""report views: every report has a grid, and may also have an HTML design and a BI dashboard

Owner decision 2026-10-06. design_uri (one design per report) becomes html_design_uri and
bi_design_uri; grid_enabled lets an administrator hide the grid of a report. report_type keeps its
name (the SQL of spec section 8.3 still works) and now means the view the page opens on.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('drs_report', schema=None) as batch_op:
        batch_op.add_column(sa.Column('html_design_uri', sa.String(length=1000), nullable=True))
        batch_op.add_column(sa.Column('bi_design_uri', sa.String(length=1000), nullable=True))
        batch_op.add_column(sa.Column('grid_enabled', sa.Boolean(), server_default=sa.true(), nullable=False))
        batch_op.alter_column('report_type', existing_type=sa.String(length=10), server_default='GRID')
    op.execute("UPDATE drs_report SET html_design_uri = design_uri WHERE report_type = 'HTML'")
    op.execute("UPDATE drs_report SET bi_design_uri = design_uri WHERE report_type = 'BI'")
    with op.batch_alter_table('drs_report', schema=None) as batch_op:
        batch_op.drop_column('design_uri')


def downgrade() -> None:
    with op.batch_alter_table('drs_report', schema=None) as batch_op:
        batch_op.add_column(sa.Column('design_uri', sa.String(length=1000), nullable=True))
    op.execute("UPDATE drs_report SET design_uri = html_design_uri WHERE report_type = 'HTML'")
    op.execute("UPDATE drs_report SET design_uri = bi_design_uri WHERE report_type = 'BI'")
    with op.batch_alter_table('drs_report', schema=None) as batch_op:
        batch_op.alter_column('report_type', existing_type=sa.String(length=10), server_default=None)
        batch_op.drop_column('grid_enabled')
        batch_op.drop_column('bi_design_uri')
        batch_op.drop_column('html_design_uri')
