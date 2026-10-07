"""Turns a source result into JSON-safe values with a type per column (spec section 11.4)."""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Sequence
from zoneinfo import ZoneInfo

MAX_SAFE_INT = 2 ** 53

# psycopg reports PostgreSQL type OIDs.
PG_OIDS = {
    16: "bool", 20: "int", 21: "int", 23: "int", 26: "int",
    700: "float", 701: "float", 1700: "decimal",
    1082: "date", 1114: "datetime", 1184: "datetime", 1083: "time", 1266: "time",
}
# pyodbc reports Python types.
PY_TYPES = {
    bool: "bool", int: "int", float: "float", Decimal: "decimal",
    datetime: "datetime", date: "date", time: "time", str: "text",
    bytes: "text", bytearray: "text",
}
# pymssql: STRING=1, BINARY=2, NUMBER=3, DATETIME=4, DECIMAL=5. NUMBER and DATETIME are ambiguous
# (int or float, date or datetime) and are inferred from the values.
PYMSSQL_CODES = {1: "text", 2: "text", 5: "decimal"}
# python-oracledb reports DbType objects. NUMBER is int or decimal depending on its scale, so it
# is inferred from the values (SQLAlchemy returns int or Decimal, never a lossy float).
ORACLE_TYPES = {
    "DB_TYPE_VARCHAR": "text", "DB_TYPE_NVARCHAR": "text", "DB_TYPE_CHAR": "text", "DB_TYPE_NCHAR": "text",
    "DB_TYPE_CLOB": "text", "DB_TYPE_NCLOB": "text", "DB_TYPE_LONG": "text", "DB_TYPE_ROWID": "text",
    "DB_TYPE_RAW": "text", "DB_TYPE_BLOB": "text", "DB_TYPE_LONG_RAW": "text",
    "DB_TYPE_BINARY_FLOAT": "float", "DB_TYPE_BINARY_DOUBLE": "float", "DB_TYPE_BINARY_INTEGER": "int",
    "DB_TYPE_DATE": "datetime", "DB_TYPE_TIMESTAMP": "datetime", "DB_TYPE_TIMESTAMP_TZ": "datetime",
    "DB_TYPE_TIMESTAMP_LTZ": "datetime", "DB_TYPE_BOOLEAN": "bool",
}


def type_from_description(dialect_driver: str, type_code: Any) -> str | None:
    if dialect_driver == "psycopg" and isinstance(type_code, int):
        return PG_OIDS.get(type_code, "text")
    if dialect_driver == "pyodbc" and isinstance(type_code, type):
        for py_type, name in PY_TYPES.items():
            if type_code is py_type:
                return name
        return "text"
    if dialect_driver == "pymssql" and isinstance(type_code, int):
        return PYMSSQL_CODES.get(type_code)
    if dialect_driver == "oracledb":
        return ORACLE_TYPES.get(getattr(type_code, "name", ""))
    return None  # SQLite and others give no usable type


def type_of_value(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, time):
        return "time"
    return "text"


def infer_types(declared: Sequence[str | None], rows: Sequence[Sequence[Any]]) -> list[str]:
    """The declared type where the driver gave one, else the type of the first non-null value."""
    types = list(declared)
    for i, t in enumerate(types):
        if t is None:
            types[i] = next((type_of_value(r[i]) for r in rows if r[i] is not None), "text")
    return types


def to_json_value(value: Any, tz: ZoneInfo) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > MAX_SAFE_INT else value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(tz).replace(tzinfo=None)
        return value.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "[binary]"
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def normalise_rows(rows: Sequence[Sequence[Any]], tz: ZoneInfo) -> list[list[Any]]:
    return [[to_json_value(v, tz) for v in row] for row in rows]
