"""Runs one mirror: reads the source table, writes the copy (see drscore.mirror).

The source is read like a report reads it (``drscore.reports.executor``): one read-only
transaction, rolled back at the end, the connection given back in autocommit. The target is
written in one transaction, so a reader of the copy sees the old rows until the new ones are all
there:

* PostgreSQL: every batch is sent with COPY to a temporary table, then merged - a full copy deletes
  the mirror's own rows and inserts the new ones, an incremental one inserts or updates by key;
* SQLite: the rows are inserted (or upserted by key) in batches.

A row read twice (an incremental run's look-back, or a read without locks on SQL Server) is
written once: the merge keeps one row per key.
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from typing import Any, Iterator, Sequence

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
    Time,
    bindparam,
    delete,
    inspect,
    literal_column,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.schema import CreateSchema
from sqlalchemy.types import TypeEngine

from drscore.datasources.connectors import SourceInfo, engines, set_autocommit
from drscore.db.engine import Database
from drscore.db.models import Datasource, MirrorTable
from drscore.db.types import utcnow
from drscore.errors import DRSError
from drscore.mirror.spec import SOURCE_COLUMN, SYNCED_COLUMN, MirrorError, parse_hours
from drscore.settings import AppSettings

log = logging.getLogger(__name__)

ROLE_RE_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")
ANALYZE_ROWS = 10000  # an incremental run that wrote this many rows refreshes the planner statistics
HEARTBEAT_SECONDS = 60  # a running copy renews its claim this often


@dataclass
class Job:
    """A mirror's definition, detached from the DRS session that loaded it."""

    mirror_id: int
    code: str
    source: SourceInfo
    source_object: str
    columns: list[str] | None
    where_sql: str | None
    target: str
    key: list[str]
    mode: str
    watermark_column: str | None
    watermark_value: str | None
    lookback_seconds: int
    full_refresh_hours: int | None
    full_hours: str | None
    last_full_at: datetime | None
    read_uncommitted: bool


@dataclass
class Outcome:
    code: str
    full: bool
    rows: int
    duration_ms: int
    watermark: str | None


# --------------------------------------------------------------------------------------------
# Claiming a run: one server at a time per mirror

def owner_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"[:100]


def claim(database: Database, mirror_id: int, owner: str, lock_minutes: int) -> bool:
    """Marks the mirror as running for ``owner``. False when another run holds it."""
    now = utcnow()
    with database.session() as s:
        done = s.execute(
            update(MirrorTable)
            .where(MirrorTable.mirror_id == mirror_id,
                   or_(MirrorTable.lock_until.is_(None), MirrorTable.lock_until < now))
            .values(lock_owner=owner, lock_until=now + timedelta(minutes=lock_minutes),
                    last_status="running", last_started_at=now)
            .execution_options(synchronize_session=False))
        return done.rowcount == 1


def renew(database: Database, mirror_id: int, owner: str, lock_minutes: int) -> None:
    with database.session() as s:
        s.execute(update(MirrorTable).where(MirrorTable.mirror_id == mirror_id, MirrorTable.lock_owner == owner)
                  .values(lock_until=utcnow() + timedelta(minutes=lock_minutes))
                  .execution_options(synchronize_session=False))


def _finish(database: Database, mirror_id: int, owner: str, **values: Any) -> None:
    with database.session() as s:
        s.execute(update(MirrorTable)
                  .where(MirrorTable.mirror_id == mirror_id, MirrorTable.lock_owner == owner)
                  .values(lock_owner=None, lock_until=None, last_finished_at=utcnow(), **values)
                  .execution_options(synchronize_session=False))


# --------------------------------------------------------------------------------------------
# Types

def target_type(source_type: TypeEngine) -> TypeEngine:
    """The column type of the copy for a reflected source column: numbers stay numbers, times stay
    times, everything else is text."""
    try:
        py = source_type.python_type
    except (NotImplementedError, AttributeError):
        return Text()
    if py is bool:
        return Boolean(create_constraint=False)
    if py is int:
        return BigInteger() if isinstance(source_type, BigInteger) else Integer()
    if py is float:
        return Float(precision=53)
    if py is Decimal:
        precision, scale = getattr(source_type, "precision", None), getattr(source_type, "scale", None)
        if precision and 0 < precision <= 1000 and (scale is None or 0 <= scale <= precision):
            return Numeric(precision, scale or 0)
        return Numeric()
    if py is datetime:
        return DateTime()
    if py is date:
        return Date()
    if py is dtime:
        return Time()
    if py is bytes:
        return LargeBinary()
    return Text()


