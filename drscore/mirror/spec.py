"""The definition of a mirror, as the CLI and an import file give it, and its checks."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,99}$")
IDENT = r"[A-Za-z_#@][A-Za-z0-9_$#@]{0,127}"
OBJECT_RE = re.compile(rf"^{IDENT}(\.{IDENT})?$")  # table or view, [schema.]name
COLUMN_RE = re.compile(rf"^{IDENT}$")
TARGET_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
HOURS_RE = re.compile(r"^\d{1,2}(-\d{1,2})?(,\d{1,2}(-\d{1,2})?)*$")
# Columns every target table has: who the row came from, and when it was copied.
SOURCE_COLUMN = "_source"
SYNCED_COLUMN = "_synced_at"


class MirrorError(Exception):
    """A mirror definition that cannot be used, or a target table that does not fit it."""


def parse_hours(value: str | None) -> set[int] | None:
    """``"0-6,22-23"`` -> {0, ..., 6, 22, 23}; empty: any hour (None)."""
    if not value:
        return None
    text = value.replace(" ", "")
    if not HOURS_RE.match(text):
        raise MirrorError(f"run_hours {value!r}: use hours 0-23, e.g. 0-6,22-23")
    hours: set[int] = set()
    for part in text.split(","):
        first, _, last = part.partition("-")
        lo, hi = int(first), int(last or first)
        if not (0 <= lo <= 23 and 0 <= hi <= 23 and lo <= hi):
            raise MirrorError(f"run_hours {value!r}: {part} is not a range of hours 0-23")
        hours.update(range(lo, hi + 1))
    return hours


class MirrorSpec(BaseModel):
    """What an administrator says about a mirror. ``datasource`` is a datasource code."""

    model_config = ConfigDict(extra="forbid")

    code: str
    datasource: str
    source: str = Field(description="table or view of the source, [schema.]name")
    target: str
    columns: list[str] | None = None
    where: str | None = None
    key: list[str] | None = None
    mode: str = "full"
    watermark: str | None = None
    lookback_seconds: int = Field(0, ge=0)
    interval_minutes: int = Field(60, ge=1)
    full_refresh_hours: int | None = Field(None, ge=1)
    run_hours: str | None = None
    full_hours: str | None = None  # incremental: the hours the periodic full copy may start in
    read_uncommitted: bool = False
    active: bool = True

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not CODE_RE.match(v):
            raise ValueError("letters, digits, '_', '.', '-'; at most 100")
        return v

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        if not OBJECT_RE.match(v):
            raise ValueError("a table or view name, optionally schema.name, without brackets or quotes")
        return v

    @field_validator("target")
    @classmethod
    def _target(cls, v: str) -> str:
        if not TARGET_RE.match(v):
            raise ValueError("lower case letters, digits and '_', starting with a letter; at most 63")
        return v

    @field_validator("columns", "key")
    @classmethod
    def _columns(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        names = [c.strip() for c in v if c.strip()]
        for name in names:
            if not COLUMN_RE.match(name):
                raise ValueError(f"{name!r} is not a plain column name")
            if name.lower() in (SOURCE_COLUMN, SYNCED_COLUMN):
                raise ValueError(f"{name!r} is a column of the mirror itself")
        if len({n.lower() for n in names}) != len(names):
            raise ValueError("a column is named twice")
        return names or None

    @field_validator("watermark")
    @classmethod
    def _watermark(cls, v: str | None) -> str | None:
        if v is not None and not COLUMN_RE.match(v):
            raise ValueError(f"{v!r} is not a plain column name")
        return v or None

    @field_validator("where")
    @classmethod
    def _where(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if ";" in v:
            raise ValueError("one condition, without ';'")
        return v or None

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in ("full", "incremental"):
            raise ValueError("full or incremental")
        return v

    @field_validator("run_hours", "full_hours")
    @classmethod
    def _hours(cls, v: str | None) -> str | None:
        try:
            parse_hours(v)
        except MirrorError as exc:
            raise ValueError(str(exc)) from exc
        return v.replace(" ", "") if v else None

    @model_validator(mode="after")
    def _incremental(self) -> "MirrorSpec":
        if self.mode == "incremental" and not (self.key and self.watermark):
            raise ValueError("an incremental mirror needs key and watermark columns")
        if self.columns:  # the key and the watermark are always copied
            have = {c.lower() for c in self.columns}
            for c in (self.key or []) + ([self.watermark] if self.watermark else []):
                if c.lower() not in have:
                    self.columns.append(c)
                    have.add(c.lower())
        return self


def spec_of(data: dict[str, Any]) -> MirrorSpec:
    try:
        return MirrorSpec(**data)
    except ValidationError as exc:
        where = data.get("code") or "mirror"
        raise MirrorError("; ".join(f"{where}.{'.'.join(map(str, e['loc'])) or 'definition'}: {e['msg']}"
                                    for e in exc.errors())) from exc


def as_columns(value: str | list[str] | None) -> list[str] | None:
    """A comma-separated list from the command line."""
    if value is None or isinstance(value, list):
        return value
    return [c.strip() for c in value.split(",") if c.strip()] or None
