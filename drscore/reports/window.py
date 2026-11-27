"""When a report may read its source on a schedule: the time window (``drs_report_schedule.time_window``).

The fields, their names and their meaning are those of dbabrain's ``time_window`` (its
``dbabrain/lib/time_window.py``), so that ``from_hour: 1`` or ``repeat_interval: 72000`` means the same
thing in both tools:

* ten **bounds** - ``from_``/``to_`` ``year``, ``month``, ``day`` (of the month), ``hour``, ``minute`` - read
  on the DRS clock (``[app] timezone``), every one inclusive and optional (``null`` = no restriction); a
  pair whose ``from`` is greater than its ``to`` wraps (``from_hour 22, to_hour 4`` spans midnight);
* ``weekdays`` - a set of ISO weekdays, 1 Monday .. 7 Sunday; absent = every day, ``[]`` = never;
* three **intervals** in seconds, each measured from the previous run's **start**: ``repeat_interval``
  (``0`` = once, ``-1`` = manual: only a forced run), ``retry_interval`` after a failed run (default: the
  repeat interval), ``timeout`` (default: the report's own timeout).

"Once a night between 01:00 and 05:00" is ``from_hour 1, to_hour 4`` plus ``repeat_interval 72000``: an
interval well under a day, so the due moment does not walk out of the window (with 86400 a run that
started at 01:00:40 is due at 01:00:40 the next night, and a slow sweep pushes it later every night).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from typing import Any

RUN_ONCE = 0
MANUAL_ONLY = -1
WEEKDAY_NAMES = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
BOUNDS = ("year", "month", "day", "hour", "minute")
INTERVALS = ("repeat_interval", "retry_interval", "timeout")
# How early a run may start, so the scheduler's polling does not cost a whole cycle (as dbabrain).
DUE_GRACE_FRACTION = 0.05
DUE_GRACE_MAX_SECONDS = 30


class WindowError(ValueError):
    """A time window that cannot be used; the message names the field."""


@dataclass(frozen=True)
class TimeWindow:
    from_year: int | None = None
    to_year: int | None = None
    from_month: int | None = None
    to_month: int | None = None
    from_day: int | None = None
    to_day: int | None = None
    from_hour: int | None = None
    to_hour: int | None = None
    from_minute: int | None = None
    to_minute: int | None = None
    repeat_interval: int | None = None
    retry_interval: int | None = None
    timeout: int | None = None
    weekdays: tuple[int, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        """The fields that are set, as stored (``weekdays`` as a list; ``[]`` kept: it means never)."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is not None:
                out[f.name] = list(value) if f.name == "weekdays" else value
        return out

    def text(self) -> str:
        """The window as a person reads it: ``01:00-04:59 Mon,Tue every 72000 s``."""
        parts: list[str] = []
        if self.weekdays is not None:
            parts.append(",".join(WEEKDAY_NAMES[d] for d in sorted(self.weekdays)) or "no day")
        if self.from_hour is not None or self.to_hour is not None or self.from_minute is not None \
                or self.to_minute is not None:
            start = f"{self.from_hour or 0:02d}:{self.from_minute or 0:02d}"
            end = f"{23 if self.to_hour is None else self.to_hour:02d}:{59 if self.to_minute is None else self.to_minute:02d}"
            parts.append(f"{start}-{end}")
        for name in ("day", "month", "year"):
            lo, hi = getattr(self, f"from_{name}"), getattr(self, f"to_{name}")
            if lo is not None or hi is not None:
                parts.append(f"{name} {'' if lo is None else lo}-{'' if hi is None else hi}")
        if self.repeat_interval == MANUAL_ONLY:
            parts.append("manual")
        elif self.repeat_interval == RUN_ONCE:
            parts.append("once")
        elif self.repeat_interval is not None:
            parts.append(f"every {self.repeat_interval} s")
        return " ".join(parts) or "any time"