def _as_text(value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value.replace("\x00", "") if value else value  # PostgreSQL text holds no NUL
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


# --------------------------------------------------------------------------------------------
# The source

def _split(name: str) -> tuple[str | None, str]:
    schema, _, table = name.rpartition(".")
    return (schema or None), table


def source_columns(job: Job) -> list[tuple[str, TypeEngine]]:
    """(source name, type) of the columns to copy, in the source's spelling and order."""
    engine = engines.get(job.source)
    schema, table = _split(job.source_object)
    try:
        found = inspect(engine).get_columns(table, schema=schema)
    except SQLAlchemyError as exc:
        raise DRSError("DATASOURCE_UNAVAILABLE",
                       admin_detail=f"mirror {job.code}: cannot read the columns of {job.source_object}: "
                                    f"{_short(exc)}") from exc
    if not found:
        raise MirrorError(f"mirror {job.code}: {job.source_object} has no columns, or does not exist "
                          f"on {job.source.code}")
    by_name = {c["name"].lower(): (c["name"], c["type"]) for c in found}
    wanted = job.columns or [c["name"] for c in found]
    needed = wanted + job.key + ([job.watermark_column] if job.watermark_column else [])
    missing = [c for c in dict.fromkeys(needed) if c.lower() not in by_name]
    if missing:
        raise MirrorError(f"mirror {job.code}: {job.source_object} has no column {', '.join(missing)}")
    return [by_name[c.lower()] for c in wanted]


def _source_sql(job: Job, engine, columns: Sequence[str], incremental: bool, inclusive: bool = False) -> str:
    quote = engine.dialect.identifier_preparer.quote
    schema, table = _split(job.source_object)
    relation = (f"{quote(schema)}." if schema else "") + quote(table)
    sql = f"SELECT {', '.join(quote(c) for c in columns)} FROM {relation}"
    conditions = []
    if job.where_sql:  # an administrator's filter: its colons are not placeholders
        conditions.append("(" + job.where_sql.replace(":", r"\:") + ")")
    if incremental:
        column = next(c for c in columns if c.lower() == job.watermark_column.lower())
        conditions.append(f"{quote(column)} {'>=' if inclusive else '>'} :wm")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    return sql


def _short(exc: Exception) -> str:
    orig = getattr(exc, "orig", None)
    return str(orig if orig is not None else exc).strip().splitlines()[0][:500]


def read_source(job: Job, columns: Sequence[str], wm: Any, batch_rows: int,
                timeout_seconds: int) -> Iterator[list[tuple]]:
    """Yields the source rows in batches, inside one read-only transaction that is rolled back. A
    date watermark (no time of day) reads its last day again: rows of that day may still come."""
    from drscore.reports.executor import _back_to_autocommit

    engine = engines.get(job.source)
    dialect = engine.dialect.name
    inclusive = isinstance(wm, date) and not isinstance(wm, datetime)
    sql = _source_sql(job, engine, columns, wm is not None, inclusive)
    stmt = text(sql).bindparams(bindparam("wm", wm)) if wm is not None else text(sql)
    try:
        conn = engine.connect()
    except SQLAlchemyError as exc:
        raise DRSError("DATASOURCE_UNAVAILABLE", admin_detail=f"datasource {job.source.code}: {_short(exc)}") from exc
    with conn:
        transaction = conn.begin()
        raw = conn.connection.dbapi_connection
        result = None
        try:
            if dialect in ("postgresql", "oracle"):
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            if dialect == "postgresql":
                conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_seconds * 1000)}")
            elif dialect == "oracle":
                raw.call_timeout = int(timeout_seconds * 1000)
            elif dialect == "mssql":
                set_autocommit(raw, False)
                if hasattr(raw, "timeout"):
                    raw.timeout = int(timeout_seconds)
                if job.read_uncommitted:
                    conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
            options = {"stream_results": True} if dialect in ("postgresql", "oracle") else {}
            result = conn.execution_options(**options).execute(stmt)
            while True:
                batch = result.fetchmany(batch_rows)
                if not batch:
                    break
                yield [tuple(r) for r in batch]
        except DBAPIError as exc:
            raise DRSError("SOURCE_QUERY_FAILED", admin_detail=f"mirror {job.code}: {_short(exc)}") from exc
        finally:
            if not conn.invalidated:
                try:
                    if result is not None:
                        result.close()
                finally:
                    try:
                        if dialect == "mssql" and hasattr(raw, "timeout"):
                            raw.timeout = 0
                        elif dialect == "oracle":
                            raw.call_timeout = 0
                        if dialect == "mssql" and job.read_uncommitted:
                            # A session setting on SQL Server, not a transaction one: left as it is,
                            # the next report on this pooled connection would read uncommitted rows.
                            try:
                                conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                            except Exception:
                                conn.invalidate()
                    finally:
                        try:
                            if transaction.is_active:
                                transaction.rollback()
                        finally:
                            if dialect == "mssql":
                                _back_to_autocommit(conn, raw)


