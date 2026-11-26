"""public links show an HTML design too (view_key 'html')

Owner, 2026-10-22 (ORD 384): the president's page needs its filters and its currency on the page
itself, titles that follow the filters, and a phone layout. An HTML design draws that from one
snapshot; an embedded Superset dashboard cannot.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-22
"""

from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None

NAME = 'ck_drs_public_link_view_key'  # the full name: op.f() inside the operations


# PostgreSQL changes the check in place. SQLite cannot: a batch rebuilds the table, and is told to
# keep AUTOINCREMENT (see 0008: a rebuilt table without it hands out the ids of deleted rows again).

def _views(views: str) -> None:
    if op.get_bind().dialect.name == 'sqlite':
        with op.batch_alter_table('drs_public_link', recreate='always',
                                  table_kwargs={'sqlite_autoincrement': True}) as batch_op:
            batch_op.drop_constraint(op.f(NAME), type_='check')
            batch_op.create_check_constraint(op.f(NAME), f"view_key IN ({views})")
    else:
        op.drop_constraint(op.f(NAME), 'drs_public_link', type_='check')
        op.create_check_constraint(op.f(NAME), 'drs_public_link', f"view_key IN ({views})")


def upgrade() -> None:
    _views("'bi', 'grid', 'html'")


def downgrade() -> None:
    op.execute("UPDATE drs_public_link SET is_active = false, view_key = 'grid' WHERE view_key = 'html'")
    _views("'bi', 'grid'")
