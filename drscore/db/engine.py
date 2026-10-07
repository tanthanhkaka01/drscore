"""The engine and sessions of the DRS database.

``database.toml`` alone decides the engine (spec section 6.1). Everything else in DRS talks to the
DRS database through the session factory returned here.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from drscore.settings import ConfigError, DatabaseSettings, get_settings

log = logging.getLogger(__name__)


def build_url(db: DatabaseSettings) -> URL:
    if db.active == "sqlite":
        return URL.create("sqlite+pysqlite", database=str(db.sqlite.path))
    pg = db.postgresql
    password = pg.password()
    if password is None:
        raise ConfigError(
            f"The DRS database password is not set: define the environment variable {pg.password_env}."
        )
    return URL.create(
        "postgresql+psycopg",
        username=pg.user,
        password=password,
        host=pg.host,
        port=pg.port,
        database=pg.database,
    )


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    # Foreign keys are off by default in SQLite; WAL lets readers and the writer work together.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA busy_timeout = 30000")
    cursor.close()


def create_bi_engine(db: DatabaseSettings) -> Engine:
    """SQLite only: the file of the BI datasets. Another program reads it (Superset, through a
    read-only mount), which decides two things. No WAL: a reader of a WAL database must be able to
    write beside it. And real transactions, DDL included - pysqlite would commit before every
    CREATE / DROP, and the reader could find the table gone in the middle of a replacement."""
    db.sqlite.bi_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(db.sqlite.bi_path)),
                           connect_args={"timeout": 30, "check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _record) -> None:
        dbapi_connection.isolation_level = None  # the driver starts no transaction of its own
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode = DELETE")
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(conn) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def create_drs_engine(db: DatabaseSettings) -> Engine:
    url = build_url(db)
    if db.active == "sqlite":
        db.sqlite.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"timeout": 30, "check_same_thread": False})
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    pg = db.postgresql
    return create_engine(
        url,
        pool_size=pg.pool_size,
        max_overflow=pg.max_overflow,
        pool_pre_ping=True,
        connect_args={
            "sslmode": pg.sslmode,
            # The DRS schema first: models, migrations and hand-written SQL use unqualified names.
            "options": f"-c search_path={pg.schema},public",
            "application_name": "drs",
        },
    )


class Database:
    """The engine and session factory of one process."""

    def __init__(self, db: DatabaseSettings):
        self.settings = db
        self.engine = create_drs_engine(db)
        self.sessionmaker = sessionmaker(self.engine, expire_on_commit=False)
        self._bi_engine: Engine | None = None

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    @property
    def bi_engine(self) -> Engine:
        """Where the BI datasets are written: schema ``bi_schema`` of the DRS database on
        PostgreSQL, a file of their own on SQLite (``[sqlite] bi_path``)."""
        if self.settings.active != "sqlite":
            return self.engine
        if self._bi_engine is None:
            self._bi_engine = create_bi_engine(self.settings)
        return self._bi_engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A session that commits on success and rolls back on any error."""
        with self.sessionmaker() as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def dispose(self) -> None:
        self.engine.dispose()
        if self._bi_engine is not None:
            self._bi_engine.dispose()
            self._bi_engine = None


_database: Database | None = None


def get_database() -> Database:
    global _database
    if _database is None:
        db = get_settings().database
        _database = Database(db)
        log.info("DRS database: %s", db.describe())
    return _database


def reset_database() -> None:
    global _database
    if _database is not None:
        _database.dispose()
    _database = None
