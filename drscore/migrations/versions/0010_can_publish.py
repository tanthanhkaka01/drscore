"""can_publish on grant_group: self-service report publishing

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-10
"""

from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('drs_grant_group', sa.Column('can_publish', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column('drs_grant_group', 'can_publish')
