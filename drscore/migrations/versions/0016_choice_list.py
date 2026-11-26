"""choice lists: choices ticked on a page and copied for the reports; a public link may change them

Owner, 2026-10-24 (ORD 395): which kinds of IT equipment count as IT spend is decided by the IT
manager, who ticks them on a page opened by a public link, and the report joins the ticked list -
not a rule written into the report's SQL, nor a field set asset by asset in the source system.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-24
"""

from alembic import op
import sqlalchemy as sa

import drscore.db.types

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None

ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')
NOW = sa.text('(CURRENT_TIMESTAMP)')
FK = 'fk_drs_public_link_choice_list_id_drs_choice_list'


def upgrade() -> None:
    op.create_table('drs_choice_list',
    sa.Column('list_id', ID, autoincrement=True, nullable=False),
    sa.Column('list_code', sa.String(length=100), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('target_table', sa.String(length=63), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('changed_by', sa.String(length=100), nullable=True),
    sa.Column('changed_at', drscore.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', drscore.db.types.UTCDateTime(timezone=True), server_default=NOW, nullable=False),
    sa.Column('updated_at', drscore.db.types.UTCDateTime(timezone=True), server_default=NOW, nullable=False),
    sa.PrimaryKeyConstraint('list_id', name=op.f('pk_drs_choice_list')),
    sa.UniqueConstraint('list_code', name=op.f('uq_drs_choice_list_list_code')),
    sa.UniqueConstraint('target_table', name=op.f('uq_drs_choice_list_target_table')),
    sqlite_autoincrement=True
    )
    op.create_table('drs_choice_item',
    sa.Column('item_id', ID, autoincrement=True, nullable=False),
    sa.Column('list_id', ID, nullable=False),
    sa.Column('item_key', sa.String(length=100), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('group_name', sa.String(length=100), nullable=True),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('is_checked', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('changed_by', sa.String(length=100), nullable=True),
    sa.Column('changed_at', drscore.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['list_id'], ['drs_choice_list.list_id'],
                            name=op.f('fk_drs_choice_item_list_id_drs_choice_list'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('item_id', name=op.f('pk_drs_choice_item')),
    sa.UniqueConstraint('list_id', 'item_key', name=op.f('uq_drs_choice_item_list_id_item_key')),
    sqlite_autoincrement=True
    )
    # SQLite adds a column with a foreign key only by rebuilding the table (kept AUTOINCREMENT, see 0015)
    if op.get_bind().dialect.name == 'sqlite':
        with op.batch_alter_table('drs_public_link', recreate='always',
                                  table_kwargs={'sqlite_autoincrement': True}) as batch_op:
            batch_op.add_column(sa.Column('choice_list_id', ID, nullable=True))
            batch_op.create_foreign_key(op.f(FK), 'drs_choice_list', ['choice_list_id'], ['list_id'])
    else:
        op.add_column('drs_public_link', sa.Column('choice_list_id', ID, nullable=True))
        op.create_foreign_key(op.f(FK), 'drs_public_link', 'drs_choice_list', ['choice_list_id'], ['list_id'])


def downgrade() -> None:
    if op.get_bind().dialect.name == 'sqlite':
        with op.batch_alter_table('drs_public_link', recreate='always',
                                  table_kwargs={'sqlite_autoincrement': True}) as batch_op:
            batch_op.drop_constraint(op.f(FK), type_='foreignkey')
            batch_op.drop_column('choice_list_id')
    else:
        op.drop_constraint(op.f(FK), 'drs_public_link', type_='foreignkey')
        op.drop_column('drs_public_link', 'choice_list_id')
    op.drop_table('drs_choice_item')
    op.drop_table('drs_choice_list')
