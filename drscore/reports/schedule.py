"""Reports read again on a schedule, inside a time window (``drs_report_schedule``, ``[schedule]``).

Without a schedule DRS reads a report's source only when someone opens the report after its snapshot has
expired - at whatever hour that is. A schedule makes the report read its source by itself, with its
default parameters, when its window is open and its interval has passed (``drscore.reports.window``:
dbabrain's time-window fields), and keeps the snapshot for the report's retention. ``live_runs 'window'``
also keeps the readers to the window: outside it they get the newest snapshot kept and never make the
report read its source (``reports.service.get_result``).

``serve`` looks for due schedules every ``[schedule] poll_seconds`` in one background thread per process,
and runs them one after the other; a schedule is claimed in the DRS database before it runs, so two
processes (or two servers on one database) never run the same report at once.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from drscore.audit import audit, log_access
from drscore.db.engine import Database
from drscore.db.models import LIVE_RUNS, Report, ReportGroup, ReportSchedule
from drscore.db.types import utcnow
from drscore.errors import DRSError
from drscore.mirror.runner import owner_name
from drscore.reports import window
from drscore.settings import AppSettings

log = logging.getLogger(__name__)

RUN_USER = "scheduler"  # the access log's user of a scheduled run


@dataclass
class RunResult:
    report_code: str
    ok: bool
    rows: int | None = None
    duration_ms: int | None = None
    snapshot_id: int | None = None
    error: str | None = None


# --------------------------------------------------------------------------------------------
# Definition

def _report(session: Session, code: str) -> Report:
    report = session.scalars(select(Report).where(Report.report_code == code)).one_or_none()
    if report is None:
        raise DRSError("REPORT_NOT_FOUND", admin_detail=f"no report {code}")
    return report


def set_schedule(session: Session, code: str, raw_window: dict[str, Any], *, live_runs: str | None, actor: str,
                 settings: AppSettings) -> ReportSchedule:
    """Creates or replaces a report's schedule. The window must say how often (``repeat_interval``); a
    timeout above the hard limit, or a claim shorter than the timeout, is refused."""
    try:
        w = window.parse(raw_window)
    except window.WindowError as exc:
        raise DRSError("PARAM_INVALID", detail={"time_window": str(exc)}, admin_detail=str(exc)) from exc
    if w.repeat_interval is None:
        raise DRSError("PARAM_INVALID", detail={"time_window": "repeat_interval is required (seconds; 0 once, -1 manual)"},
                       admin_detail="repeat_interval is required")
    if w.timeout is not None and w.timeout > settings.limits.hard_timeout_seconds:
        raise DRSError("PARAM_INVALID", detail={"time_window": (
            f"timeout {w.timeout} s is above [limits] hard_timeout_seconds ({settings.limits.hard_timeout_seconds})")},
            admin_detail="timeout above the hard limit")
    if live_runs is not None and live_runs not in LIVE_RUNS:
        raise DRSError("PARAM_INVALID", detail={"live_runs": f"one of {', '.join(LIVE_RUNS)}"})
    report = _report(session, code)
    sched = session.get(ReportSchedule, report.report_id)
    before = _definition(sched) if sched is not None else None
    if sched is None:
        sched = ReportSchedule(report_id=report.report_id, time_window=w.to_dict(), live_runs=live_runs or "always")
        session.add(sched)
    else:
        sched.time_window = w.to_dict()
        if live_runs is not None:
            sched.live_runs = live_runs
        sched.is_active = True
    sched.updated_by = actor
    session.flush()
    audit(session, actor, "SCHEDULE_SET", "report", code, before=before, after=_definition(sched))
    return sched


def set_active(session: Session, code: str, active: bool, *, actor: str) -> ReportSchedule:
    report = _report(session, code)
    sched = session.get(ReportSchedule, report.report_id)
    if sched is None:
        raise DRSError("REPORT_NOT_FOUND", admin_detail=f"{code} has no schedule")
    sched.is_active = active
    sched.updated_by = actor
    audit(session, actor, "SCHEDULE_ON" if active else "SCHEDULE_OFF", "report", code)
    return sched


def _definition(sched: ReportSchedule) -> dict[str, Any]:
    return {"time_window": sched.time_window, "live_runs": sched.live_runs, "is_active": sched.is_active}


def describe(sched: ReportSchedule, settings: AppSettings, now: datetime | None = None) -> dict[str, Any]:
    """A schedule for a listing: its window as text, whether it is open and due now, the last run."""
    now = now or utcnow()
    local = now.astimezone(settings.tz)
    try:
        w = window.parse(sched.time_window)
        text, reason = w.text(), window.closed_reason(w, local)
        is_due = sched.is_active and window.due(w, last_start=sched.last_started_at,
                                                last_failed=sched.last_status == "error", now=now, local_now=local)
    except window.WindowError as exc:
        text, reason, is_due = f"invalid: {exc}", str(exc), False
    return {
        "report": sched.report.report_code, "window": text, "time_window": sched.time_window,
        "live_runs": sched.live_runs, "active": sched.is_active, "open": not reason, "closed_reason": reason,
        "due": is_due, "last_started_at": sched.last_started_at, "last_finished_at": sched.last_finished_at,
        "last_status": sched.last_status, "last_rows": sched.last_rows, "last_duration_ms": sched.last_duration_ms,
        "last_snapshot_id": sched.last_snapshot_id, "last_error": sched.last_error,
    }


# --------------------------------------------------------------------------------------------
# Claim

def claim(database: Database, report_id: int, owner: str, lock_minutes: int) -> bool:
    """Marks the schedule as running for ``owner``. False when another run holds it."""
    now = utcnow()
    with database.session() as s:
        done = s.execute(
            update(ReportSchedule)
            .where(ReportSchedule.report_id == report_id,
                   or_(ReportSchedule.lock_until.is_(None), ReportSchedule.lock_until < now))
            .values(lock_owner=owner, lock_until=now + timedelta(minutes=lock_minutes),
                    last_status="running", last_started_at=now)
            .execution_options(synchronize_session=False))
        return done.rowcount == 1


def _finish(database: Database, report_id: int, owner: str, **values: Any) -> None:
    with database.session() as s:
        s.execute(update(ReportSchedule)
                  .where(ReportSchedule.report_id == report_id, ReportSchedule.lock_owner == owner)
                  .values(lock_owner=None, lock_until=None, last_finished_at=utcnow(), **values)
                  .execution_options(synchronize_session=False))


# --------------------------------------------------------------------------------------------
# Run

def run_one(database: Database, settings: AppSettings, code: str, *, owner: str | None = None,
            actor: str = RUN_USER) -> RunResult:
    """Runs one scheduled report now, whatever its window says (the scheduler calls it for a due one;
    ``report schedule run`` forces it): the source is read with the default parameters, the new snapshot
    is kept for the report's retention and loaded into its BI dataset."""
    from drscore.bi import dataset as bi_dataset
    from drscore.reports.service import get_result, prepare

    owner = owner or owner_name()
    with database.session() as s:
        report = _report(s, code)
        sched = s.get(ReportSchedule, report.report_id)
        if sched is None:
            raise DRSError("REPORT_NOT_FOUND", admin_detail=f"{code} has no schedule")
        report_id, table = report.report_id, report.bi_dataset_table
        w = window.parse(sched.time_window)
    if not claim(database, report_id, owner, settings.schedule.lock_minutes):
        return RunResult(code, False, error="another run of this schedule is in progress")
    started = time.perf_counter()
    try:
        with database.session() as s:
            prep = prepare(s, s.get(Report, report_id), {}, settings)
        if w.timeout:
            prep = dataclasses.replace(prep, timeout_seconds=min(w.timeout, settings.limits.hard_timeout_seconds))
        outcome = get_result(database, prep, settings, refresh=True, username=actor, scheduled=True)
        if table:
            bi_dataset.refresh(database, report_id, table, outcome)
    except Exception as exc:  # noqa: BLE001 - a failed run is recorded and the next schedule runs
        ms = int((time.perf_counter() - started) * 1000)
        if isinstance(exc, DRSError):
            message = f"{exc.code}: {exc.admin_detail or exc.message}"
            if exc.code == "PARAM_INVALID":
                message = "PARAM_INVALID: a required parameter has no default (a schedule runs with the defaults)"
        else:
            log.exception("scheduled run of %s failed", code)
            message = f"{type(exc).__name__}: {exc}"
        _finish(database, report_id, owner, last_status="error", last_error=message[:2000], last_duration_ms=ms)
        log_access(actor, "RUN", "ERROR", report_code=code, view_key="schedule",
                   error_code=getattr(exc, "code", "SCHEDULE_FAILED"), error_message=message[:2000], duration_ms=ms)
        log.warning("scheduled run of %s failed: %s", code, message)
        return RunResult(code, False, duration_ms=ms, error=message)
    ms = int((time.perf_counter() - started) * 1000)
    _finish(database, report_id, owner, last_status="ok", last_error=None, last_rows=len(outcome.rows),
            last_duration_ms=ms, last_snapshot_id=outcome.snapshot_id)
    log_access(actor, "RUN", "OK", report_code=code, view_key="schedule", params_json=outcome.params,
               snapshot_id=outcome.snapshot_id, cache_hit=False, row_count=len(outcome.rows), duration_ms=ms)
    log.info("scheduled run of %s: %d row(s) in %d ms, snapshot %s", code, len(outcome.rows), ms, outcome.snapshot_id)
    return RunResult(code, True, rows=len(outcome.rows), duration_ms=ms, snapshot_id=outcome.snapshot_id)