def _int(value: Any, name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise WindowError(f"{name} must be a whole number, not {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise WindowError(f"{name} must be a whole number, not {value!r}") from exc


def _weekdays(value: Any) -> tuple[int, ...] | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = [v for v in value.replace(" ", "").split(",") if v]
    if not isinstance(value, (list, tuple)):
        raise WindowError("weekdays must be a list of ISO weekdays 1-7 (1 Monday .. 7 Sunday)")
    days: list[int] = []
    for item in value:
        day = _int(item, "weekdays")
        if day not in WEEKDAY_NAMES:
            extra = " (0 is cron's Sunday; here Sunday is 7)" if day == 0 else ""
            raise WindowError(f"weekdays: {item!r} is not an ISO weekday 1-7{extra}")
        if day in days:
            raise WindowError(f"weekdays lists {day} twice")
        days.append(day)
    return tuple(days)


def parse(raw: Any) -> TimeWindow:
    """A window from its stored or typed form (a dict, or JSON already decoded). Unknown fields, negative
    values and wrong weekdays are refused rather than dropped: a dropped value changes when a report runs."""
    if raw in (None, ""):
        return TimeWindow()
    if not isinstance(raw, dict):
        raise WindowError("the time window must be an object of dbabrain time_window fields")
    known = {f.name for f in fields(TimeWindow)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise WindowError(f"unknown field(s) {', '.join(unknown)}; known: {', '.join(sorted(known))}")
    values: dict[str, Any] = {}
    for name in BOUNDS:
        for prefix in ("from", "to"):
            key = f"{prefix}_{name}"
            v = _int(raw.get(key), key)
            if v is not None and v < 0:
                raise WindowError(f"{key} must be >= 0")
            values[key] = v
    for key in INTERVALS:
        v = _int(raw.get(key), key)
        if v is not None and v < 0 and not (key == "repeat_interval" and v == MANUAL_ONLY):
            raise WindowError(f"{key} must be >= 0" + (" (or -1: manual)" if key == "repeat_interval" else ""))
        values[key] = v
    values["weekdays"] = _weekdays(raw.get("weekdays"))
    return TimeWindow(**values)


def closed_reason(window: TimeWindow | None, local_now: datetime) -> str:
    """The first dimension that closes the window at ``local_now`` (on the DRS clock); "" when open."""
    if window is None:
        return ""
    if window.weekdays is not None:
        if not window.weekdays:
            return "no weekday is allowed"
        if local_now.isoweekday() not in window.weekdays:
            return f"weekday {local_now.isoweekday()} is not allowed"
    for name in BOUNDS:
        value = getattr(local_now, name)
        lo, hi = getattr(window, f"from_{name}"), getattr(window, f"to_{name}")
        if lo is not None and hi is not None and lo > hi:  # wraps (22 -> 4 spans midnight)
            if not (value >= lo or value <= hi):
                return f"{name} {value} is outside {lo}-{hi}"
        elif (lo is not None and value < lo) or (hi is not None and value > hi):
            return f"{name} {value} is outside {'' if lo is None else lo}-{'' if hi is None else hi}"
    return ""


def is_open(window: TimeWindow | None, local_now: datetime) -> bool:
    return not closed_reason(window, local_now)


def _grace(interval: int) -> float:
    return 0.0 if interval <= 0 else min(interval * DUE_GRACE_FRACTION, DUE_GRACE_MAX_SECONDS, float(interval))


def interval_due(last_start: datetime | None, interval: int | None, now: datetime) -> bool:
    """Whether the interval since the last start has passed: -1 never, no start yet always, 0 never again."""
    if interval == MANUAL_ONLY:
        return False
    if last_start is None:
        return True
    if interval is None or interval == RUN_ONCE:
        return False
    return last_start + timedelta(seconds=interval - _grace(interval)) <= now


def due(window: TimeWindow, *, last_start: datetime | None, last_failed: bool, now: datetime,
        local_now: datetime) -> bool:
    """Open now, and its interval (the retry interval after a failed run) has passed since the last start."""
    if window.repeat_interval == MANUAL_ONLY or not is_open(window, local_now):
        return False
    if last_failed and last_start is not None:
        retry = window.retry_interval if window.retry_interval is not None else window.repeat_interval
        if retry == RUN_ONCE and window.retry_interval is None:
            return True  # a run-once that failed is tried again on the next look
        return interval_due(last_start, retry, now)
    return interval_due(last_start, window.repeat_interval, now)
