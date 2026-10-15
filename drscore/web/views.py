"""What the portal shows of a report: its definition, a grid answer, an export file.

Every function repeats the permission check and applies the user's row filter: the routes only
translate HTTP to these calls.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import select

from drscore.audit import log_access
from drscore.authz.permissions import Access, require_report
from drscore.authz.rowfilter import filter_rows, user_attributes
from drscore.db.engine import Database
from drscore.db.models import Report, ReportSnapshot, User
from drscore.db.types import utcnow
from drscore.errors import DRSError
from drscore.export import writers
from drscore.bi import dataset as bi_dataset
from drscore.bi import members as bi_members
from drscore.bi import superset
from drscore.render import design, html
from drscore.render.grid import grid_columns, project
from drscore.reports import cache
from drscore.reports.params import ValueError_, default_for, form_options, parents_of, to_text
from drscore.reports.service import Served, default_context, options_loader, param_defs, run_for_user
from drscore.settings import AppSettings
from drscore.web.schemas import (
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

log = logging.getLogger(__name__)

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


def _reads_members(session, report: Report, user: User, d: design.Design) -> bool:
    """The embedded dashboard also reads the datasets of other reports - which the user must be
    allowed to view, and which have no row-filter rules (``bi_members.check_members``)."""
    if d.kind != "bi_embed":
        return False
    fed = bi_members.members_of(session, report.report_id)
    bi_members.check_members(session, user, report.report_code, fed)
    return any(not m.is_host for m in fed)


def view_states(session, report: Report, user: User, settings: AppSettings) -> list[ViewInfo]:
    """Every tab of the report with its state. A tab whose design is missing or broken is still
    listed (no design, no view: it shows the error); the others work."""
    states = []
    for key in design.available_views(report):
        try:
            d = design.check(session, report, settings, key)
            bi = None
            if key == "bi":
                bi = bi_payload(d, settings, bool(report.bi_dataset_table), _reads_members(session, report, user, d))
            states.append(ViewInfo(key=key, ok=True, bi=bi))
        except DRSError as exc:
            # ``detail`` names, for everyone, the report a dashboard reads that the user may not view.
            states.append(ViewInfo(key=key, ok=False, error=ErrorBody(
                code=exc.code, message=exc.message, detail=exc.admin_detail if user.is_admin else exc.detail)))
    return states


def _param_info(p, ctx, options, defs, **extra) -> ParamInfo:
    return ParamInfo(
        name=p.name, label=p.label, data_type=p.data_type, input_kind=p.input_kind,
        required=p.is_required, default=_input_text(_default(p, ctx), p.data_type),
        options=[ParamOption(**o) for o in options[p.name]] if p.has_options else None,
        depends_on=parents_of(p, defs) or None,
        min_value=p.min_value, max_value=p.max_value, max_length=p.max_length,
    ).model_copy(update=extra)


def _dashboard_params(session, report: Report, settings: AppSettings, ctx) -> list[ParamInfo]:
    """The inputs the dashboard adds to the form: parameters of the other reports it reads that
    are not one of the report's own inputs. Each depends on the inputs its options query uses."""
    pan = bi_members.panel(session, report)
    infos, options = [], {}
    for e in pan.extra():
        m = next(m for m in pan.others if m.code == e.report_code)
        defs = pan.defs[m.code]
        if m.code not in options:
            loader = options_loader(session, session.get(Report, m.report_id), settings)
            options[m.code], _ = form_options(defs, {}, ctx, loader)
        parents = [x.name for x in (pan.entry_of(m.code, n) for n in parents_of(e.p, defs)) if x is not None]
        infos.append(_param_info(e.p, ctx, options[m.code], defs, name=e.name, depends_on=parents or None,
                                 view="bi", source=m.name))
    return infos