# --------------------------------------------------------------------------------------------
# The target

def target_table(database: Database, job: Job, columns: Sequence[tuple[str, TypeEngine]], *,
                 unlogged: bool = False) -> Table:
    """The copy's table as this mirror writes it: ``_source``, the source columns (lower case),
    ``_synced_at``. The key is (``_source``, key columns). ``unlogged``: PostgreSQL only."""
    cols = [Column(SOURCE_COLUMN, Text, nullable=False)]
    cols += [Column(name.lower(), target_type(typ)) for name, typ in columns]
    cols.append(Column(SYNCED_COLUMN, DateTime, nullable=False))
    extra: list[Any] = []
    if job.key:
        extra.append(PrimaryKeyConstraint(SOURCE_COLUMN, *[k.lower() for k in job.key],
                                          name=f"pk_{job.target}"[:63]))
    else:
        extra.append(Index(f"ix_{job.target}__source"[:63], SOURCE_COLUMN))
    return Table(job.target, MetaData(schema=database.mirror_schema), *cols, *extra,
                 prefixes=["UNLOGGED"] if unlogged else [])


def wants_unlogged(database: Database, settings: AppSettings) -> bool:
    """Whether this server's copies are unlogged tables (``[mirror] unlogged``, PostgreSQL only)."""
    return settings.mirror.unlogged and database.mirror_engine.dialect.name == "postgresql"


def _role(settings: AppSettings) -> str | None:
    role = settings.mirror.read_role.strip()
    if role and not set(role.lower()) <= ROLE_RE_CHARS:
        raise MirrorError(f"[mirror] read_role {role!r}: a plain role name")
    return role or None


def ensure_target(database: Database, settings: AppSettings, job: Job, table: Table) -> None:
    """Creates the table, or adds the columns it lacks. A column is never dropped or changed: the
    copy of another server, or an older definition, may still use it."""
    engine = database.mirror_engine
    schema = database.mirror_schema
    quote = engine.dialect.identifier_preparer.quote
    with engine.begin() as conn:
        if schema and not conn.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :s"), {"s": schema}).first():
            conn.execute(CreateSchema(schema, if_not_exists=True))
        insp = inspect(conn)
        if not insp.has_table(job.target, schema=schema):
            table.create(conn)
            log.info("mirror %s: created table %s", job.code, _display(database, job.target))
        else:
            have = {c["name"].lower() for c in insp.get_columns(job.target, schema=schema)}
            relation = (f"{quote(schema)}." if schema else "") + quote(job.target)
            for col in table.columns:
                if col.name not in have:
                    ddl = col.type.compile(dialect=engine.dialect)
                    conn.exec_driver_sql(f"ALTER TABLE {relation} ADD COLUMN {quote(col.name)} {ddl}")
                    log.info("mirror %s: added column %s to %s", job.code, col.name, _display(database, job.target))
            pk = [c.lower() for c in insp.get_pk_constraint(job.target, schema=schema).get("constrained_columns") or []]
            wanted = [SOURCE_COLUMN] + [k.lower() for k in job.key]
            if job.key and pk != wanted:
                raise MirrorError(f"mirror {job.code}: table {_display(database, job.target)} has the key "
                                  f"({', '.join(pk) or 'none'}), this mirror needs ({', '.join(wanted)})")
            if engine.dialect.name == "postgresql":
                _set_persistence(conn, database, job, relation, wants_unlogged(database, settings))
        role = _role(settings) if schema else None
        if role:
            conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA {quote(schema)} TO {quote(role)}")
            conn.exec_driver_sql(f"GRANT SELECT ON {quote(schema)}.{quote(job.target)} TO {quote(role)}")


