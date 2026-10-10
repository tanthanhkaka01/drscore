"""BI datasets: the data a Superset dashboard reads (spec section 14.3).

A dataset is the report's own result, not a copy of one run of it. Every run of a report is kept as
a snapshot - the JSON of its rows, for the parameter values it was made with - and that is what the
grid and the HTML design show. The dataset is that same JSON, read as rows and columns:

* **PostgreSQL**: a view ``<bi_schema>.<name>`` over ``drs_report_snapshot`` - SQL that unpacks
  ``rows_json`` into typed columns. Nothing is copied and nothing is loaded: a snapshot is in the
  dataset for as long as it is in the cache.
* **SQLite**: a BI tool is never given the DRS database file, so there the snapshots that are asked
  for are written to a table of the datasets file (``[sqlite] bi_path``) and leave it with their
  snapshot.

Both have the column ``drs_snapshot_id``. A dashboard is shown for **one** snapshot - the one on
screen, the result for the parameters the user chose: the guest token DRS issues carries Superset's
row-level rule ``drs_snapshot_id = <that snapshot>`` (``rls_clause``). Two users who chose different
parameters read two different results at the same moment, through the same dataset.
"""

from __future__ import annotations

import hashlib
import json
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
    select,
    text,
)
from sqlalchemy.schema import CreateSchema

from drscore.db.engine import Database
from drscore.db.models import BiDataset, ReportSnapshot
from drscore.db.types import utcnow
from drscore.errors import DRSError
from drscore.reports import cache

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,50}$")
SNAPSHOT_COLUMN = "drs_snapshot_id"
LOCK_KEY = "bi-dataset"
LOCK_WAIT_SECONDS = 60
CHUNK = 1000

SQL_TYPES = {
    "text": Text, "int": BigInteger, "decimal": Numeric, "float": lambda: Float(precision=53),
    "bool": Boolean, "date": Date, "datetime": DateTime, "time": Time,
}
# How a JSON value (read as text) becomes a column of the PostgreSQL view.
PG_CASTS = {"int": "::bigint", "decimal": "::numeric", "float": "::double precision", "bool": "::boolean",
            "datetime": "::timestamp", "time": "::time"}


def physical_name(database: Database, name: str) -> tuple[str | None, str]:
    """(schema, relation) of a dataset as ``database.bi_engine`` sees it. ``name`` must pass NAME_RE."""
    if not NAME_RE.match(name or ""):
        raise DRSError("REPORT_MISCONFIGURED",
                       admin_detail=f"bi_dataset_table {name!r} does not match ^[a-z][a-z0-9_]{{0,50}}$")
    if database.dialect == "postgresql":
        return database.settings.postgresql.bi_schema, name
    return None, name


def display_name(database: Database, name: str) -> str:
    schema, table = physical_name(database, name)
    return f"{schema}.{table}" if schema else table


def rls_clause(snapshot_id: int) -> str:
    """The row-level rule of a guest token: the reader of a dashboard sees one snapshot."""
    return f"{SNAPSHOT_COLUMN} = {int(snapshot_id)}"


def _checked(columns: Sequence[dict[str, str]]) -> None:
    seen: set[str] = set()
    for c in columns:
        key = c["name"].casefold()
        if key == SNAPSHOT_COLUMN:
            raise DRSError("REPORT_MISCONFIGURED", admin_detail=(
                f"the result has a column {c['name']!r}: that name is the dataset's own (the snapshot of a row)"))
        if key in seen:
            raise DRSError("REPORT_MISCONFIGURED", admin_detail=f"the result has the column {c['name']!r} twice")
        seen.add(key)


# --------------------------------------------------------------------------------------------
# PostgreSQL: a view over the snapshots

def _view_sql(database: Database, schema: str, name: str, report_id: int, columns: Sequence[dict[str, str]]) -> str:
    quote = database.engine.dialect.identifier_preparer.quote
    exprs = [f"s.snapshot_id AS {SNAPSHOT_COLUMN}"]
    for i, c in enumerate(columns):
        value = f"(r.value ->> {i})"
        expr = f"left({value}, 10)::date" if c["type"] == "date" else value + PG_CASTS.get(c["type"], "")
        exprs.append(f"{expr} AS {quote(c['name'])}")
    # A snapshot made before the report's query changed has other columns: it is not of this view.
    shape = json.dumps([{"name": c["name"], "type": c["type"]} for c in columns], ensure_ascii=False,
                       separators=(",", ":")).replace("'", "''")
    return (f"CREATE VIEW {quote(schema)}.{quote(name)} AS\nSELECT " + ",\n       ".join(exprs)
            + f"\nFROM {quote(database.settings.postgresql.schema)}.drs_report_snapshot s"
              "\nCROSS JOIN LATERAL jsonb_array_elements(s.rows_json) AS r(value)"
            + f"\nWHERE s.report_id = {int(report_id)} AND s.columns_json = '{shape}'::jsonb")


