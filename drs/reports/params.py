"""Report parameters: bind names, validation, defaults, canonical form (spec sections 11.2, 12.1)."""

from __future__ import annotations

import calendar
import json
import re
import threading
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Literal, Sequence

from pydantic import BaseModel, ConfigDict

from drs.db.models import ReportParam
from drs.errors import DRSError

# The same pattern SQLAlchemy's text() uses for named binds, so DRS and the driver agree on what a
# bind is. A literal colon is written "\:"; "::" (PostgreSQL casts) is not a bind.
BIND_RE = re.compile(r"(?<![:\w\x5c]):(\w+)(?!:)")

INT_RE = re.compile(r"^[+-]?\d+$")
DECIMAL_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$")

# default_value: one text column for every type (owner decision 2026-10-06). It holds a literal in
# the type's text form, or a token. A literal that starts with "@" is written "@@".
DATE_TOKEN_RE = re.compile(
    r"^@(today|yesterday|now|week_start|month_start|month_end|prev_month_start|prev_month_end|year_start|year_end)"
    r"(?:\s*([+-])\s*(\d{1,4})\s*([dwmy]))?$")
USER_TOKEN_RE = re.compile(r"^@user\.([A-Za-z0-9_]{1,100})$")


def binds_of(query_text: str) -> list[str]:
    seen: list[str] = []
    for name in BIND_RE.findall(query_text or ""):
        if name not in seen:
            seen.append(name)
    return seen


class ParamDef(BaseModel):
    """A drs_report_param row, detached from the session."""

    model_config = ConfigDict(frozen=True)

    name: str
    label: str
    data_type: Literal["text", "int", "decimal", "date", "datetime", "bool"]
    input_kind: Literal["input", "select", "multiselect"] = "input"
    is_required: bool = False
    default_value: str | None = None
    options_json: Any = None
    options_query: str | None = None
    options_datasource_id: int | None = None
    multi_bind_mode: Literal["expand", "csv"] = "expand"
    min_value: str | None = None
    max_value: str | None = None
    max_length: int | None = None
    regex: str | None = None

    @classmethod
    def of(cls, p: ReportParam) -> "ParamDef":
        return cls(name=p.param_name, label=p.label, data_type=p.data_type, input_kind=p.input_kind,
                   is_required=p.is_required, default_value=p.default_value, options_json=p.options_json,
                   options_query=p.options_query, options_datasource_id=p.options_datasource_id,
                   multi_bind_mode=p.multi_bind_mode, min_value=p.min_value, max_value=p.max_value,
                   max_length=p.max_length, regex=p.regex)

    @property
    def is_multi(self) -> bool:
        return self.input_kind == "multiselect"

    @property
    def has_options(self) -> bool:
        return self.input_kind in ("select", "multiselect")


class ValueError_(ValueError):
    """A submitted value cannot be used; the message is shown beside the field."""


# --------------------------------------------------------------------------------------------
# Parsing one value

def parse_value(data_type: str, raw: Any) -> Any:
    if isinstance(raw, bool):
        raw = "true" if raw else "false"
    if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
        raw = str(raw)
    if not isinstance(raw, str):
        raise ValueError_("must be a single value")
    text = raw.strip()
    if data_type == "text":
        return raw
    if data_type == "int":
        if not INT_RE.match(text):
            raise ValueError_("must be a whole number")
        return int(text)
    if data_type == "decimal":
        if not DECIMAL_RE.match(text):
            raise ValueError_("must be a number with '.' as decimal separator")
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            raise ValueError_("must be a number") from exc
    if data_type == "date":
        if not DATE_RE.match(text):
            raise ValueError_("must be a date YYYY-MM-DD")
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError_("is not a valid date") from exc
    if data_type == "datetime":
        if not DATETIME_RE.match(text):
            raise ValueError_("must be a date and time YYYY-MM-DD HH:MM[:SS]")
        try:
            return datetime.fromisoformat(text.replace("T", " "))
        except ValueError as exc:
            raise ValueError_("is not a valid date and time") from exc
    if data_type == "bool":
        if text.lower() not in ("true", "false"):
            raise ValueError_("must be true or false")
        return text.lower() == "true"
    raise ValueError_(f"has an unknown type {data_type}")


