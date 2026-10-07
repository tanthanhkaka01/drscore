"""What the portal shows of a report: its definition, a grid answer, an export file.

Every function repeats the permission check and applies the user's row filter: the routes only
translate HTTP to these calls.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import select

from drs.audit import log_access
from drs.authz.permissions import Access, require_report
from drs.authz.rowfilter import filter_rows, user_attributes
from drs.db.engine import Database
from drs.db.models import Report, User
from drs.db.types import utcnow
from drs.errors import DRSError
from drs.export import writers
from drs.bi import dataset as bi_dataset
from drs.bi import superset
from drs.render import design, html
from drs.render.grid import grid_columns, project
from drs.reports import cache
from drs.reports.params import ValueError_, default_for, to_text
from drs.reports.service import Served, default_context, options_loader, param_defs, run_for_user
from drs.settings import AppSettings
from drs.web.schemas import (
    ErrorBody,
    GridColumnInfo,
    ParamInfo,
    ParamOption,
    ReportDefinition,
    ReportInfo,
    RunResponse,
    SnapshotInfo,
    ViewInfo,
)

EXPORT_MEDIA = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
}


def local_time(value: datetime, settings: AppSettings, fmt: str = "%d/%m/%Y %H:%M:%S") -> str:
    return value.astimezone(settings.tz).strftime(fmt)


def _user(session, username: str) -> User:
    user = session.scalars(select(User).where(User.username == username)).one_or_none()
    if user is None or not user.is_active:
        raise DRSError("AUTH_REQUIRED")
    return user


def report_info(report: Report) -> ReportInfo:
    return ReportInfo(code=report.report_code, name=report.report_name, type=report.report_type,
                      group=report.group.group_code, group_name=report.group.group_name,
                      description=report.description)


# --------------------------------------------------------------------------------------------
# Definition (the parameter form)

def _input_text(value: Any, data_type: str) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(to_text(v)) for v in value]
    if data_type == "datetime":
        return value.strftime("%Y-%m-%dT%H:%M")  # the form of <input type="datetime-local">
    if data_type == "bool":
        return "true" if value else "false"
    return str(to_text(value))


def _default(p, ctx):
    try:
        return default_for(p, ctx)
    except ValueError_ as exc:
        raise DRSError("REPORT_MISCONFIGURED", admin_detail=f"parameter {p.name}: {exc}") from exc


def view_states(session, report: Report, user: User, settings: AppSettings) -> list[ViewInfo]:
    """Every tab of the report with its state. A tab whose design is missing or broken is still
    listed (no design, no view: it shows the error); the others work."""
    states = []
    for key in design.available_views(report):
        try:
            d = design.check(session, report, settings, key)
            states.append(ViewInfo(key=key, ok=True, bi=bi_payload(d, settings) if key == "bi" else None))
        except DRSError as exc:
            states.append(ViewInfo(key=key, ok=False, error=ErrorBody(
                code=exc.code, message=exc.message, detail=exc.admin_detail if user.is_admin else None)))
    return states


def definition(database: Database, settings: AppSettings, username: str, code: str) -> ReportDefinition:
    with database.session() as s:
        user = _user(s, username)
        report, access = require_report(s, user, code)
        ctx = default_context(settings, user_attributes(s, user))
        load = options_loader(s, report, settings)
        params = []
        for p in param_defs(s, report.report_id):
            params.append(ParamInfo(
                name=p.name, label=p.label, data_type=p.data_type, input_kind=p.input_kind,
                required=p.is_required, default=_input_text(_default(p, ctx), p.data_type),
                options=[ParamOption(**o) for o in load(p)] if p.has_options else None,
                min_value=p.min_value, max_value=p.max_value, max_length=p.max_length,
            ))
        views = view_states(s, report, user, settings)
        if not views:
            raise DRSError("REPORT_MISCONFIGURED", admin_detail="the report has no query and no design: nothing to show")
        default = design.default_view(report)
        auto_run = all(not p.required or p.default not in (None, "", []) for p in params)
        has_grid = "grid" in design.available_views(report)
        return ReportDefinition(report=report_info(report), params=params,
                                can_export=access.can_export and has_grid, can_refresh=access.can_refresh,
                                auto_run=auto_run, views=views, default_view=default)


# --------------------------------------------------------------------------------------------
# Run, and the views of a snapshot

def _snapshot_info(o, settings: AppSettings) -> SnapshotInfo:
    return SnapshotInfo(id=o.snapshot_id, created_at=o.created_at, expires_at=o.expires_at,
                        created_at_display=local_time(o.created_at, settings),
                        cache_hit=o.cache_hit, is_stale=o.is_stale)


def _grid_response(session, report: Report, settings: AppSettings, outcome, rows) -> RunResponse:
    columns = [c for c in grid_columns(session, report.report_id, outcome.columns) if c.visible]
    return RunResponse(report=report_info(report), snapshot=_snapshot_info(outcome, settings), view="grid",
                       params=outcome.params, columns=[GridColumnInfo(**c.to_json()) for c in columns],
                       rows=project(rows, columns), row_count=len(rows))


def html_url(code: str, snapshot_id: int) -> str:
    return f"/reports/{code}/html?snapshot_id={snapshot_id}"


def bi_payload(d: design.Design, settings: AppSettings) -> dict[str, Any]:
    if d.kind == "bi_link":
        return {"mode": "LINK", "url": d.url}
    return {"mode": "EMBED", "superset_url": settings.bi.superset.base_url, "dashboard_id": d.dashboard_id}


def open_design(database: Database, settings: AppSettings, username: str, code: str,
                view: str) -> design.Design:
    """Permission, the view, then the design check of section 13."""
    with database.session() as s:
        report, _ = require_report(s, _user(s, username), code)
        return design.check(s, report, settings, design.require_view(report, view))


def run(database: Database, settings: AppSettings, username: str, code: str, params: dict[str, Any],
        refresh: bool, client_ip: str | None, view: str | None = None) -> RunResponse:
    """Runs the report (or reads its snapshot) for one view. The other views of the same
    snapshot are then read without running again (``grid_of_snapshot``, the HTML route)."""
    with database.session() as s:
        report, _ = require_report(s, _user(s, username), code)
        chosen = design.require_view(report, view)
        info = report_info(report)
    if chosen == "bi":
        return bi_answer(database, settings, username, code, info, client_ip)
    served = run_for_user(database, settings, username, code, params, refresh=refresh, client_ip=client_ip,
                          view=chosen)
    if chosen == "grid":
        with database.session() as s:
            report = s.scalars(select(Report).where(Report.report_code == code)).one()
            return _grid_response(s, report, settings, served.outcome, served.rows)
    o = served.outcome
    return RunResponse(report=info, snapshot=_snapshot_info(o, settings), view="html", params=o.params,
                       row_count=len(served.rows), html_url=html_url(code, o.snapshot_id))


@dataclass
class _Stored:
    """A stored snapshot seen like a run outcome."""

    snapshot_id: int
    created_at: datetime
    expires_at: datetime
    columns: list
    params: dict
    cache_hit: bool = True
    is_stale: bool = False


def grid_of_snapshot(database: Database, settings: AppSettings, username: str, code: str, snapshot_id: int,
                     client_ip: str | None) -> RunResponse:
    """The grid of a snapshot already on screen in another tab: same rows, no new run."""
    try:
        with database.session() as s:
            user = _user(s, username)
            report, _ = require_report(s, user, code)
            design.require_view(report, "grid")
            snap = cache.by_id(s, report.report_id, snapshot_id)
            if snap is None:
                raise DRSError("SNAPSHOT_GONE")
            rows = filter_rows(s, user, report.report_id, [c["name"] for c in snap.columns_json], snap.rows_json)
            stored = _Stored(snap.snapshot_id, snap.created_at, snap.expires_at, snap.columns_json,
                             snap.params_json, is_stale=snap.expires_at <= utcnow())
            answer = _grid_response(s, report, settings, stored, rows)
    except DRSError as exc:
        log_access(username, "VIEW", "DENIED" if exc.code == "FORBIDDEN" else "ERROR", report_code=code,
                   snapshot_id=snapshot_id, error_code=exc.code, error_message=exc.admin_detail or exc.message,
                   client_ip=client_ip)
        raise
    log_access(username, "VIEW", "OK", report_code=code, snapshot_id=snapshot_id, row_count=answer.row_count,
               client_ip=client_ip, view_key="grid", params_json=answer.params)
    return answer


def bi_answer(database: Database, settings: AppSettings, username: str, code: str, info: ReportInfo,
              client_ip: str | None) -> RunResponse:
    """The dashboard view. A report with ``bi_dataset_table`` first brings its dataset up to date
    (spec 14.3 step 5): the report runs (or reads its snapshot, by retention) and the snapshot is
    loaded into the table the dashboard reads."""
    try:
        d = open_design(database, settings, username, code, "bi")
        with database.session() as s:
            report = s.scalars(select(Report).where(Report.report_code == code)).one()
            report_id, table = report.report_id, report.bi_dataset_table
            if table:
                bi_dataset.physical_name(database, table)  # the name is checked before anything runs
    except DRSError as exc:
        log_access(username, "VIEW", "DENIED" if exc.code == "FORBIDDEN" else "ERROR", report_code=code,
                   error_code=exc.code, error_message=exc.admin_detail or exc.message, client_ip=client_ip,
                   view_key="bi")
        raise
    payload = bi_payload(d, settings)
    if not table:
        log_access(username, "VIEW", "OK", report_code=code, client_ip=client_ip, view_key="bi")
        return RunResponse(report=info, view="bi", params={}, row_count=0, bi=payload)
    served = run_for_user(database, settings, username, code, {}, client_ip=client_ip, view="bi")
    bi_dataset.refresh(database, report_id, table, served.outcome)
    payload["dataset"] = bi_dataset.display_name(database, table)
    return RunResponse(report=info, snapshot=_snapshot_info(served.outcome, settings), view="bi",
                       params=served.outcome.params, row_count=len(served.outcome.rows), bi=payload)


def bi_token(database: Database, settings: AppSettings, username: str, code: str, client_ip: str | None) -> str:
    """A guest token for the embedded dashboard of a report (spec 14.4 step 5), limited to that one
    dashboard. Row-level security is not built yet: a report with row-filter rules never gets
    here (design check)."""
    try:
        with database.session() as s:
            user = _user(s, username)
            report, _ = require_report(s, user, code)
            d = design.check(s, report, settings, design.require_view(report, "bi"))
            if d.kind != "bi_embed":
                raise DRSError("VIEW_NOT_AVAILABLE", admin_detail="the dashboard is not in embedded mode")
            who = {"username": user.username, "first_name": user.display_name, "last_name": ""}
        token = superset.client(settings.bi.superset).guest_token(d.dashboard_id, who, rls=[])
    except DRSError as exc:
        log_access(username, "BI_TOKEN", "DENIED" if exc.code == "FORBIDDEN" else "ERROR", report_code=code,
                   error_code=exc.code, error_message=exc.admin_detail or exc.message, client_ip=client_ip,
                   view_key="bi")
        raise
    log_access(username, "BI_TOKEN", "OK", report_code=code, client_ip=client_ip, view_key="bi")
    return token


def html_page(database: Database, settings: AppSettings, username: str, code: str, snapshot_id: int,
              csp_nonce: str, client_ip: str | None) -> str:
    """The rendered HTML design of a snapshot (iframe content). Same permission check and row
    filter as every other route; the snapshot must belong to this report."""
    try:
        with database.session() as s:
            user = _user(s, username)
            report, _ = require_report(s, user, code)
            d = design.check(s, report, settings, design.require_view(report, "html"))
            snap = cache.by_id(s, report.report_id, snapshot_id)
            if snap is None:
                raise DRSError("SNAPSHOT_GONE")
            page = html.render(s, settings, report, user, snap, d, csp_nonce)
    except DRSError as exc:
        log_access(username, "VIEW", "DENIED" if exc.code == "FORBIDDEN" else "ERROR", report_code=code,
                   snapshot_id=snapshot_id, error_code=exc.code, error_message=exc.admin_detail or exc.message,
                   client_ip=client_ip)
        raise
    log_access(username, "VIEW", "OK", report_code=code, snapshot_id=snapshot_id, client_ip=client_ip,
               view_key="html")
    return page


# --------------------------------------------------------------------------------------------
# Export

@dataclass
class ExportFile:
    filename: str
    media_type: str
    chunks: Iterator[bytes] | None = None
    path: Path | None = None  # XLSX: a temporary file the route deletes after sending


def export(database: Database, settings: AppSettings, username: str, code: str, fmt: str, snapshot_id: int,
           client_ip: str | None) -> ExportFile:
    """All rows the user may see of the snapshot on screen, every exported column, whatever the
    browser's sort or filter."""
    action = f"EXPORT_{fmt.upper()}" if fmt in EXPORT_MEDIA else "EXPORT_CSV"
    started = time.perf_counter()
    try:
        if fmt not in EXPORT_MEDIA:
            raise DRSError("PARAM_INVALID", detail={"format": "must be xlsx, csv or txt"})
        with database.session() as s:
            user = _user(s, username)
            report, access = require_report(s, user, code)
            if "grid" not in design.available_views(report) or not access.can_export:
                raise DRSError("EXPORT_NOT_ALLOWED")  # the rows of a report whose grid is hidden stay hidden
            snap = cache.by_id(s, report.report_id, snapshot_id)
            if snap is None:
                raise DRSError("SNAPSHOT_GONE")
            columns = [c for c in grid_columns(s, report.report_id, snap.columns_json) if c.exported]
            rows = project(filter_rows(s, user, report.report_id, [c["name"] for c in snap.columns_json],
                                       snap.rows_json), columns)
            meta = writers.ExportMeta(
                report_code=report.report_code, report_name=report.report_name, params=snap.params_json,
                data_time=local_time(snap.created_at, settings), exported_by=user.username,
                exported_at=local_time(utcnow(), settings))
            snapshot_ref, params = snap.snapshot_id, snap.params_json
        stamp = utcnow().astimezone(settings.tz).strftime("%Y%m%d_%H%M%S")
        result = ExportFile(filename=f"{code}_{stamp}.{fmt}", media_type=EXPORT_MEDIA[fmt])
        if fmt == "xlsx":
            result.path = writers.write_xlsx(columns, rows, meta, settings.export)
        elif fmt == "csv":
            result.chunks = writers.csv_chunks(columns, rows, settings.export)
        else:
            result.chunks = writers.txt_chunks(columns, rows, settings.export)
    except DRSError as exc:
        status = "DENIED" if exc.code in ("FORBIDDEN", "EXPORT_NOT_ALLOWED", "AUTH_REQUIRED") else "ERROR"
        log_access(username, action, status, report_code=code, snapshot_id=snapshot_id, error_code=exc.code,
                   error_message=exc.admin_detail or exc.message, client_ip=client_ip,
                   duration_ms=int((time.perf_counter() - started) * 1000))
        raise
    log_access(username, action, "OK", report_code=code, snapshot_id=snapshot_ref, params_json=params, view_key="grid",
               row_count=len(rows), client_ip=client_ip, duration_ms=int((time.perf_counter() - started) * 1000))
    return result