def due(database: Database, settings: AppSettings, now: datetime | None = None) -> list[str]:
    """The codes of the active schedules (of active reports, in active groups) that are open and due."""
    now = now or utcnow()
    local = now.astimezone(settings.tz)
    codes = []
    with database.session() as s:
        rows = s.execute(
            select(ReportSchedule, Report.report_code).join(Report, Report.report_id == ReportSchedule.report_id)
            .join(ReportGroup, ReportGroup.group_id == Report.group_id)
            .where(ReportSchedule.is_active.is_(True), Report.is_active.is_(True), ReportGroup.is_active.is_(True),
                   or_(ReportSchedule.lock_until.is_(None), ReportSchedule.lock_until < now))
            .order_by(Report.report_code)).all()
        for sched, code in rows:
            try:
                w = window.parse(sched.time_window)
            except window.WindowError as exc:
                log.warning("schedule of %s is not valid, skipped: %s", code, exc)
                continue
            if window.due(w, last_start=sched.last_started_at, last_failed=sched.last_status == "error",
                          now=now, local_now=local):
                codes.append(code)
    return codes


def run_due(database: Database, settings: AppSettings, owner: str | None = None) -> list[RunResult]:
    """Runs every schedule that is due, one after the other; a failure is recorded and the next runs."""
    owner = owner or owner_name()
    return [run_one(database, settings, code, owner=owner) for code in due(database, settings)]


class ReportScheduler:
    """The background thread of ``serve`` that runs the report schedules that are due ([schedule] enabled)."""

    def __init__(self, database: Database, settings: AppSettings):
        self.database = database
        self.settings = settings
        self.owner = owner_name()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="drs-schedule", daemon=True)
            self._thread.start()
            log.info("report scheduler started (every %d s)", self.settings.schedule.poll_seconds)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # The first look waits one interval: the server starts and serves before it reads any source.
        while not self._stop.wait(self.settings.schedule.poll_seconds):
            try:
                run_due(self.database, self.settings, owner=self.owner)
            except Exception:  # the DRS database unreachable for a moment: try again later
                log.exception("report scheduler: the due schedules could not be run")