def _set_persistence(conn, database: Database, job: Job, relation: str, unlogged: bool) -> None:
    """Switches an existing copy to what ``[mirror] unlogged`` says. The table is rewritten once,
    under an exclusive lock: a report reading it waits for the few seconds that takes."""
    persistence = conn.execute(text("SELECT relpersistence FROM pg_class WHERE oid = to_regclass(:r)"),
                               {"r": relation}).scalar()
    wanted = "u" if unlogged else "p"
    if persistence in ("u", "p") and persistence != wanted:
        conn.exec_driver_sql(f"ALTER TABLE {relation} SET {'UNLOGGED' if unlogged else 'LOGGED'}")
        log.info("mirror %s: table %s is now %s", job.code, _display(database, job.target),
                 "unlogged" if unlogged else "logged")


def _holds_rows(database: Database, job: Job, table: Table) -> bool:
    """Whether the copy has any row of this mirror's source."""
    with database.mirror_engine.connect() as conn:
        found = conn.execute(select(literal_column("1")).select_from(table)
                             .where(table.c[SOURCE_COLUMN] == job.source.code).limit(1)).first()
    return found is not None


def _display(database: Database, name: str) -> str:
    return f"{database.mirror_schema}.{name}" if database.mirror_schema else name


class _Writer:
    """Writes the batches of one run in one target transaction."""

    def __init__(self, database: Database, job: Job, table: Table, full: bool):
        self.watermark = job.watermark_column.lower() if job.watermark_column else None
        self.database = database
        self.job = job
        self.table = table
        self.full = full
        self.engine = database.mirror_engine
        self.pg = self.engine.dialect.name == "postgresql"
        self.names = [c.name for c in table.columns]
        self.key = [SOURCE_COLUMN] + [k.lower() for k in job.key] if job.key else []
        self.stage = f"_mirror_{uuid.uuid4().hex[:12]}"
        self.rows = 0
        self.conn = None
        self.transaction = None

    def __enter__(self) -> "_Writer":
        self.conn = self.engine.connect()
        self.transaction = self.conn.begin()
        if self.pg:
            quote = self.engine.dialect.identifier_preparer.quote
            self.conn.exec_driver_sql(
                f"CREATE TEMPORARY TABLE {quote(self.stage)} (LIKE {self._relation()} INCLUDING DEFAULTS) "
                f"ON COMMIT DROP")
        if self.full:
            self.conn.execute(delete(self.table).where(self.table.c[SOURCE_COLUMN] == self.job.source.code))
        return self

    def _relation(self) -> str:
        quote = self.engine.dialect.identifier_preparer.quote
        schema = self.database.mirror_schema
        return (f"{quote(schema)}." if schema else "") + quote(self.job.target)

    def write(self, rows: list[tuple]) -> None:
        self.rows += len(rows)
        if self.pg:
            from psycopg import sql

            raw = self.conn.connection.dbapi_connection
            copy = sql.SQL("COPY {} ({}) FROM STDIN").format(
                sql.Identifier(self.stage), sql.SQL(", ").join(sql.Identifier(n) for n in self.names))
            with raw.cursor() as cursor, cursor.copy(copy) as out:
                for row in rows:
                    out.write_row(row)
            return
        records = [dict(zip(self.names, row)) for row in rows]
        if not self.key:
            self.conn.execute(self.table.insert(), records)
            return
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = sqlite_insert(self.table)
        stmt = stmt.on_conflict_do_update(index_elements=self.key,
                                          set_={n: stmt.excluded[n] for n in self.names if n not in self.key})
        self.conn.execute(stmt, records)

    def _merge(self) -> None:
        quote = self.engine.dialect.identifier_preparer.quote
        cols = ", ".join(quote(n) for n in self.names)
        if not self.key:
            self.conn.exec_driver_sql(f"INSERT INTO {self._relation()} ({cols}) SELECT {cols} FROM {quote(self.stage)}")
            return
        keys = ", ".join(quote(k) for k in self.key)
        # One row per key: the newest by the watermark, else the last one sent.
        newest = f"{quote(self.watermark)} DESC NULLS LAST, " if self.watermark else ""
        picked = (f"SELECT DISTINCT ON ({keys}) {cols} FROM {quote(self.stage)} "
                  f"ORDER BY {keys}, {newest}ctid DESC")
        others = [n for n in self.names if n not in self.key]
        action = ("DO UPDATE SET " + ", ".join(f"{quote(n)} = EXCLUDED.{quote(n)}" for n in others)
                  if others else "DO NOTHING")
        self.conn.exec_driver_sql(f"INSERT INTO {self._relation()} ({cols}) {picked} ON CONFLICT ({keys}) {action}")

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                if self.pg:
                    self._merge()
                self.transaction.commit()
                if self.pg and (self.full or self.rows >= ANALYZE_ROWS):
                    # A table just filled has no statistics yet: a report's planner took it for a few
                    # rows and looped over it (seen: minutes instead of seconds).
                    self.conn.exec_driver_sql(f"ANALYZE {self._relation()}")
                    self.conn.commit()
            else:
                self.transaction.rollback()
        finally:
            self.conn.close()


