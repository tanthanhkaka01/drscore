"""A dashboard on the datasets of several reports (owner request 2026-10-14).

A report's embedded dashboard may hold charts on the datasets of other reports: its *members*,
recorded when the dashboard is attached (``drs_report_bi_member``). Every member runs for the
user like the report itself, and the dashboard is shown for one snapshot of each: the guest token
carries one row-level rule per dataset (``bi_token``).

Parameters: the report's form keeps its own inputs, and a designer puts parameters of the members
on it too (``drs_report_bi_param``, on the BI page). Parameters with the same name, label, type
and kind (one value or several) share one input - the report's own one when it has it. The others
get an input of their own, named ``<MEMBER>-<param>``: no parameter can have that name, ``-`` is
not part of a bind name. A parameter of a member that is not on the form runs with its default
value. The inputs of members only are shown on the Dashboard tab.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from drscore.authz.permissions import require_report
from drscore.db.models import Report, ReportBiMember, ReportBiParam, ReportRowFilter, User
from drscore.errors import DRSError
from drscore.reports.params import ParamDef
from drscore.reports.service import param_defs
from drscore.settings import AppSettings

SEPARATOR = "-"
CHECK_CACHE_SECONDS = 60


def input_name(member_code: str, param_name: str) -> str:
    return f"{member_code}{SEPARATOR}{param_name}"


def is_member_input(name: str) -> bool:
    """An input of the form that only the dashboard's members read (never one of the report's)."""
    return SEPARATOR in name


def share_key(p: ParamDef) -> tuple[str, str, str, bool]:
    """Two parameters with this key are one input on the form."""
    return p.name, p.label, p.data_type, p.is_multi


def has_row_filter(session: Session, report_id: int) -> bool:
    return bool(session.scalar(select(func.count()).select_from(ReportRowFilter)
                               .where(ReportRowFilter.report_id == report_id)))


# --------------------------------------------------------------------------------------------
# Members and the form

@dataclass(frozen=True)
class Member:
    report_id: int
    code: str
    name: str
    table: str | None
    dataset_id: int
    is_host: bool  # the report whose dashboard it is


def members_of(session: Session, report_id: int) -> list[Member]:
    """The datasets of the report's dashboard and the reports that feed them; empty for a dashboard
    attached before 1.4, or by hand, which reads the report's own dataset only."""
    rows = session.execute(
        select(ReportBiMember, Report).join(Report, Report.report_id == ReportBiMember.member_report_id)
        .where(ReportBiMember.report_id == report_id)
        .order_by(ReportBiMember.sort_order, ReportBiMember.member_report_id)).all()
    return [Member(r.report_id, r.report_code, r.report_name, r.bi_dataset_table, m.dataset_id,
                   r.report_id == report_id) for m, r in rows]


@dataclass
class Entry:
    """One input of the form, and the parameters it gives its value to."""

    name: str  # of the input
    p: ParamDef  # how it is asked: the first parameter put there
    report_code: str  # whose parameter ``p`` is
    report_name: str
    on_report: bool  # one of the report's own inputs
    targets: list[tuple[str, str]] = field(default_factory=list)  # (report code, parameter name)


@dataclass
class Panel:
    report_code: str
    members: list[Member]
    entries: list[Entry]
    defs: dict[str, tuple[ParamDef, ...]]  # active parameters of each member, by report code
    picks: list[tuple[str, str]]  # (member code, parameter name) put on the form, in their order

    @property
    def others(self) -> list[Member]:
        return [m for m in self.members if not m.is_host]

    def extra(self) -> list[Entry]:
        """The inputs the dashboard adds to the form."""
        return [e for e in self.entries if not e.on_report]

    def entry_of(self, report_code: str, param_name: str) -> Entry | None:
        return next((e for e in self.entries if (report_code, param_name) in e.targets), None)

    def entry_named(self, name: str) -> Entry | None:
        return next((e for e in self.entries if e.name == name), None)

    def inputs_for(self, report_code: str, submitted: dict[str, Any] | None) -> dict[str, Any]:
        """What a member is run with: the values of the inputs its parameters are on. A parameter
        not on the form is left out, and takes its default."""
        values = {}
        for e in self.entries:
            if e.name in (submitted or {}):
                for code, name in e.targets:
                    if code == report_code:
                        values[name] = submitted[e.name]
        return values


def panel(session: Session, report: Report) -> Panel:
    members = members_of(session, report.report_id)
    entries = [Entry(p.name, p, report.report_code, report.report_name, True, [(report.report_code, p.name)])
               for p in param_defs(session, report.report_id)]
    by_key = {share_key(e.p): e for e in entries}
    others = {m.report_id: m for m in members if not m.is_host}
    defs = {m.code: param_defs(session, m.report_id) for m in others.values()}
    picks = []
    for pick in session.scalars(select(ReportBiParam).where(ReportBiParam.report_id == report.report_id)
                                .order_by(ReportBiParam.sort_order, ReportBiParam.param_name)):
        m = others.get(pick.member_report_id)
        p = next((d for d in defs[m.code] if d.name == pick.param_name), None) if m else None
        if p is None:  # no longer a member, or the parameter is gone or inactive
            continue
        e = by_key.get(share_key(p))
        if e is None:
            e = by_key[share_key(p)] = Entry(input_name(m.code, p.name), p, m.code, m.name, False)
            entries.append(e)
        e.targets.append((m.code, p.name))
        picks.append((m.code, p.name))
    return Panel(report.report_code, members, entries, defs, picks)


