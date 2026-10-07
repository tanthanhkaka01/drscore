"""``db upgrade`` and ``db check``: one Alembic history for PostgreSQL and SQLite."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select, text
from sqlalchemy.schema import CreateSchema

from drs.db.engine import Database
from drs.db.models import Base

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
VERSION_TABLE = "drs_alembic_version"


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


# Taken during an upgrade on PostgreSQL, so two servers starting at once do not migrate together.
MIGRATION_LOCK_KEY = 0x445253  # "DRS"


def upgrade(database: Database, revision: str = "head") -> None:
    if database.dialect != "postgresql":
        _upgrade(database, revision)
        return
    with database.engine.connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
        lock.commit()
        try:
            schema = database.settings.postgresql.schema
            with database.engine.begin() as conn:
                exists = conn.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :s"), {"s": schema}).first()
                if not exists:  # CREATE needs the CREATE right on the database; skip it when a DBA made the schema
                    conn.execute(CreateSchema(schema))
            _upgrade(database, revision)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
            lock.commit()


def _upgrade(database: Database, revision: str) -> None:
    cfg = alembic_config()
    with database.engine.connect() as conn:
        sqlite = database.dialect == "sqlite"
        if sqlite:
            # Batch migrations rebuild a table on SQLite (copy, drop, rename). With foreign keys on,
            # dropping a table that other rows reference fails, so they are off during the upgrade
            # (the pragma only works outside a transaction) and checked afterwards.
            conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
            conn.commit()
        try:
            with conn.begin():
                cfg.attributes["connection"] = conn
                command.upgrade(cfg, revision)
            if sqlite:
                broken = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                conn.commit()
                if broken:
                    raise RuntimeError(f"foreign key check failed after the upgrade: {broken[:5]}")
        finally:
            if sqlite:
                conn.rollback()
                conn.exec_driver_sql("PRAGMA foreign_keys = ON")
                conn.commit()


def current_revision(database: Database) -> str | None:
    with database.engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"version_table": VERSION_TABLE})
        return ctx.get_current_revision()


@dataclass
class CheckResult:
    engine: str
    target: str
    revision: str | None
    head: str
    tables: dict[str, str]  # table -> "ok" or the error

    @property
    def ok(self) -> bool:
        return self.revision == self.head and all(v == "ok" for v in self.tables.values())


def check(database: Database) -> CheckResult:
    """Active engine, migration version, and that every table can be read."""
    tables: dict[str, str] = {}
    with database.engine.connect() as conn:
        existing = set(inspect(conn).get_table_names())
        for name, table in sorted(Base.metadata.tables.items()):
            if name not in existing:
                tables[name] = "missing"
                continue
            try:
                conn.execute(select(text("1")).select_from(table).limit(1)).all()
                tables[name] = "ok"
            except Exception as exc:  # reported, not raised: db check lists every table
                tables[name] = f"error: {exc.__class__.__name__}"
    return CheckResult(
        engine=database.dialect,
        target=database.settings.describe(),
        revision=current_revision(database),
        head=head_revision(),
        tables=tables,
    )