def definition(database: Database, settings: AppSettings, username: str, code: str) -> ReportDefinition:
    with database.session() as s:
        user = _user(s, username)
        report, access = require_report(s, user, code)
        ctx = default_context(settings, user_attributes(s, user))
        defs = param_defs(s, report.report_id)
        options, _ = form_options(defs, {}, ctx, options_loader(s, report, settings))
        params = [_param_info(p, ctx, options, defs) for p in defs]
        views = view_states(s, report, user, settings)
        if not views:
            raise DRSError("REPORT_MISCONFIGURED", admin_detail="the report has no query and no design: nothing to show")
        default = design.default_view(report)
        # The report's own inputs only: those of the dashboard's other reports are asked for there.
        auto_run = all(not p.required or p.default not in (None, "", []) for p in params)
        bi_view = next((v for v in views if v.key == "bi" and v.ok and v.bi.get("members")), None)
        if bi_view is not None:
            try:
                params += _dashboard_params(s, report, settings, ctx)
            except DRSError as exc:  # the options of another report: its tab says so, the others work
                bi_view.ok, bi_view.bi = False, None
                bi_view.error = ErrorBody(code=exc.code, message=exc.message,
                                          detail=exc.admin_detail if user.is_admin else None)
        has_grid = "grid" in design.available_views(report)
        return ReportDefinition(report=report_info(report), params=params,
                                can_export=access.can_export and has_grid, can_refresh=access.can_refresh,
                                auto_run=auto_run, views=views, default_view=default)


def param_options(database: Database, settings: AppSettings, username: str, code: str, name: str,
                  submitted: dict[str, Any]) -> tuple[ParamInfo, list[str]]:
    """The options of one select parameter for the values now on the form, and what stays chosen
    among them. The report page asks for it (htmx) when a parameter the options depend on changes."""
    with database.session() as s:
        user = _user(s, username)
        report, _ = require_report(s, user, code)
        if bi_members.is_member_input(name):
            return _member_param_options(s, settings, user, report, name, submitted)
        defs = param_defs(s, report.report_id)
        p = next((d for d in defs if d.name == name and d.has_options), None)
        if p is None:
            raise DRSError("REPORT_NOT_FOUND", admin_detail=f"{code} has no select parameter {name}")
        ctx = default_context(settings, user_attributes(s, user))
        for d in defs:  # a form sends a ticked checkbox as "on" and leaves an unticked one out
            if d.data_type == "bool" and d.input_kind == "input":
                submitted = {**submitted, d.name: "true" if d.name in submitted else "false"}
        options, values = form_options(defs, submitted, ctx, options_loader(s, report, settings))
        chosen = _input_text(values[name], p.data_type)
        info = ParamInfo(name=p.name, label=p.label, data_type=p.data_type, input_kind=p.input_kind,
                         required=p.is_required, options=[ParamOption(**o) for o in options[name]])
        return info, chosen if isinstance(chosen, list) else [] if chosen is None else [chosen]


def _member_param_options(s, settings: AppSettings, user: User, report: Report, name: str,
                          submitted: dict[str, Any]) -> tuple[ParamInfo, list[str]]:
    """``param_options`` for an input of the dashboard: the options of that parameter of the other
    report, for the values its own parameters get from the form (their default when not on it)."""
    pan = bi_members.panel(s, report)
    e = pan.entry_named(name)
    if e is None or e.on_report or not e.p.has_options:
        raise DRSError("REPORT_NOT_FOUND", admin_detail=f"{report.report_code} has no dashboard select input {name}")
    bi_members.check_members(s, user, report.report_code, pan.members)
    m = next(m for m in pan.others if m.code == e.report_code)
    defs = pan.defs[m.code]
    values = pan.inputs_for(m.code, submitted)
    for d in defs:  # a ticked checkbox is sent as "on", an unticked one is left out
        on_form = pan.entry_of(m.code, d.name)
        if on_form is not None and d.data_type == "bool" and d.input_kind == "input":
            values[d.name] = "true" if on_form.name in submitted else "false"
    ctx = default_context(settings, user_attributes(s, user))
    loader = options_loader(s, s.get(Report, m.report_id), settings)
    options, chosen_values = form_options(defs, values, ctx, loader)
    chosen = _input_text(chosen_values[e.p.name], e.p.data_type)
    info = ParamInfo(name=e.name, label=e.p.label, data_type=e.p.data_type, input_kind=e.p.input_kind,
                     required=e.p.is_required, options=[ParamOption(**o) for o in options[e.p.name]])
    return info, chosen if isinstance(chosen, list) else [] if chosen is None else [chosen]


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