def check_members(session: Session, user: User, report_code: str, members: list[Member]) -> None:
    """The user may view every member, and none has row-filter rules: a dashboard shows a dataset
    whole, so a member whose rows are filtered per user is never embedded."""
    for m in members:
        if m.is_host:
            continue
        try:
            require_report(session, user, m.code)
        except DRSError as exc:
            # Named, so that the user knows which access to ask for: they may open this report, its
            # dashboard also reads that one.
            raise DRSError(exc.code, detail=f"{m.code} - {m.name}",
                           admin_detail=f"the dashboard of {report_code} reads the dataset of {m.code}") from exc
        if has_row_filter(session, m.report_id):
            raise DRSError("REPORT_MISCONFIGURED", admin_detail=(
                f"the dashboard of {report_code} reads the dataset of {m.code}, which has row-filter rules: "
                "a dashboard cannot filter rows per user"))


# --------------------------------------------------------------------------------------------
# The datasets the dashboard uses now (Superset can be changed after it was attached)

_used: dict[str, tuple[float, frozenset[int]]] = {}
_used_lock = threading.Lock()


def dashboard_datasets(settings: AppSettings, dashboard_uuid: str) -> frozenset[int] | None:
    """The datasets of an embedded dashboard as Superset has them, kept 60 seconds; None when DRS
    has no Superset admin account to ask with (self-service off)."""
    from drscore.bi import selfservice

    if not selfservice.is_on(settings):
        return None
    now = time.monotonic()
    with _used_lock:
        hit = _used.get(dashboard_uuid)
        if hit and now - hit[0] < CHECK_CACHE_SECONDS:
            return hit[1]
    client = selfservice.admin_client(settings.bi.superset)
    dashboard_id = client.embedded_dashboard_id(dashboard_uuid)
    if dashboard_id is None:
        raise DRSError("DESIGN_NOT_FOUND", admin_detail=f"Superset has no embedded dashboard {dashboard_uuid}")
    used = frozenset(client.dashboard_dataset_ids(dashboard_id))
    with _used_lock:
        _used[dashboard_uuid] = (now, used)
    return used


def clear_datasets_cache(dashboard_uuid: str | None = None) -> None:
    with _used_lock:
        if dashboard_uuid is None:
            _used.clear()
        else:
            _used.pop(dashboard_uuid, None)


# --------------------------------------------------------------------------------------------
# The BI page: which parameters of the members are on the form

def _pick(code: str, name: str) -> str:
    return f"{code}.{name}"


def _without_default(p: ParamDef) -> bool:
    return p.is_required and not (p.default_value or "").strip()


def designer_view(session: Session, report: Report) -> dict[str, Any] | None:
    """What the BI page shows of the form of a dashboard on several reports; None when the
    dashboard reads the report's own dataset only."""
    pan = panel(session, report)
    if not pan.others:
        return None

    def key(p: ParamDef) -> str:  # the page tells shared inputs apart with it
        return json.dumps(share_key(p), ensure_ascii=False)

    members = [{"code": m.code, "name": m.name, "params": [
        {"pick": _pick(m.code, p.name), "member": m.code, "member_name": m.name, "name": p.name, "label": p.label,
         "data_type": p.data_type, "multi": p.is_multi, "required": p.is_required, "default": p.default_value,
         "without_default": _without_default(p), "key": key(p)}
        for p in pan.defs[m.code]]} for m in pan.others]
    params = {p["pick"]: p for m in members for p in m["params"]}
    picked = [params[_pick(code, name)] for code, name in pan.picks]
    return {"members": members,
            "own": [{"name": e.p.name, "label": e.p.label, "key": key(e.p)} for e in pan.entries if e.on_report],
            "picked": picked,
            "missing": [p for p in params.values() if p["without_default"] and p not in picked]}


def save_picks(session: Session, report: Report, picks: list[str]) -> list[str]:
    """Puts these parameters of the members on the form, in this order (``CODE.param`` each).
    Refuses a pick that is not a parameter of a member, and a form that leaves out a required
    parameter without a default value: its report could not run."""
    pan = panel(session, report)
    others = {m.code: m for m in pan.others}
    if not others:
        raise DRSError("DRAFT_INVALID", detail={"reason": "no_members"})
    chosen: list[tuple[Member, str]] = []
    for pick in picks:
        code, _, name = pick.partition(".")
        m = others.get(code)
        if m is None or not any(p.name == name for p in pan.defs[code]):
            raise DRSError("DRAFT_INVALID", detail={"reason": "unknown_param", "param": pick})
        if (m, name) not in chosen:
            chosen.append((m, name))
    on_form = {(m.code, name) for m, name in chosen}
    missing = [_pick(code, p.name) for code, defs in pan.defs.items() for p in defs
               if _without_default(p) and (code, p.name) not in on_form]
    if missing:
        raise DRSError("DRAFT_INVALID", detail={"reason": "required_params", "params": missing})
    session.execute(delete(ReportBiParam).where(ReportBiParam.report_id == report.report_id))
    for i, (m, name) in enumerate(chosen):
        session.add(ReportBiParam(report_id=report.report_id, member_report_id=m.report_id, param_name=name,
                                  sort_order=i))
    session.flush()
    return [_pick(m.code, name) for m, name in chosen]
