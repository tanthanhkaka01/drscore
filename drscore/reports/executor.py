"""Runs a report's query on its source (spec sections 11.2, 11.3, 11.4)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import bindparam, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from drscore.datasources.connectors import SourceInfo, engines
from drscore.errors import DRSError
from drscore.reports import normalize
from drscore.reports.params import ParamDef, bind_values, binds_of

log = logging.getLogger(__name__)

FETCH_BATCH = 1000


@dataclass
class QueryResult:
    columns: list[dict[str, str]]  # [{"name": ..., "type": ...}]
    rows: list[list[Any]]  # JSON-safe values
    duration_ms: int
    byte_size: int


def _sqlite_value(value: Any) -> Any:
    # The sqlite3 module has no adapter for these (the date ones are deprecated since 3.12).
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_sqlite_value(v) for v in value]
    return value


def _is_timeout(exc: DBAPIError, dialect: str, timed_out: bool) -> bool:
    if timed_out:
        return True
    orig = exc.orig
    if dialect == "postgresql":
        return getattr(orig, "sqlstate", None) == "57014"  # query_canceled
    if dialect == "mssql":
        return "HYT00" in str(orig) or "timeout" in str(orig).lower()
    if dialect == "oracle":  # call timeout exceeded / user requested cancel
        return any(code in str(orig) for code in ("DPY-4024", "DPY-4011", "ORA-03156", "ORA-01013"))
    return False


def ping_sql(db_type: str) -> str:
    """A trivial query for ``datasource test``."""
    return "SELECT 1 AS ok FROM dual" if db_type == "oracle" else "SELECT 1 AS ok"


def run_query(src: SourceInfo, query_text: str, defs: Sequence[ParamDef], values: dict[str, Any], *,
              timeout_seconds: int, max_rows: int, max_bytes: int, tz: ZoneInfo) -> QueryResult:
    """Executes the query and returns the first result set that has columns.

    Values are always bound. The run fails with ``RESULT_TOO_LARGE`` as soon as it passes
    ``max_rows`` or ``max_bytes`` - a partial result is never returned.
    """
    used = set(binds_of(query_text))
    expanding = [bindparam(p.name, expanding=True) for p in defs
                 if p.is_multi and p.multi_bind_mode == "expand" and p.name in used]
    params = {k: v for k, v in bind_values(defs, values).items() if k in used}

    engine = engines.get(src)
    dialect = engine.dialect.name
    sql = query_text
    if dialect == "mssql":
        sql = "SET NOCOUNT ON;\n" + sql
    if dialect == "sqlite":
        params = {k: _sqlite_value(v) for k, v in params.items()}
    stmt = text(sql).bindparams(*expanding) if expanding else text(sql)

    started = time.perf_counter()
    try:
        conn = engine.connect()
    except SQLAlchemyError as exc:
        raise DRSError("DATASOURCE_UNAVAILABLE",
                       admin_detail=f"datasource {src.code}: {_short(exc)}") from exc

    timed_out = False
    try:
        with conn, conn.begin():
            raw = conn.connection.dbapi_connection
            if dialect == "postgresql":
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_seconds * 1000)}")
            elif dialect == "oracle":
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                raw.call_timeout = int(timeout_seconds * 1000)  # python-oracledb, milliseconds
            elif dialect == "sqlite":
                deadline = time.monotonic() + timeout_seconds

                def progress() -> int:
                    nonlocal timed_out
                    if time.monotonic() > deadline:
                        timed_out = True
                        return 1  # interrupts the statement
                    return 0

                raw.set_progress_handler(progress, 10000)
            elif dialect == "mssql" and hasattr(raw, "timeout"):
                raw.timeout = int(timeout_seconds)  # pyodbc query timeout

            result = conn.execute(stmt, params)
            if not result.returns_rows:
                raise DRSError("SOURCE_QUERY_FAILED", admin_detail="the query returned no result set")
            names = list(result.keys())
            description = result.cursor.description if result.cursor is not None else None
            types: list[str | None] = [
                normalize.type_from_description(engine.dialect.driver, d[1]) for d in description
            ] if description else [None] * len(names)

            rows: list[list[Any]] = []
            byte_size = 2
            while True:
                batch = result.fetchmany(FETCH_BATCH)
                if not batch:
                    break
                if len(rows) + len(batch) > max_rows:
                    raise DRSError("RESULT_TOO_LARGE",
                                   admin_detail=f"more than {max_rows} rows")
                if None in types:
                    types = [t or next((normalize.type_of_value(r[i]) for r in batch if r[i] is not None), None)
                             for i, t in enumerate(types)]
                converted = normalize.normalise_rows(batch, tz)
                byte_size += len(json.dumps(converted, ensure_ascii=False, separators=(",", ":"))
                                 .encode("utf-8"))
                if byte_size > max_bytes:
                    raise DRSError("RESULT_TOO_LARGE",
                                   admin_detail=f"more than {max_bytes // (1024 * 1024)} MB")
                rows.extend(converted)
            if dialect == "sqlite":
                raw.set_progress_handler(None, 0)
            elif dialect == "mssql" and hasattr(raw, "timeout"):
                raw.timeout = 0
            elif dialect == "oracle":
                raw.call_timeout = 0
    except DRSError:
        raise
    except DBAPIError as exc:
        if exc.connection_invalidated:
            raise DRSError("DATASOURCE_UNAVAILABLE", admin_detail=f"datasource {src.code}: {_short(exc)}") from exc
        if _is_timeout(exc, dialect, timed_out):
            raise DRSError("SOURCE_TIMEOUT", admin_detail=f"more than {timeout_seconds} s") from exc
        raise DRSError("SOURCE_QUERY_FAILED", admin_detail=_short(exc)) from exc
    except SQLAlchemyError as exc:
        raise DRSError("SOURCE_QUERY_FAILED", admin_detail=_short(exc)) from exc

    columns = [{"name": n, "type": t or "text"} for n, t in zip(names, types)]
    return QueryResult(columns, rows, int((time.perf_counter() - started) * 1000), byte_size)


def _short(exc: Exception) -> str:
    """The driver's message without SQLAlchemy's SQL echo and background link."""
    orig = getattr(exc, "orig", None)
    return str(orig if orig is not None else exc).strip().splitlines()[0][:500]