def bi_payload(d: design.Design, settings: AppSettings, follows_report: bool,
               members: bool = False) -> dict[str, Any]:
    """``follows_report``: the dashboard reads the report's dataset, so it is shown for the snapshot
    on screen - the result for the parameters the user chose - like the grid and the HTML design.
    ``members``: it also reads the datasets of other reports, which run with the form (``bi_members``)."""
    if d.kind == "bi_link":
        return {"mode": "LINK", "url": d.url, "follows_report": False}
    return {"mode": "EMBED", "superset_url": settings.bi.superset.base_url, "dashboard_id": d.dashboard_id,
            "follows_report": follows_report, "members": members}


def open_design(database: Database, settings: AppSettings, username: str, code: str,
                view: str) -> design.Design:
    """Permission, the view, then the design check of section 13."""
    with database.session() as s:
        report, _ = require_report(s, _user(s, username), code)
        return design.check(s, report, settings, design.require_view(report, view))


def run(database: Database, settings: AppSettings, username: str, code: str, params: dict[str, Any],
        refresh: bool, client_ip: str | None, view: str | None = None) -> RunResponse:
    """Runs the report (or reads its snapshot) for one view. The other views of the same
    snapshot are then read without running again (``grid_of_snapshot``, the HTML route). The
    inputs of the dashboard's other reports are theirs (``bi_members``), not the report's."""
    params = {k: v for k, v in (params or {}).items() if not bi_members.is_member_input(k)}
    with database.session() as s:
        report, _ = require_report(s, _user(s, username), code)
        chosen = design.require_view(report, view)
        info = report_info(report)
    if chosen == "bi":
        return bi_answer(database, settings, username, code, info, client_ip, params, refresh)
    served = run_for_user(database, settings, username, code, params, refresh=refresh, client_ip=client_ip,
                          view=chosen)
    _dataset_of(database, code, served.outcome)
    if chosen == "grid":
        with database.session() as s:
            report = s.scalars(select(Report).where(Report.report_code == code)).one()
            return _grid_response(s, report, settings, served.outcome, served.rows)
    o = served.outcome
    return RunResponse(report=info, snapshot=_snapshot_info(o, settings), view="html", params=o.params,
                       row_count=len(served.rows), html_url=html_url(code, o.snapshot_id))


def _dataset_of(database: Database, code: str, outcome) -> None:
    """A report that names a BI dataset has it from its first run on: a dashboard is designed on the
    dataset before the report has a Dashboard tab. Never the reason a grid is not shown."""
    with database.session() as s:
        report = s.scalars(select(Report).where(Report.report_code == code)).one()
        report_id, table = report.report_id, report.bi_dataset_table
    if not table:
        return
    try:
        bi_dataset.refresh(database, report_id, table, outcome)
    except DRSError as exc:
        log.warning("dataset %s of %s: %s: %s", table, code, exc.code, exc.admin_detail or exc.message)


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


