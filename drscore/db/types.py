"""Column types that behave the same on PostgreSQL and SQLite (spec section 8.1)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

# Identity primary key: BIGINT on PostgreSQL, INTEGER on SQLite (only INTEGER PRIMARY KEY is a
# rowid alias there, which AUTOINCREMENT requires).
IdType = BigInteger().with_variant(Integer(), "sqlite")

# JSON document: JSONB on PostgreSQL, JSON (text) on SQLite.
JsonType = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator):
    """A UTC timestamp.

    PostgreSQL stores ``timestamptz``. SQLite stores text without an offset, always in UTC (which
    is also what its ``CURRENT_TIMESTAMP`` produces). Values read back are timezone-aware UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):  # a value inserted by hand on SQLite
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
