"""report designer: grants, drafts and approval

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

import drscore.db.types

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('drs_grant_group', sa.Column('can_design', sa.Boolean(), server_default=sa.false(), nullable=False))

    op.create_table('drs_grant_datasource',
        sa.Column('grant_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('datasource_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('user_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
        sa.Column('role_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
        sa.Column('granted_by', sa.String(length=100), nullable=True),
        sa.Column('granted_at', drscore.db.types.UTCDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.CheckConstraint('(user_id IS NULL) <> (role_id IS NULL)', name=op.f('ck_drs_grant_datasource_one_principal')),
        sa.ForeignKeyConstraint(['datasource_id'], ['drs_datasource.datasource_id'], name=op.f('fk_drs_grant_datasource_datasource_id_drs_datasource'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['drs_role.role_id'], name=op.f('fk_drs_grant_datasource_role_id_drs_role'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['drs_user.user_id'], name=op.f('fk_drs_grant_datasource_user_id_drs_user'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('grant_id', name=op.f('pk_drs_grant_datasource')),
        sqlite_autoincrement=True
    )
    op.create_index('uq_drs_grant_datasource_role', 'drs_grant_datasource', ['datasource_id', 'role_id'], unique=True, postgresql_where=sa.text('role_id IS NOT NULL'), sqlite_where=sa.text('role_id IS NOT NULL'))
    op.create_index('uq_drs_grant_datasource_user', 'drs_grant_datasource', ['datasource_id', 'user_id'], unique=True, postgresql_where=sa.text('user_id IS NOT NULL'), sqlite_where=sa.text('user_id IS NOT NULL'))

    op.create_table('drs_report_draft',
        sa.Column('draft_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('report_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
        sa.Column('report_code', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=10), server_default=sa.text("'DRAFT'"), nullable=False),
        sa.Column('content_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('tested_hash', sa.String(length=64), nullable=True),
        sa.Column('test_summary', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
        sa.Column('author', sa.String(length=100), nullable=False),
        sa.Column('submitted_at', drscore.db.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column('reviewed_by', sa.String(length=100), nullable=True),
        sa.Column('reviewed_at', drscore.db.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column('review_note', sa.String(length=2000), nullable=True),
        sa.Column('created_at', drscore.db.types.UTCDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', drscore.db.types.UTCDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT', 'PENDING', 'APPROVED', 'REJECTED')", name=op.f('ck_drs_report_draft_status')),
        sa.ForeignKeyConstraint(['report_id'], ['drs_report.report_id'], name=op.f('fk_drs_report_draft_report_id_drs_report'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('draft_id', name=op.f('pk_drs_report_draft')),
        sqlite_autoincrement=True
    )
    op.create_index(op.f('ix_drs_report_draft_author'), 'drs_report_draft', ['author'], unique=False)
    op.create_index(op.f('ix_drs_report_draft_status'), 'drs_report_draft', ['status'], unique=False)
    op.create_index('uq_drs_report_draft_open_code', 'drs_report_draft', ['report_code'], unique=True, postgresql_where=sa.text("status IN ('DRAFT', 'PENDING')"), sqlite_where=sa.text("status IN ('DRAFT', 'PENDING')"))


def downgrade() -> None:
    op.drop_index('uq_drs_report_draft_open_code', table_name='drs_report_draft', postgresql_where=sa.text("status IN ('DRAFT', 'PENDING')"), sqlite_where=sa.text("status IN ('DRAFT', 'PENDING')"))
    op.drop_index(op.f('ix_drs_report_draft_status'), table_name='drs_report_draft')
    op.drop_index(op.f('ix_drs_report_draft_author'), table_name='drs_report_draft')
    op.drop_table('drs_report_draft')

    op.drop_index('uq_drs_grant_datasource_user', table_name='drs_grant_datasource', postgresql_where=sa.text('user_id IS NOT NULL'), sqlite_where=sa.text('user_id IS NOT NULL'))
    op.drop_index('uq_drs_grant_datasource_role', table_name='drs_grant_datasource', postgresql_where=sa.text('role_id IS NOT NULL'), sqlite_where=sa.text('role_id IS NOT NULL'))
    op.drop_table('drs_grant_datasource')

    op.drop_column('drs_grant_group', 'can_design')
