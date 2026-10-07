"""Running a report for a user: the path every route and the CLI take (spec sections 10-12).

    permission -> report definition check -> parameters -> cache (retention, lock) -> source
    -> JSON snapshot -> row filter for this user -> access log
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from drscore.audit import log_access
from drscore.authz.permissions import Access, require_report
from drscore.authz.rowfilter import filter_rows, user_attributes
from drscore.datasources.connectors import SourceInfo
from drscore.db.engine import Database
from drscore.db.models import Datasource, Report, ReportParam, ReportSnapshot, User
from drscore.db.types import utcnow
from drscore.errors import DRSError
from drscore.render import design
from drscore.reports import cache, executor
from drscore.reports.params import (
    DefaultContext,
    ParamDef,
    binds_of,
    canonical,
    canonical_json,
    options_cache,
    parents_of,
    static_options,
    validate,
)
from drscore.settings import AppSettings

log = logging.getLogger(__name__)

# A failure of the source itself; serve_stale_on_error applies to these.
SOURCE_ERRORS = {"DATASOURCE_UNAVAILABLE", "SOURCE_QUERY_FAILED", "SOURCE_TIMEOUT"}
WAIT_POLL_SECONDS = 0.2


@dataclass(frozen=True)
class Prepared:
    report_id: int
    report_code: str
    ttl_seconds: int
    serve_stale_on_error: bool
    timeout_seconds: int
    max_rows: int
    source: SourceInfo
    query_text: str
    defs: tuple[ParamDef, ...]
    values: dict[str, Any]
    params_canonical: dict[str, Any]
    key: str


@dataclass
class Outcome:
    snapshot_id: int
    created_at: datetime
    expires_at: datetime
    cache_hit: bool
    is_stale: bool
    executed: bool  # this request ran the source
    columns: list[dict[str, str]]
    rows: list[list[Any]]  # the full result - filter it before it leaves the server
    params: dict[str, Any]


@dataclass
class Served:
    """A report result as one user may see it."""

    report: dict[str, Any]
    outcome: Outcome
    rows: list[list[Any]]  # row-filtered for the user
    access: Access
    meta: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------------------------
# Definition and parameters

def source_of(session: Session, datasource_id: int | None, what: str) -> SourceInfo:
    ds = session.get(Datasource, datasource_id) if datasource_id is not None else None
    if ds is None:
        raise DRSError("REPORT_MISCONFIGURED", admin_detail=f"{what}: datasource not set or missing")
    if not ds.is_active:
        raise DRSError("REPORT_MISCONFIGURED", admin_detail=f"{what}: datasource {ds.datasource_code} is inactive")
    return SourceInfo.of(ds)


def param_defs(session: Session, report_id: int) -> tuple[ParamDef, ...]:
    return tuple(ParamDef.of(p) for p in session.scalars(
        select(ReportParam).where(ReportParam.report_id == report_id, ReportParam.is_active.is_(True))
        .order_by(ReportParam.sort_order, ReportParam.param_id)))


def options_loader(session: Session, report: Report, settings: AppSettings):
    """Loads the options of a select / multiselect parameter (static or from options_query).

    An options query may use the parameters placed before it as binds: its options are then those
    of the values chosen there (a company, then the units of that company). A parameter nothing is
    chosen for is bound as NULL - what the list shows then is what the query returns for NULL."""
    defs = param_defs(session, report.report_id)
    order = {d.name: i for i, d in enumerate(defs)}

    def load(p: ParamDef, values: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if not p.options_query:
            return static_options(p)
        src = source_of(session, p.options_datasource_id or report.datasource_id,
                        f"options of parameter {p.name}")
        for b in binds_of(p.options_query):
            if b not in order:
                raise DRSError("REPORT_MISCONFIGURED", admin_detail=(
                    f"options of parameter {p.name}: the query uses :{b}, which is not an active parameter "
                    "of the report"))
            if order[b] >= order.get(p.name, -1):
                raise DRSError("REPORT_MISCONFIGURED", admin_detail=(
                    f"options of parameter {p.name}: the query uses :{b}, which must be placed before "
                    f"{p.name} (order)"))
        parents = [d for d in defs if d.name in parents_of(p, defs)]
        bound = {d.name: (values or {}).get(d.name, [] if d.is_multi else None) for d in parents}
        key = (src, p.options_query, tuple((k, repr(v)) for k, v in bound.items()))

        def run() -> list[dict[str, Any]]:
            result = executor.run_query(src, p.options_query, parents, bound, timeout_seconds=30,
                                        max_rows=10000, max_bytes=10 * 1024 * 1024, tz=settings.tz)
            if len(result.columns) < 1:
                raise DRSError("REPORT_MISCONFIGURED", admin_detail=f"options of {p.name}: no column")
            return [{"value": "" if r[0] is None else str(r[0]),
                     "label": str(r[1] if len(r) > 1 and r[1] is not None else r[0])} for r in result.rows]

        return options_cache.get(key, run)
    return load


def check_definition(report: Report, defs: tuple[ParamDef, ...]) -> None:
    """The report rows are consistent enough to run (REPORT_MISCONFIGURED otherwise)."""
    if report.datasource_id is None or not report.query_text:
        raise DRSError("REPORT_MISCONFIGURED", admin_detail="the report needs datasource_id and query_text to read data")
    unknown = [b for b in binds_of(report.query_text or "") if b not in {p.name for p in defs}]
    if unknown:
        raise DRSError("REPORT_MISCONFIGURED",
                       admin_detail=f"the query uses :{', :'.join(unknown)}, which is not an active parameter of the report")


def default_context(settings: AppSettings, user_attributes: dict[str, list[str]] | None = None,
                    now: datetime | None = None) -> DefaultContext:
    local = (now or utcnow()).astimezone(settings.tz).replace(tzinfo=None)
    return DefaultContext(now=local, user_attributes=user_attributes or {})


def prepare(session: Session, report: Report, raw_params: dict[str, Any] | None, settings: AppSettings,
            now: datetime | None = None, user_attributes: dict[str, list[str]] | None = None) -> Prepared:
    defs = param_defs(session, report.report_id)
    check_definition(report, defs)
    src = source_of(session, report.datasource_id, f"report {report.report_code}")
    ctx = default_context(settings, user_attributes, now)
    values = validate(defs, raw_params, ctx, options_loader(session, report, settings))
    limits = settings.limits
    return Prepared(
        report_id=report.report_id,
        report_code=report.report_code,
        ttl_seconds=max(0, report.cache_ttl_seconds or 0),
        serve_stale_on_error=report.serve_stale_on_error,
        timeout_seconds=min(report.timeout_seconds or limits.default_timeout_seconds, limits.hard_timeout_seconds),
        max_rows=min(report.max_rows or limits.default_max_rows, limits.hard_max_rows),
        source=src,
        query_text=report.query_text,
        defs=defs,
        values=values,
        params_canonical=canonical(values),
        key=cache.cache_key(src.code, report.query_text, canonical_json(values)),
    )


# --------------------------------------------------------------------------------------------
# Cache, lock, source

def _outcome(snap, *, cache_hit: bool, is_stale: bool = False, executed: bool = False) -> Outcome:
    return Outcome(snap.snapshot_id, snap.created_at, snap.expires_at, cache_hit, is_stale, executed,
                   snap.columns_json, snap.rows_json, snap.params_json)


def get_result(database: Database, prep: Prepared, settings: AppSettings, *, refresh: bool = False,
               username: str | None = None) -> Outcome:
    """The result of a report for these parameters.

    A snapshot inside its retention is served as it is. Otherwise the request takes the lock of
    (report, parameters), moves the old snapshots to the history, runs the source once and saves
    the new snapshot. Identical requests arriving meanwhile wait for that snapshot.
    """
    cfg = settings.cache
    if not refresh:
        with database.session() as s:
            snap = cache.find_valid(s, prep.report_id, prep.key, utcnow())
            if snap is not None:
                return _outcome(snap, cache_hit=True)

    with database.session() as s:
        marker = cache.newest_id(s, prep.report_id, prep.key)
    owner = cache.lock_owner()
    deadline = time.monotonic() + cfg.lock_wait_seconds
    while not cache.try_lock(database, prep.report_id, prep.key, owner, cfg.lock_stale_seconds):
        time.sleep(WAIT_POLL_SECONDS)
        with database.session() as s:  # another request is reading the data: use its snapshot
            snap = cache.newer_than(s, prep.report_id, prep.key, marker)
            if snap is not None:
                return _outcome(snap, cache_hit=True)
        if time.monotonic() > deadline:
            raise DRSError("BUSY")

    try:
        now = utcnow()
        with database.session() as s:
            if not refresh:  # finished by another request between our check and the lock
                snap = cache.find_valid(s, prep.report_id, prep.key, now)
                if snap is not None:
                    return _outcome(snap, cache_hit=True)
            snap = cache.newer_than(s, prep.report_id, prep.key, marker) if refresh else None
            if snap is not None:  # a refresh that another request has just done
                return _outcome(snap, cache_hit=True)
            cache.archive_key(s, prep.report_id, prep.key, now, only_expired=not refresh)

        try:
            result = executor.run_query(
                prep.source, prep.query_text, prep.defs, prep.values,
                timeout_seconds=prep.timeout_seconds, max_rows=prep.max_rows,
                max_bytes=settings.limits.max_snapshot_mb * 1024 * 1024, tz=settings.tz)
        except DRSError as exc:
            if exc.code in SOURCE_ERRORS and prep.serve_stale_on_error:
                with database.session() as s:
                    old = cache.newest_kept(s, prep.report_id, prep.key)
                    if old is not None:
                        log.warning("serving stale snapshot %s of %s: %s", old.snapshot_id, prep.report_code, exc)
                        return _outcome(old, cache_hit=False, is_stale=True)
            raise

        created = utcnow()
        with database.session() as s:
            snap = ReportSnapshot(
                report_id=prep.report_id, report_code=prep.report_code, cache_key=prep.key,
                params_json=prep.params_canonical, columns_json=result.columns, rows_json=result.rows,
                row_count=len(result.rows), byte_size=result.byte_size,
                source_duration_ms=result.duration_ms, created_at=created,
                expires_at=created + timedelta(seconds=prep.ttl_seconds), created_by=username,
            )
            s.add(snap)
            s.flush()
            return _outcome(snap, cache_hit=False, executed=True)
    finally:
        cache.release_lock(database, prep.report_id, prep.key, owner)


# --------------------------------------------------------------------------------------------
# For a user

def run_for_user(database: Database, settings: AppSettings, username: str, report_code: str,
                 raw_params: dict[str, Any] | None = None, *, refresh: bool = False,
                 client_ip: str | None = None, view: str | None = None) -> Served:
    """Runs (or reads from the cache) a report as this user would get it, with the user's
    permission check and row filter, and writes the access log.

    ``view`` (grid / html; the report's default when None) is checked first: a view the report
    does not offer, or whose design is missing, never reaches the source. Every view of a report
    reads the same snapshot."""
    started = time.perf_counter()
    canonical_params: Any = raw_params
    try:
        with database.session() as s:
            user = s.scalars(select(User).where(User.username == username)).one_or_none()
            if user is None or not user.is_active:
                raise DRSError("AUTH_REQUIRED")
            report, access = require_report(s, user, report_code)
            if refresh and not access.can_refresh:
                raise DRSError("REFRESH_NOT_ALLOWED")
            chosen = design.require_view(report, view)
            design.check(s, report, settings, chosen)  # no design, no view - and no source run
            prep = prepare(s, report, raw_params, settings, user_attributes=user_attributes(s, user))
            canonical_params = prep.params_canonical
            report_info = {"code": report.report_code, "name": report.report_name, "type": report.report_type,
                           "group": report.group.group_code, "description": report.description}

        outcome = get_result(database, prep, settings, refresh=refresh, username=username)

        with database.session() as s:
            user = s.get(User, user.user_id)
            rows = filter_rows(s, user, prep.report_id, [c["name"] for c in outcome.columns], outcome.rows)
    except DRSError as exc:
        status = "DENIED" if exc.code in ("FORBIDDEN", "REFRESH_NOT_ALLOWED", "AUTH_REQUIRED") else "ERROR"
        log_access(username, "RUN", status, report_code=report_code, params_json=canonical_params, view_key=view,
                   error_code=exc.code, error_message=exc.admin_detail or exc.message,
                   duration_ms=int((time.perf_counter() - started) * 1000), client_ip=client_ip)
        raise

    log_access(username, "RUN" if outcome.executed else "VIEW", "OK", report_code=report_code, view_key=chosen,
               params_json=outcome.params, snapshot_id=outcome.snapshot_id, cache_hit=outcome.cache_hit,
               row_count=len(rows), duration_ms=int((time.perf_counter() - started) * 1000), client_ip=client_ip)
    return Served(report_info, outcome, rows, access)