# --------------------------------------------------------------------------------------------
# A run

def _wm_value(text_value: str | None, typ: TypeEngine) -> Any:
    if text_value is None:
        return None
    try:
        py = typ.python_type
    except (NotImplementedError, AttributeError):
        return text_value
    try:
        if py is datetime:
            return datetime.fromisoformat(text_value)
        if py is date:
            return date.fromisoformat(text_value[:10])
        if py is int:
            return int(text_value)
        if py in (Decimal, float):
            return Decimal(text_value)
    except (ValueError, ArithmeticError):
        raise MirrorError(f"the stored watermark {text_value!r} does not fit its column; reset it") from None
    return text_value


def _wm_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def load_job(database: Database, code: str) -> Job:
    with database.session() as s:
        m = s.scalars(select(MirrorTable).where(MirrorTable.mirror_code == code)).first()
        if m is None:
            raise MirrorError(f"no mirror {code}")
        ds = s.get(Datasource, m.datasource_id)
        if not ds.is_active:
            raise MirrorError(f"mirror {code}: datasource {ds.datasource_code} is disabled")
        return Job(mirror_id=m.mirror_id, code=m.mirror_code, source=SourceInfo.of(ds), source_object=m.source_object,
                   columns=list(m.columns_json) if m.columns_json else None, where_sql=m.where_sql,
                   target=m.target_table, key=list(m.key_columns_json or []), mode=m.mode,
                   watermark_column=m.watermark_column, watermark_value=m.watermark_value,
                   lookback_seconds=m.lookback_seconds, full_refresh_hours=m.full_refresh_hours,
                   full_hours=m.full_hours, last_full_at=m.last_full_at, read_uncommitted=m.read_uncommitted)


def wants_full(job: Job, force_full: bool, now: datetime, tz) -> bool:
    """A full copy: asked for, a full mirror, nothing copied yet - or the periodic one of an
    incremental mirror, when it is due and the hour is one it may start in (a big table at night)."""
    if force_full or job.mode == "full" or job.watermark_value is None:
        return True
    if job.full_refresh_hours:
        due = job.last_full_at is None or now - job.last_full_at >= timedelta(hours=job.full_refresh_hours)
        hours = parse_hours(job.full_hours)
        return due and (hours is None or now.astimezone(tz).hour in hours)
    return False


def sync(database: Database, settings: AppSettings, code: str, *, force_full: bool = False,
         owner: str | None = None) -> Outcome:
    """Runs the mirror ``code`` now. Raises ``MirrorError`` when another run holds it."""
    job = load_job(database, code)
    owner = owner or owner_name()
    if not claim(database, job.mirror_id, owner, settings.mirror.lock_minutes):
        raise MirrorError(f"mirror {code} is running elsewhere")
    started = time.perf_counter()
    try:
        outcome = _run(database, settings, job, force_full, owner)
    except BaseException as exc:
        message = exc.admin_detail or exc.message if isinstance(exc, DRSError) else str(exc)
        _finish(database, job.mirror_id, owner, last_status="error", last_error=(message or type(exc).__name__)[:2000],
                last_duration_ms=int((time.perf_counter() - started) * 1000))
        log.warning("mirror %s failed: %s", code, message)
        raise
    values: dict[str, Any] = dict(last_status="ok", last_error=None, last_rows=outcome.rows,
                                  last_duration_ms=outcome.duration_ms, watermark_value=outcome.watermark)
    if outcome.full:
        values["last_full_at"] = utcnow()
    _finish(database, job.mirror_id, owner, **values)
    log.info("mirror %s: %s copy, %d rows in %d ms", code, "full" if outcome.full else "incremental",
             outcome.rows, outcome.duration_ms)
    return outcome


