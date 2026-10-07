"""Alembic environment. Run through ``python -m drs db upgrade`` (see ``drs.db.migrate``)."""

from alembic import context

from drs.db.models import Base

config = context.config
connection = config.attributes["connection"]

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    version_table="drs_alembic_version",
    # Batch mode rebuilds the table on SQLite, where ALTER TABLE is limited.
    render_as_batch=True,
    compare_type=True,
    compare_server_default=True,
)

with context.begin_transaction():
    context.run_migrations()