@dataclass
class _Rows:
    """A stored snapshot as the dataset needs it."""

    snapshot_id: int
    columns: list
    rows: list


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
              client_ip: str | None, params: dict[str, Any] | None = None, refresh: bool = False) -> RunResponse:
    """A run asked for the dashboard view (a report whose only view it is). With ``bi_dataset_table``
    the report runs - or reads its snapshot, by retention - for the parameters given, exactly as
    for the grid, and that snapshot is what the dashboard is then shown for (``bi_token``)."""
    try:
        d = open_design(database, settings, username, code, "bi")
        with database.session() as s:
            report = s.scalars(select(Report).where(Report.report_code == code)).one()
            report_id, table = report.report_id, report.bi_dataset_table
            if table:
                bi_dataset.physical_name(database, table)  # the name is checked before anything runs
            members = _reads_members(s, report, _user(s, username), d)
    except DRSError as exc:
        log_access(username, "VIEW", "DENIED" if exc.code == "FORBIDDEN" else "ERROR", report_code=code,
                   error_code=exc.code, error_message=exc.admin_detail or exc.message, client_ip=client_ip,
                   view_key="bi")
        raise
    payload = bi_payload(d, settings, bool(table), members)
    if not table:
        log_access(username, "VIEW", "OK", report_code=code, client_ip=client_ip, view_key="bi")
        return RunResponse(report=info, view="bi", params={}, row_count=0, bi=payload)
    served = run_for_user(database, settings, username, code, params or {}, refresh=refresh, client_ip=client_ip,
                          view="bi")
    bi_dataset.refresh(database, report_id, table, served.outcome)
    payload["dataset"] = bi_dataset.display_name(database, table)
    return RunResponse(report=info, snapshot=_snapshot_info(served.outcome, settings), view="bi",
                       params=served.outcome.params, row_count=len(served.outcome.rows), bi=payload)


def bi_members_run(database: Database, settings: AppSettings, username: str, code: str,
                   params: dict[str, Any] | None, refresh: bool, client_ip: str | None) -> dict[str, int]:
    """Runs the other reports whose datasets the embedded dashboard of this report reads, each
    with the inputs of the form its parameters are on (``bi_members``) - their default for the
    others. Returns the snapshot of each by report code: the dashboard is shown for them
    (``bi_token``). A refresh refreshes the reports the user may refresh, and reads the others.

    A value one of them refuses is reported beside its input, as for the report itself."""
    try:
        with database.session() as s:
            user = _user(s, username)
            report, _ = require_report(s, user, code)
            d = design.check(s, report, settings, design.require_view(report, "bi"))
            if d.kind != "bi_embed":
                raise DRSError("VIEW_NOT_AVAILABLE", admin_detail="the dashboard is not in embedded mode")
            pan = bi_members.panel(s, report)
            bi_members.check_members(s, user, code, pan.members)
            plan = [(m, pan.inputs_for(m.code, params), refresh and require_report(s, user, m.code)[1].can_refresh)
                    for m in pan.others]
    except DRSError as exc:
        log_access(username, "VIEW", "DENIED" if exc.code == "FORBIDDEN" else "ERROR", report_code=code,
                   error_code=exc.code, error_message=exc.admin_detail or exc.message, client_ip=client_ip,
                   view_key="bi")
        raise
    snapshots: dict[str, int] = {}
    errors: dict[str, str] = {}
    for m, raw, again in plan:  # each run is logged as the user's run of that report
        try:
            served = run_for_user(database, settings, username, m.code, raw, refresh=again, client_ip=client_ip,
                                  view="dataset")
        except DRSError as exc:
            if exc.code != "PARAM_INVALID":
                raise DRSError(exc.code, exc.detail, admin_detail=f"{m.code}: {exc.admin_detail or exc.message}") from exc
            for name, message in (exc.detail or {}).items():
                e = pan.entry_of(m.code, name)  # not on the form: its default could not be used
                errors[e.name if e is not None else f"{m.name} ({name})"] = message
            continue
        bi_dataset.refresh(database, m.report_id, m.table, served.outcome)
        snapshots[m.code] = served.outcome.snapshot_id
    if errors:
        raise DRSError("PARAM_INVALID", detail=errors)
    return snapshots