def _run(database: Database, settings: AppSettings, job: Job, force_full: bool, owner: str) -> Outcome:
    started = time.perf_counter()
    full = wants_full(job, force_full, utcnow(), settings.tz)
    columns = source_columns(job)
    names = [name for name, _ in columns]
    table = target_table(database, job, columns, unlogged=wants_unlogged(database, settings))
    ensure_target(database, settings, job, table)
    if not full and not _holds_rows(database, job, table):
        # A crash empties an unlogged table, but the watermark survives in the DRS schema: an
        # incremental run would add the latest rows to an empty copy and call it up to date.
        log.info("mirror %s: the copy holds no row of %s - full copy", job.code, job.source.code)
        full = True

    wm_index = wm_type = None
    if job.watermark_column:
        wm_index = next(i for i, n in enumerate(names) if n.lower() == job.watermark_column.lower())
        wm_type = columns[wm_index][1]
    since = None
    if not full:
        since = _wm_value(job.watermark_value, wm_type)
        if isinstance(since, datetime) and job.lookback_seconds:
            since -= timedelta(seconds=job.lookback_seconds)
    converters = [_as_text if isinstance(c.type, Text) else None for c in table.columns][1:-1]
    # A full copy replaces every row: its watermark is the highest it read, or none (the next run is
    # then a full copy again) - never the one of the rows it has just replaced.
    highest = _wm_value(job.watermark_value, wm_type) if (job.watermark_column and not full) else None
    count = 0
    synced_at = utcnow().replace(tzinfo=None)
    beat = time.monotonic()
    with _Writer(database, job, table, full) as writer:
        for batch in read_source(job, names, since, settings.mirror.batch_rows, settings.mirror.timeout_seconds):
            rows = []
            for row in batch:
                values = [conv(v) if conv else v for conv, v in zip(converters, row)]
                rows.append((job.source.code, *values, synced_at))
                if wm_index is not None:
                    value = row[wm_index]
                    if value is not None and (highest is None or value > highest):
                        highest = value
            writer.write(rows)
            count += len(rows)
            if time.monotonic() - beat > HEARTBEAT_SECONDS:
                renew(database, job.mirror_id, owner, settings.mirror.lock_minutes)
                beat = time.monotonic()
    return Outcome(code=job.code, full=full, rows=count, duration_ms=int((time.perf_counter() - started) * 1000),
                   watermark=_wm_text(highest) if highest is not None else (None if full else job.watermark_value))


# --------------------------------------------------------------------------------------------
# What is due

def due(database: Database, settings: AppSettings, now: datetime | None = None) -> list[str]:
    """The codes of the active mirrors whose interval has passed, in an hour they may run in."""
    now = now or utcnow()
    hour = now.astimezone(settings.tz).hour
    out = []
    with database.session() as s:
        for m in s.scalars(select(MirrorTable).where(MirrorTable.is_active).order_by(MirrorTable.mirror_code)):
            if m.lock_until is not None and m.lock_until > now:
                continue
            hours = parse_hours(m.run_hours)
            if hours is not None and hour not in hours:
                continue
            last = m.last_finished_at or m.last_started_at
            if last is None or now - last >= timedelta(minutes=m.interval_minutes):
                out.append(m.mirror_code)
    return out


def run_due(database: Database, settings: AppSettings, owner: str | None = None) -> list[Outcome]:
    """Runs every mirror that is due, one after the other; a failure is logged and the next runs."""
    owner = owner or owner_name()
    done = []
    for code in due(database, settings):
        try:
            done.append(sync(database, settings, code, owner=owner))
        except MirrorError as exc:
            log.info("mirror %s skipped: %s", code, exc)
        except Exception:  # recorded on the mirror by sync(); the others still run
            continue
    return done
