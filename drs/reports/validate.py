"""``report validate``: finds problems in report definitions before a user does (spec section 18).

Checks made without running anything: datasource, query / parameter binds, parameter rows, BI
table name, design state. Result columns are only known after a run, so column and row-filter
names are compared with the newest snapshot (cache or history) when there is one.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from drs.db.models import (
    Datasource,
    Report,
    ReportColumn,
    ReportParam,
    ReportRowFilter,
    ReportSnapshot,
    ReportSnapshotHistory,
)
from drs.errors import DRSError
from drs.render import design
from drs.reports.params import DefaultContext, ParamDef, ValueError_, binds_of, default_for, parse_value
from drs.settings import AppSettings

REPORT_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,49}$")
PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
BI_TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,50}$")


def _last_columns(s: Session, report_id: int) -> list[str] | None:
    for model in (ReportSnapshot, ReportSnapshotHistory):
        snap = s.scalars(select(model).where(model.report_id == report_id)
                         .order_by(model.snapshot_id.desc()).limit(1)).first()
        if snap is not None:
            return [c["name"] for c in snap.columns_json]
    return None


def check_report(s: Session, r: Report, settings: AppSettings) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    err = lambda m: findings.append(("ERROR", m))  # noqa: E731
    warn = lambda m: findings.append(("WARN", m))  # noqa: E731

    if not REPORT_CODE_RE.match(r.report_code):
        err("report_code does not match ^[A-Z][A-Z0-9_]{1,49}$")
    if not r.is_active:
        warn("report is inactive")
    if not r.group.is_active:
        warn(f"group {r.group.group_code} is inactive")

    views = design.available_views(r)
    if not views:
        err("the report has nothing to show: no query (grid) and no design")
    if bool(r.datasource_id) != bool(r.query_text):
        err("datasource_id and query_text go together")
    if "html" in views and not r.query_text:
        err("the HTML view needs a query")
    if not r.grid_enabled:
        findings.append(("INFO", "grid view hidden (grid_enabled = false)"))
    if r.datasource_id is not None:
        ds = s.get(Datasource, r.datasource_id)
        if ds is not None and not ds.is_active:
            err(f"datasource {ds.datasource_code} is inactive")

    all_params = list(s.scalars(select(ReportParam).where(ReportParam.report_id == r.report_id)))
    params = [p for p in all_params if p.is_active]
    inactive = {p.param_name for p in all_params if not p.is_active}
    names = {p.param_name for p in params}
    position = {p.param_name: i for i, p in enumerate(sorted(params, key=lambda p: (p.sort_order, p.param_id)))}
    binds = binds_of(r.query_text or "")
    for b in binds:
        if b in inactive:
            err(f"the query uses :{b}, but parameter {b} is inactive")
        elif b not in names:
            err(f"the query uses :{b}, which is not a parameter of the report")
    for name in sorted(inactive - set(binds)):
        findings.append(("INFO", f"parameter {name} is inactive"))
    for p in params:
        if not PARAM_NAME_RE.match(p.param_name):
            err(f"parameter {p.param_name}: name does not match ^[a-z][a-z0-9_]*$")
        if p.param_name not in binds:
            warn(f"parameter {p.param_name} is not used by the query")
        if p.input_kind in ("select", "multiselect") and not (p.options_json or p.options_query):
            err(f"parameter {p.param_name}: a {p.input_kind} needs options_json or options_query")
        # An options query may use other parameters (the units of the chosen company): they must be
        # known when its options are made, so they come before it.
        for b in binds_of(p.options_query or ""):
            if b in inactive:
                err(f"parameter {p.param_name}: the options query uses :{b}, but parameter {b} is inactive")
            elif b not in names:
                err(f"parameter {p.param_name}: the options query uses :{b}, which is not a parameter of the report")
            elif position[b] >= position[p.param_name]:
                err(f"parameter {p.param_name}: the options query uses :{b}; place {b} before "
                    f"{p.param_name} (order)")
        if p.default_value:
            try:
                default_for(ParamDef.of(p), DefaultContext(now=datetime.now()))
            except ValueError_ as exc:
                err(f"parameter {p.param_name}: default {p.default_value!r}: {exc}")
        for bound in (p.min_value, p.max_value):
            if bound:
                try:
                    parse_value(p.data_type, bound)
                except ValueError_ as exc:
                    err(f"parameter {p.param_name}: bound {bound!r} {exc}")
        if p.regex:
            try:
                re.compile(p.regex)
            except re.error as exc:
                err(f"parameter {p.param_name}: regex is not valid ({exc})")

    if r.bi_dataset_table and not BI_TABLE_RE.match(r.bi_dataset_table):
        err("bi_dataset_table does not match ^[a-z][a-z0-9_]{0,50}$")
    if r.bi_dataset_table and not r.query_text:
        err("bi_dataset_table needs a query: the dataset is the result of the report")

    for view in views:
        if view == "grid":
            continue
        try:
            d = design.check(s, r, settings, view)
            findings.append(("INFO", f"{view} design: OK ({d.uri})"))
        except DRSError as exc:
            level = "WARN" if exc.code in ("DESIGN_NOT_SET", "BI_ENGINE_UNAVAILABLE") else "ERROR"
            findings.append((level, f"{view} design: {exc.code} - {exc.admin_detail}"))

    columns = [c.field_name for c in s.scalars(select(ReportColumn).where(ReportColumn.report_id == r.report_id))]
    rules = [f.column_name for f in s.scalars(select(ReportRowFilter).where(ReportRowFilter.report_id == r.report_id))]
    if columns or rules:
        result = _last_columns(s, r.report_id)
        if result is None:
            findings.append(("INFO", "columns / row filter not compared: the report has not run yet"))
        else:
            have = {n.casefold() for n in result}
            for c in columns:
                if c.casefold() not in have:
                    err(f"column {c} is not in the result of the last run")
            for c in rules:
                if c.casefold() not in have:
                    err(f"row filter column {c} is not in the result of the last run")
    return findings


def validate_reports(s: Session, settings: AppSettings, code: str | None = None):
    stmt = select(Report).order_by(Report.report_code)
    if code:
        stmt = stmt.where(Report.report_code == code)
    return [(r.report_code, check_report(s, r, settings)) for r in s.scalars(stmt)]