def _member_rules(database: Database, settings: AppSettings, dashboard_uuid: str,
                  feeds: list[tuple[bi_members.Member, Any]]) -> list[dict[str, Any]]:
    """The row-level rules of a dashboard on the datasets of several reports: one per dataset, for
    the snapshot of its report, and one for every dataset - the snapshots on screen, no other - so
    that a dataset added to the dashboard in Superset since it was attached shows nothing.
    Superset is asked which datasets the dashboard uses now; one that no report of it feeds is
    refused with a reason, rather than shown empty."""
    used = bi_members.dashboard_datasets(settings, dashboard_uuid)
    if used is not None:
        unknown = used - {m.dataset_id for m, _ in feeds}
        if unknown:
            raise DRSError("DESIGN_INVALID", admin_detail=(
                f"the dashboard now uses the dataset(s) {sorted(unknown)}, which no report of it feeds: "
                "attach it again on the BI page"))
    rls = [{"clause": bi_dataset.rls_clause_any([rows.snapshot_id for _, rows in feeds])}]
    for m, rows in feeds:
        if used is not None and m.dataset_id not in used:
            continue
        bi_dataset.refresh(database, m.report_id, m.table, rows)
        rls.append({"dataset": m.dataset_id, "clause": bi_dataset.rls_clause(rows.snapshot_id)})
    return rls


def bi_token(database: Database, settings: AppSettings, username: str, code: str, client_ip: str | None,
             snapshot_id: int | None = None, members: dict[str, int] | None = None) -> str:
    """A guest token for the embedded dashboard of a report (spec 14.4 step 5), limited to that one
    dashboard - and, for a report with ``bi_dataset_table``, to one snapshot of it: the result on
    the user's screen. The token carries Superset's row-level rule ``drs_snapshot_id = <snapshot>``,
    so two users who chose different parameters read different data through the same dataset.
    A report with row-filter rules never gets here (design check).

    A dashboard that also reads the datasets of other reports is shown for one snapshot of each
    (``members``, from ``bi_members_run``): the user must be allowed to view each of them."""
    try:
        with database.session() as s:
            user = _user(s, username)
            report, _ = require_report(s, user, code)
            d = design.check(s, report, settings, design.require_view(report, "bi"))
            if d.kind != "bi_embed":
                raise DRSError("VIEW_NOT_AVAILABLE", admin_detail="the dashboard is not in embedded mode")
            who = {"username": user.username, "first_name": user.display_name, "last_name": ""}
            report_id, table, snapshot = report.report_id, report.bi_dataset_table, None
            fed = bi_members.members_of(s, report_id)
            bi_members.check_members(s, user, code, fed)
            if table:
                # Of this report, and still in the cache: the dataset holds nothing else.
                snap = s.get(ReportSnapshot, snapshot_id) if snapshot_id is not None else None
                if snap is None or snap.report_id != report_id:
                    raise DRSError("SNAPSHOT_GONE")
                snapshot = _Rows(snap.snapshot_id, snap.columns_json, snap.rows_json)
            feeds = []
            for m in fed:
                if m.is_host:
                    if snapshot is not None:
                        feeds.append((m, snapshot))
                    continue
                wanted = (members or {}).get(m.code)
                snap = s.get(ReportSnapshot, wanted) if wanted is not None else None
                if snap is None or snap.report_id != m.report_id:
                    raise DRSError("SNAPSHOT_GONE", admin_detail=f"no snapshot of {m.code}")
                feeds.append((m, _Rows(snap.snapshot_id, snap.columns_json, snap.rows_json)))
        if fed:  # the datasets of the dashboard are known: never a token without their rules
            if not feeds:
                raise DRSError("SNAPSHOT_GONE")
            rls = _member_rules(database, settings, d.dashboard_id, feeds)
        else:
            rls = []
            if snapshot is not None:
                bi_dataset.refresh(database, report_id, table, snapshot)
                rls = [{"clause": bi_dataset.rls_clause(snapshot.snapshot_id)}]
        token = superset.client(settings.bi.superset).guest_token(d.dashboard_id, who, rls=rls)
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
