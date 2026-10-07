"""Connections to report sources (spec section 11.1).

Supported sources: SQL Server 2008 R2 and later, Oracle, PostgreSQL, SQLite.

One SQLAlchemy engine per datasource, reused across runs and rebuilt when the datasource row
changes. Sources are read-only: SQLite files are opened with ``mode=ro``; PostgreSQL and Oracle
run every report in a read-only transaction (see ``drs.reports.executor``); the login of an MSSQL
source must only have SELECT / EXECUTE rights there.

SQL Server drivers (``drs_datasource.driver``):

* empty / ``pyodbc``: pyodbc with "ODBC Driver 18 for SQL Server";
* an ODBC driver name, for example ``ODBC Driver 17 for SQL Server``: pyodbc with that driver;
* ``pymssql``: the open-source FreeTDS driver, no ODBC install needed.

For SQL Server 2008 R2, whose TLS support is old, either use ``pymssql`` or set ``ssl_mode =
'disable'`` (or ``trust_server_certificate = true``).

The connection settings are columns of drs_datasource (as a DBA tool such as Navicat keeps them):
host, port, SQL Server instance, database (Oracle: service name or SID), default schema,
authentication (password / Windows / none), SSL mode, trust of the server certificate, connect
timeout, driver; ``options_json`` adds any other driver option. ``connection_spec`` turns them
into the parameters of each driver.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.pool import NullPool

from drs.datasources import secrets
from drs.db.models import Datasource
from drs.errors import DRSError
from drs.settings import resolve_path

DEFAULT_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"
DEFAULT_CONNECT_TIMEOUT = 15  # seconds
SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]{0,127}$")
POOL = {"pool_size": 2, "max_overflow": 3, "pool_recycle": 1800, "pool_pre_ping": True}


class SourceInfo(BaseModel):
    """What the executor needs from a datasource row, detached from any DRS session. Frozen and
    hashable: two equal values mean the same connection, so a cached engine can be reused."""

    model_config = ConfigDict(frozen=True)

    datasource_id: int
    code: str
    db_type: str
    host: str | None = None
    port: int | None = None
    instance_name: str | None = None
    database_name: str
    oracle_connect_by: str | None = None
    default_schema: str | None = None
    auth_method: str = "password"
    username: str | None = None
    secret_ref: str | None = None
    ssl_mode: str | None = None
    trust_server_certificate: bool = False
    connect_timeout_seconds: int | None = None
    driver: str | None = None
    options: tuple[tuple[str, str], ...] = ()
    updated_at: Any = None

    @classmethod
    def of(cls, ds: Datasource) -> "SourceInfo":
        options = tuple(sorted((str(k), str(v)) for k, v in (ds.options_json or {}).items()))
        return cls(datasource_id=ds.datasource_id, code=ds.datasource_code, db_type=ds.db_type, host=ds.host,
                   port=ds.port, instance_name=ds.instance_name, database_name=ds.database_name,
                   oracle_connect_by=ds.oracle_connect_by, default_schema=ds.default_schema,
                   auth_method=ds.auth_method, username=ds.username, secret_ref=ds.secret_ref,
                   ssl_mode=ds.ssl_mode, trust_server_certificate=ds.trust_server_certificate,
                   connect_timeout_seconds=ds.connect_timeout_seconds, driver=ds.driver, options=options,
                   updated_at=ds.updated_at)

    @property
    def timeout(self) -> int:
        return self.connect_timeout_seconds or DEFAULT_CONNECT_TIMEOUT


@dataclass
class ConnectionSpec:
    """How SQLAlchemy is asked to connect: built from the datasource row only, so it can be
    checked without a server."""

    url: URL
    connect_args: dict[str, Any] = field(default_factory=dict)
    engine_args: dict[str, Any] = field(default_factory=dict)
    on_connect: list[str] = field(default_factory=list)  # statements run on every new connection


def _mssql_skip_rowcount_results(conn, cursor, statement, parameters, context, executemany) -> None:
    """The first result set that has columns is the result (spec 11.2 rule 3): skip results a
    procedure produces before its final SELECT that carry no columns."""
    try:
        while cursor.description is None and cursor.nextset():
            pass
    except Exception:  # a driver without nextset support; SQLAlchemy reports "no result" itself
        pass


def _odbc_value(value: str) -> str:
    """A value in an ODBC connection string, braced so ; = { } in a password are safe."""
    return "{" + str(value).replace("}", "}}") + "}"


def _schema(src: SourceInfo) -> str | None:
    if not src.default_schema:
        return None
    if not SCHEMA_RE.match(src.default_schema):
        raise DRSError("REPORT_MISCONFIGURED",
                       admin_detail=f"datasource {src.code}: default_schema {src.default_schema!r} is not a plain name")
    return src.default_schema


def connection_spec(src: SourceInfo, password: str | None) -> ConnectionSpec:
    options = dict(src.options)
    if src.auth_method != "password":
        password = None

    if src.db_type == "sqlite":
        path = resolve_path(src.database_name)
        url = URL.create("sqlite+pysqlite", database=f"file:{path.as_posix()}", query={"mode": "ro", "uri": "true"})
        # NullPool: a SQLite connection is cheap, and the timeout handler is per connection.
        return ConnectionSpec(url, {"check_same_thread": False}, {"poolclass": NullPool})

    if src.db_type == "postgresql":
        url = URL.create("postgresql+psycopg", username=src.username, password=password, host=src.host,
                         port=src.port, database=src.database_name)
        args: dict[str, Any] = {"application_name": "drs-report", "connect_timeout": src.timeout}
        if src.ssl_mode:
            args["sslmode"] = {"disable": "disable", "prefer": "prefer", "require": "require",
                               "verify": "require" if src.trust_server_certificate else "verify-full"}[src.ssl_mode]
        if (schema := _schema(src)) is not None:
            args["options"] = f"-c search_path={schema},public"
        return ConnectionSpec(url, {**args, **options}, dict(POOL))

    if src.db_type == "oracle":
        engine_args: dict[str, Any] = dict(POOL)
        if (src.driver or "").strip().lower() == "thick":
            lib_dir = options.pop("lib_dir", None)
            engine_args["thick_mode"] = {"lib_dir": lib_dir} if lib_dir else True
        by_sid = src.oracle_connect_by == "sid"
        url = URL.create("oracle+oracledb", username=src.username, password=password, host=src.host,
                         port=src.port or 1521, database=src.database_name if by_sid else None,
                         query={} if by_sid else {"service_name": src.database_name})
        args = {"tcp_connect_timeout": float(src.timeout)}
        if src.ssl_mode in ("require", "verify"):
            args["protocol"] = "tcps"
            args["ssl_server_dn_match"] = src.ssl_mode == "verify" and not src.trust_server_certificate
        on_connect = []
        if (schema := _schema(src)) is not None:
            on_connect.append(f"ALTER SESSION SET CURRENT_SCHEMA = {schema}")
        return ConnectionSpec(url, {**args, **options}, engine_args, on_connect)

    if src.db_type == "mssql":
        driver = (src.driver or "").strip()
        if driver.lower() == "pymssql":
            server = f"{src.host}\\{src.instance_name}" if src.instance_name else src.host
            url = URL.create("mssql+pymssql", username=src.username, password=password, host=server,
                             port=None if src.instance_name else src.port, database=src.database_name)
            return ConnectionSpec(url, {"login_timeout": src.timeout, **options}, dict(POOL))
        odbc = driver if driver and driver.lower() != "pyodbc" else DEFAULT_ODBC_DRIVER
        if src.instance_name:
            server = f"{src.host}\\{src.instance_name}"  # the port comes from SQL Server Browser
        else:
            server = f"{src.host},{src.port}" if src.port else src.host
        parts = {"DRIVER": _odbc_value(odbc), "SERVER": _odbc_value(server), "DATABASE": _odbc_value(src.database_name)}
        if src.auth_method == "windows":
            parts["Trusted_Connection"] = "yes"
        elif src.username:
            parts["UID"] = _odbc_value(src.username)
            if password is not None:
                parts["PWD"] = _odbc_value(password)
        encrypt = {"disable": "no", "prefer": "yes", "require": "yes", "verify": "yes"}.get(src.ssl_mode or "")
        if encrypt:
            parts["Encrypt"] = encrypt
        trust = src.trust_server_certificate or src.ssl_mode == "prefer"
        if src.ssl_mode == "verify":
            trust = False
        if trust or src.ssl_mode:
            parts["TrustServerCertificate"] = "yes" if trust else "no"
        parts["APP"] = "drs-report"
        for key, value in options.items():
            parts[key] = _odbc_value(value)
        odbc_connect = ";".join(f"{k}={v}" for k, v in parts.items())
        url = URL.create("mssql+pyodbc", query={"odbc_connect": odbc_connect})
        return ConnectionSpec(url, {"timeout": src.timeout}, dict(POOL))

    raise DRSError("REPORT_MISCONFIGURED", admin_detail=f"datasource {src.code}: unknown db_type {src.db_type!r}")


def build_engine(src: SourceInfo) -> Engine:
    spec = connection_spec(src, secrets.resolve(src.secret_ref, src.code) if src.auth_method == "password" else None)
    engine = create_engine(spec.url, connect_args=spec.connect_args, **spec.engine_args)
    if src.db_type == "mssql":
        event.listen(engine, "after_cursor_execute", _mssql_skip_rowcount_results)
    if spec.on_connect:
        statements = list(spec.on_connect)

        def _session_setup(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                for statement in statements:
                    cursor.execute(statement)
            finally:
                cursor.close()

        event.listen(engine, "connect", _session_setup)
    return engine


class EngineCache:
    """Engines by datasource id. An engine is rebuilt when the row's connection fields or
    ``updated_at`` differ from the ones it was built with."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engines: dict[int, tuple[SourceInfo, Engine]] = {}

    def get(self, src: SourceInfo) -> Engine:
        with self._lock:
            cached = self._engines.get(src.datasource_id)
            if cached and cached[0] == src:
                return cached[1]
            try:
                engine = build_engine(src)
            except ImportError as exc:  # e.g. pyodbc without unixODBC on Linux
                raise DRSError("DATASOURCE_UNAVAILABLE",
                               admin_detail=f"datasource {src.code}: the driver cannot be loaded ({exc})") from exc
            self._engines[src.datasource_id] = (src, engine)
        if cached:
            cached[1].dispose()
        return engine

    def dispose_all(self) -> None:
        with self._lock:
            for _, engine in self._engines.values():
                engine.dispose()
            self._engines.clear()


engines = EngineCache()