@dataclass(frozen=True)
class DefaultContext:
    """What default tokens are evaluated against: the time in app.timezone and the viewer."""

    now: datetime  # local time, no offset
    user_attributes: dict[str, list[str]] = field(default_factory=dict)

    @property
    def today(self) -> date:
        return self.now.date()


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def _date_token(name: str, sign: str | None, amount: str | None, unit: str | None, ctx: DefaultContext):
    today = ctx.today
    first = today.replace(day=1)
    prev_last = first - timedelta(days=1)
    base = {
        "today": today,
        "now": ctx.now,
        "yesterday": today - timedelta(days=1),
        "week_start": today - timedelta(days=today.weekday()),  # Monday
        "month_start": first,
        "month_end": today.replace(day=calendar.monthrange(today.year, today.month)[1]),
        "prev_month_start": prev_last.replace(day=1),
        "prev_month_end": prev_last,
        "year_start": today.replace(month=1, day=1),
        "year_end": today.replace(month=12, day=31),
    }[name]
    if sign:
        n = int(amount) * (-1 if sign == "-" else 1)
        if unit == "d":
            base = base + timedelta(days=n)
        elif unit == "w":
            base = base + timedelta(weeks=n)
        else:
            months = n if unit == "m" else 12 * n
            if isinstance(base, datetime):
                base = datetime.combine(_add_months(base.date(), months), base.time())
            else:
                base = _add_months(base, months)
    return base


def default_for(p: ParamDef, ctx: DefaultContext) -> Any:
    """The default of a parameter, typed, or None (an empty list for a multiselect).

    ``default_value`` holds, for every type:

    * a literal in the type's text form: ``A1A``, ``100``, ``1.5``, ``2026-01-31``,
      ``2026-01-31 08:00``, ``true``; a multiselect lists values with commas: ``A1A,B2B``;
    * a date token, with an optional offset in days / weeks / months / years: ``@today``,
      ``@yesterday``, ``@now``, ``@week_start``, ``@month_start``, ``@month_end``,
      ``@prev_month_start``, ``@prev_month_end``, ``@year_start``, ``@year_end``, ``@today-7d``,
      ``@month_start-1m``, ``@year_start+1y``;
    * ``@user.<attribute>``: the viewer's value of a user attribute (``@user.company_code``); a
      multiselect gets all of them; a user with ``*`` or no value gets no default.

    A literal that starts with ``@`` is written ``@@``. Raises ValueError_ for a bad default.
    """
    raw = (p.default_value or "").strip()
    if not raw:
        return [] if p.is_multi else None
    if raw.startswith("@@"):
        return [raw[1:]] if p.is_multi else parse_value(p.data_type, raw[1:])
    if raw.startswith("@"):
        token = raw.lower()
        if (m := DATE_TOKEN_RE.match(token)) is not None:
            if p.data_type not in ("date", "datetime"):
                raise ValueError_(f"default {raw} is for a date or date-time parameter")
            value = _date_token(*m.groups(), ctx)
            if p.data_type == "date":
                value = value.date() if isinstance(value, datetime) else value
            elif not isinstance(value, datetime):
                value = datetime.combine(value, datetime.min.time())
            else:
                value = value.replace(second=0, microsecond=0)
            return [value] if p.is_multi else value
        if (m := USER_TOKEN_RE.match(raw)) is not None:
            values = sorted(v for v in ctx.user_attributes.get(m.group(1), []) if v.strip())
            if not values or "*" in values:
                return [] if p.is_multi else None
            parsed = [parse_value(p.data_type, v) for v in values]
            return parsed if p.is_multi else parsed[0]
        raise ValueError_(f"default {raw} is not a known token (a literal starting with @ is written @@)")
    if p.is_multi:
        return [parse_value(p.data_type, v) for v in raw.split(",") if v.strip()]
    return parse_value(p.data_type, raw)


# --------------------------------------------------------------------------------------------
# Canonical text form (cache key, params_json, logs)

def to_text(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, list):
        return sorted((to_text(v) for v in value), key=lambda v: json.dumps(v, ensure_ascii=False))
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        text = format(value.normalize(), "f")
        return text if text != "-0" else "0"
    if isinstance(value, int):
        return str(value)
    return value


def canonical(values: dict[str, Any]) -> dict[str, Any]:
    return {k: to_text(v) for k, v in sorted(values.items())}