def _ensure_view(database: Database, schema: str, name: str, report_id: int,
                 columns: Sequence[dict[str, str]]) -> bool:
    """Creates the view, or replaces it when the report's columns changed. True when it did."""
    quote = database.engine.dialect.identifier_preparer.quote
    sql = _view_sql(database, schema, name, report_id, columns)
    marker = "drscore dataset " + hashlib.sha256(sql.encode("utf-8")).hexdigest()[:32]
    relation = f"{quote(schema)}.{quote(name)}"
    with database.engine.begin() as conn:
        if not conn.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :s"), {"s": schema}).first():
            conn.execute(CreateSchema(schema))
        found = conn.execute(text(
            "SELECT c.relkind::text, obj_description(c.oid, 'pg_class') FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :s AND c.relname = :t"),
            {"s": schema, "t": name}).first()
        if found and found[0] == "v" and found[1] == marker:
            return False
        if found:  # another shape - or the table that versions before 1.1 copied the rows into
            kind = {"v": "VIEW", "m": "MATERIALIZED VIEW"}.get(found[0], "TABLE")
            conn.execute(text(f"DROP {kind} {relation}"))
        conn.execute(text(f"DROP TABLE IF EXISTS {quote(schema)}.{quote(name + '__new')}"))
        # Through the driver's own cursor, without parameters: a column name of the report may hold
        # "%" or ":" and neither may be taken for a placeholder.
        with conn.connection.cursor() as cursor:
            cursor.execute(sql)
        conn.execute(text(f"COMMENT ON VIEW {relation} IS '{marker}'"))
    log.info("dataset %s: view over the snapshots of report %s (%d columns)", display_name(database, name),
             report_id, len(columns))
    return True


# --------------------------------------------------------------------------------------------
# SQLite: the snapshots asked for, in the datasets file

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


def _table(name: str, columns: Sequence[dict[str, str]]) -> Table:
    cols = [Column(SNAPSHOT_COLUMN, BigInteger, nullable=False, index=True)]
    cols += [Column(c["name"], SQL_TYPES.get(c["type"], Text)(), quote=True) for c in columns]
    return Table(name, MetaData(), *cols, quote=True)


def _load_rows(database: Database, name: str, outcome, live: set[int]) -> bool:
    """Writes this snapshot's rows unless they are there, and removes the rows of snapshots that
    left the cache. True when rows were written."""
    engine = database.bi_engine
    table = _table(name, outcome.columns)
    quoted = engine.dialect.identifier_preparer.quote(name)
    expected = [SNAPSHOT_COLUMN] + [c["name"] for c in outcome.columns]
    types = [c["type"] for c in outcome.columns]
    names = [c["name"] for c in outcome.columns]
    with engine.begin() as conn:
        existing = [row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({quoted})")]
        if existing != expected:  # no table yet, or the report's columns changed
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {quoted}")
            table.create(conn)
        column = table.c[SNAPSHOT_COLUMN]
        loaded = conn.execute(select(column).where(column == outcome.snapshot_id).limit(1)).first() is not None
        if not loaded:
            rows = outcome.rows
            for start in range(0, len(rows), CHUNK):
                conn.execute(table.insert(), [
                    {SNAPSHOT_COLUMN: outcome.snapshot_id, **{n: _convert(v, t) for n, v, t in zip(names, row, types)}}
                    for row in rows[start:start + CHUNK]])
        conn.execute(table.delete().where(column.not_in(sorted(live | {outcome.snapshot_id}))))
    if not loaded:
        log.info("dataset %s: %d rows of snapshot %s", name, len(outcome.rows), outcome.snapshot_id)
    return not loaded


# --------------------------------------------------------------------------------------------

def refresh(database: Database, report_id: int, name: str, outcome) -> bool:
    """Makes ``outcome`` (a snapshot: columns, rows, snapshot_id) readable through the dataset.
    PostgreSQL: the view exists and has the report's columns. SQLite: the snapshot's rows are in
    the datasets file. Returns True when something had to be done."""
    schema, relation = physical_name(database, name)
    _checked(outcome.columns)
    owner = cache.lock_owner()
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while not cache.try_lock(database, report_id, LOCK_KEY, owner, stale_seconds=900):
        if time.monotonic() > deadline:
            raise DRSError("BUSY", admin_detail=f"dataset {name}: another process has been preparing it for "
                                                f"{LOCK_WAIT_SECONDS} seconds")
        time.sleep(0.2)
    try:
        if schema is not None:
            changed = _ensure_view(database, schema, relation, report_id, outcome.columns)
        else:
            with database.session() as s:
                live = set(s.scalars(select(ReportSnapshot.snapshot_id).where(ReportSnapshot.report_id == report_id)))
            changed = _load_rows(database, relation, outcome, live)
        with database.session() as s:
            row = s.get(BiDataset, report_id) or BiDataset(report_id=report_id)
            if changed or row.snapshot_id != outcome.snapshot_id:
                row.table_name = display_name(database, name)
                row.snapshot_id = outcome.snapshot_id
                row.row_count = len(outcome.rows)
                row.loaded_at = utcnow()
                s.add(row)
    finally:
        cache.release_lock(database, report_id, LOCK_KEY, owner)
    if changed:  # a new or replaced view: Superset's dataset and its access follow (self-service)
        from drscore.bi import selfservice
        from drscore.settings import get_settings

        if selfservice.is_on(get_settings().app):
            selfservice.schedule_sync(report_id)
    return changed
