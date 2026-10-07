"""BI dataset tables: the data a Superset dashboard reads (spec section 14.3).

The result of a BI report (its snapshot) is written to a real table, where a BI tool reads it and
nothing else of DRS: ``<bi_schema>.<bi_dataset_table>`` on PostgreSQL (default schema ``drs_bi``,
the only schema Superset's login may read), table ``<bi_dataset_table>`` of the datasets file on
SQLite (``[sqlite] bi_path``, a file of its own beside the DRS database). The table is replaced
without ever being empty: the rows go into ``<name>__new``, then the old table is dropped and the
new one renamed, in one transaction. It is refreshed when the report's snapshot changes (retention), when
the dashboard tab is opened or by ``cache warm``.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    MetaData,
    Numeric,
    Table,
    Text,
    Time,
    text,
)
from sqlalchemy.schema import CreateSchema

from drscore.db.engine import Database
from drscore.db.models import BiDataset
from drscore.db.types import utcnow
from drscore.errors import DRSError
from drscore.reports import cache

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,50}$")
LOCK_KEY = "bi-dataset"
LOCK_WAIT_SECONDS = 60
CHUNK = 1000

SQL_TYPES = {
    "text": Text, "int": BigInteger, "decimal": Numeric, "float": lambda: Float(precision=53),
    "bool": Boolean, "date": Date, "datetime": DateTime, "time": Time,
}


def physical_name(database: Database, name: str) -> tuple[str | None, str]:
    """(schema, table) of a dataset where ``database.bi_engine`` writes. ``name`` must pass NAME_RE."""
    if not NAME_RE.match(name or ""):
        raise DRSError("REPORT_MISCONFIGURED",
                       admin_detail=f"bi_dataset_table {name!r} does not match ^[a-z][a-z0-9_]{{0,50}}$")
    if database.dialect == "postgresql":
        return database.settings.postgresql.bi_schema, name
    return None, name


def display_name(database: Database, name: str) -> str:
    schema, table = physical_name(database, name)
    return f"{schema}.{table}" if schema else table


def _convert(value: Any, column_type: str) -> Any:
    """A snapshot JSON value back to a typed value for the dataset column."""
    if value is None:
        return None
    try:
        if column_type == "int":
            return int(value)
        if column_type == "decimal":
            return Decimal(str(value))
        if column_type == "float":
            return float(value)
        if column_type == "bool":
            return bool(value)
        if column_type == "date":
            return date.fromisoformat(str(value)[:10])
        if column_type == "datetime":
            return datetime.fromisoformat(str(value))
        if column_type == "time":
            return dtime.fromisoformat(str(value))
    except (ValueError, ArithmeticError):
        return None  # a value that does not fit its column type is left empty
    return value if isinstance(value, str) else str(value)


def _table(schema: str | None, name: str, columns: Sequence[dict[str, str]]) -> Table:
    seen: set[str] = set()
    cols = []
    for c in columns:
        key = c["name"].casefold()
        if key in seen:
            raise DRSError("REPORT_MISCONFIGURED", admin_detail=f"the result has the column {c['name']!r} twice")
        seen.add(key)
        cols.append(Column(c["name"], SQL_TYPES.get(c["type"], Text)(), quote=True))
    return Table(name, MetaData(schema=schema), *cols, quote=True)


def loaded_snapshot(database: Database, report_id: int) -> int | None:
    with database.session() as s:
        row = s.get(BiDataset, report_id)
        return row.snapshot_id if row else None


def refresh(database: Database, report_id: int, name: str, outcome) -> bool:
    """Loads ``outcome`` (a snapshot: columns, rows, snapshot_id) into the dataset table, unless
    that snapshot is already loaded. Returns True when the table was replaced."""
    schema, table_name = physical_name(database, name)
    if loaded_snapshot(database, report_id) == outcome.snapshot_id:
        return False
    owner = cache.lock_owner()
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while not cache.try_lock(database, report_id, LOCK_KEY, owner, stale_seconds=900):
        if time.monotonic() > deadline:
            log.warning("dataset %s: another process is loading it, skipped", name)
            return False
        time.sleep(0.2)
    try:
        if loaded_snapshot(database, report_id) == outcome.snapshot_id:  # loaded meanwhile
            return False
        new = _table(schema, f"{table_name}__new", outcome.columns)
        types = [c["type"] for c in outcome.columns]
        preparer = database.bi_engine.dialect.identifier_preparer
        qualified = (preparer.quote_schema(schema) + "." if schema else "")
        with database.bi_engine.begin() as conn:
            if schema and not conn.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :s"),
                                           {"s": schema}).first():
                conn.execute(CreateSchema(schema))
            conn.execute(text(f"DROP TABLE IF EXISTS {qualified}{preparer.quote(table_name + '__new')}"))
            new.create(conn)
            rows = outcome.rows
            names = [c["name"] for c in outcome.columns]
            for start in range(0, len(rows), CHUNK):
                batch = [{n: _convert(v, t) for n, v, t in zip(names, row, types)}
                         for row in rows[start:start + CHUNK]]
                conn.execute(new.insert(), batch)
            # The swap: readers see the old table until this transaction commits.
            conn.execute(text(f"DROP TABLE IF EXISTS {qualified}{preparer.quote(table_name)}"))
            conn.execute(text(f"ALTER TABLE {qualified}{preparer.quote(table_name + '__new')} "
                              f"RENAME TO {preparer.quote(table_name)}"))
        with database.session() as s:
            row = s.get(BiDataset, report_id) or BiDataset(report_id=report_id)
            row.table_name = display_name(database, name)
            row.snapshot_id = outcome.snapshot_id
            row.row_count = len(outcome.rows)
            row.loaded_at = utcnow()
            s.add(row)
        log.info("dataset %s loaded: %d rows from snapshot %s", display_name(database, name), len(outcome.rows),
                 outcome.snapshot_id)
        return True
    finally:
        cache.release_lock(database, report_id, LOCK_KEY, owner)