def canonical_json(values: dict[str, Any]) -> str:
    return json.dumps(canonical(values), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# --------------------------------------------------------------------------------------------
# Options of select / multiselect

OptionsLoader = Callable[[ParamDef], list[dict[str, Any]]]


class OptionsCache:
    """Options from ``options_query`` are reused for 60 seconds."""

    TTL = 60.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[tuple, tuple[float, list[dict[str, Any]]]] = {}

    def get(self, key: tuple, load: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        now = _time.monotonic()
        with self._lock:
            hit = self._items.get(key)
            if hit and now - hit[0] < self.TTL:
                return hit[1]
        options = load()
        with self._lock:
            self._items[key] = (now, options)
        return options

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


options_cache = OptionsCache()


def static_options(p: ParamDef) -> list[dict[str, Any]]:
    result = []
    for item in p.options_json or []:
        if isinstance(item, dict) and "value" in item:
            result.append({"value": str(item["value"]), "label": str(item.get("label", item["value"]))})
        else:
            result.append({"value": str(item), "label": str(item)})
    return result


# --------------------------------------------------------------------------------------------
# Validation

def validate(defs: Sequence[ParamDef], submitted: dict[str, Any] | None, ctx: DefaultContext,
             load_options: OptionsLoader | None = None) -> dict[str, Any]:
    """Typed values by parameter name. A parameter that is not submitted takes its default.

    Raises ``PARAM_INVALID`` with ``detail`` = {param name: message} for every bad value.
    """
    submitted = dict(submitted or {})
    errors: dict[str, str] = {}
    values: dict[str, Any] = {}
    known = {p.name for p in defs}
    for name in submitted:
        if name not in known:
            errors[name] = "is not a parameter of this report"

    for p in defs:
        try:
            if p.name not in submitted:
                try:
                    value = default_for(p, ctx)
                except ValueError_ as exc:
                    raise DRSError("REPORT_MISCONFIGURED", admin_detail=f"parameter {p.name}: {exc}") from exc
            else:
                value = _parse_submitted(p, submitted[p.name])
            empty = value is None or value == [] or (p.data_type == "text" and value == "")
            if empty:
                if p.is_required:
                    raise ValueError_("is required")
                values[p.name] = [] if p.is_multi else None
                continue
            for v in value if p.is_multi else [value]:
                _check_bounds(p, v)
            if p.has_options:
                allowed = {o["value"] for o in (load_options(p) if load_options else static_options(p))}
                for v in value if p.is_multi else [value]:
                    if str(to_text(v)) not in allowed:
                        raise ValueError_(f"{to_text(v)!s} is not one of the options")
            values[p.name] = value
        except ValueError_ as exc:
            errors[p.name] = f"{p.label} {exc}"
    if errors:
        raise DRSError("PARAM_INVALID", detail=errors)
    return values


def _parse_submitted(p: ParamDef, raw: Any) -> Any:
    if p.is_multi:
        if raw is None or raw == "":
            return []
        items = raw if isinstance(raw, list) else str(raw).split(",")
        parsed = []
        for item in items:
            if item is None or (isinstance(item, str) and item.strip() == ""):
                continue
            parsed.append(parse_value(p.data_type, item))
        return parsed
    if raw is None or (isinstance(raw, str) and raw.strip() == "" and p.data_type != "text"):
        return None
    if isinstance(raw, list):
        raise ValueError_("must be a single value")
    return parse_value(p.data_type, raw)


def _check_bounds(p: ParamDef, value: Any) -> None:
    if p.data_type == "text":
        if p.max_length is not None and len(value) > p.max_length:
            raise ValueError_(f"must have at most {p.max_length} characters")
        if p.regex and not re.fullmatch(p.regex, value):
            raise ValueError_("has a format that is not accepted")
        return
    for bound, ok, word in ((p.min_value, lambda v, b: v >= b, "at least"),
                            (p.max_value, lambda v, b: v <= b, "at most")):
        if bound not in (None, ""):
            try:
                limit = parse_value(p.data_type, bound)
            except ValueError_ as exc:
                raise DRSError("REPORT_MISCONFIGURED",
                               admin_detail=f"parameter {p.name}: bound {bound!r} {exc}") from exc
            if not ok(value, limit):
                raise ValueError_(f"must be {word} {to_text(limit)}")


def bind_values(defs: Sequence[ParamDef], values: dict[str, Any]) -> dict[str, Any]:
    """Values as they are bound to the query. A multiselect in ``csv`` mode is one string."""
    result = {}
    for p in defs:
        v = values.get(p.name)
        if p.is_multi and p.multi_bind_mode == "csv":
            v = ",".join(str(to_text(x)) for x in v) if v else None
        result[p.name] = v
    return result
