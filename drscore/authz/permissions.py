"""Who may view a report, and with which rights (spec sections 10.1, 10.2 and 10.4).

A user's principals are the user itself plus every active role the user has. A user may view a
report when the user, the report and its group are active, and:

1. the user is an admin; or
2. the report is not restricted and a principal holds a grant on the report's group; or
3. a principal holds a grant on the report itself.

``can_export`` / ``can_refresh`` are true when any grant that lets the user view the report has the
flag. Admins have both. There is no deny rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from drscore.db.models import GrantGroup, GrantReport, Report, ReportGroup, Role, User, UserRole
from drscore.errors import DRSError


@dataclass(frozen=True)
class Principals:
    user_id: int
    role_ids: frozenset[int]
    role_codes: dict[int, str] = field(default_factory=dict, compare=False)


@dataclass
class Access:
    """The rights of one user on one report, and the grants they come from."""

    can_view: bool = False
    can_export: bool = False
    can_refresh: bool = False
    via: list[str] = field(default_factory=list)

    def add(self, source: str, can_export: bool, can_refresh: bool) -> None:
        self.can_view = True
        self.can_export = self.can_export or can_export
        self.can_refresh = self.can_refresh or can_refresh
        self.via.append(source)


ADMIN_ACCESS_SOURCE = "admin"


def principals_of(session: Session, user: User) -> Principals:
    rows = session.execute(
        select(Role.role_id, Role.role_code)
        .join(UserRole, UserRole.role_id == Role.role_id)
        .where(UserRole.user_id == user.user_id, Role.is_active.is_(True))
    ).all()
    return Principals(user.user_id, frozenset(r.role_id for r in rows), {r.role_id: r.role_code for r in rows})


def _principal_filter(model, p: Principals):
    conditions = [model.user_id == p.user_id]
    if p.role_ids:
        conditions.append(model.role_id.in_(p.role_ids))
    return or_(*conditions)


def _source(kind: str, code: str, grant, p: Principals) -> str:
    who = "user" if grant.user_id is not None else f"role {p.role_codes.get(grant.role_id, grant.role_id)}"
    return f"{kind} {code} ({who})"


def accessible_reports(session: Session, user: User) -> list[tuple[Report, Access]]:
    """Every active report in an active group that the user may view, in menu order."""
    if not user.is_active:
        return []
    reports = session.scalars(
        select(Report)
        .join(ReportGroup, ReportGroup.group_id == Report.group_id)
        .where(Report.is_active.is_(True), ReportGroup.is_active.is_(True))
        .order_by(ReportGroup.sort_order, ReportGroup.group_name, Report.sort_order, Report.report_name)
    ).all()
    if user.is_admin:
        return [(r, Access(True, True, True, [ADMIN_ACCESS_SOURCE])) for r in reports]

    p = principals_of(session, user)
    group_grants: dict[int, list[GrantGroup]] = {}
    for g in session.scalars(select(GrantGroup).where(_principal_filter(GrantGroup, p))):
        group_grants.setdefault(g.group_id, []).append(g)
    report_grants: dict[int, list[GrantReport]] = {}
    for g in session.scalars(select(GrantReport).where(_principal_filter(GrantReport, p))):
        report_grants.setdefault(g.report_id, []).append(g)

    result = []
    for report in reports:
        access = Access()
        if not report.is_restricted:
            for g in group_grants.get(report.group_id, []):
                access.add(_source("group", report.group.group_code, g, p), g.can_export, g.can_refresh)
        for g in report_grants.get(report.report_id, []):
            access.add(_source("report", report.report_code, g, p), g.can_export, g.can_refresh)
        if access.can_view:
            result.append((report, access))
    return result


def access_to(session: Session, user: User, report: Report) -> Access:
    """The rights of the user on one report (all false when the user may not view it)."""
    if not (user.is_active and report.is_active and report.group.is_active):
        return Access()
    if user.is_admin:
        return Access(True, True, True, [ADMIN_ACCESS_SOURCE])
    p = principals_of(session, user)
    access = Access()
    if not report.is_restricted:
        for g in session.scalars(select(GrantGroup).where(
                GrantGroup.group_id == report.group_id, _principal_filter(GrantGroup, p))):
            access.add(_source("group", report.group.group_code, g, p), g.can_export, g.can_refresh)
    for g in session.scalars(select(GrantReport).where(
            GrantReport.report_id == report.report_id, _principal_filter(GrantReport, p))):
        access.add(_source("report", report.report_code, g, p), g.can_export, g.can_refresh)
    return access


def require_report(session: Session, user: User, report_code: str) -> tuple[Report, Access]:
    """The report a user asks for by code.

    ``REPORT_NOT_FOUND`` when there is no such active report in an active group; ``FORBIDDEN`` when
    the user may not view it. Every route that serves a report goes through here.
    """
    report = session.scalars(select(Report).where(Report.report_code == report_code)).one_or_none()
    if report is None or not report.is_active or not report.group.is_active:
        raise DRSError("REPORT_NOT_FOUND")
    access = access_to(session, user, report)
    if not access.can_view:
        raise DRSError("FORBIDDEN")
    return report, access


def menu(session: Session, user: User) -> list[dict]:
    """Groups with at least one report the user may view, each with those reports (10.4)."""
    groups: dict[int, dict] = {}
    for report, access in accessible_reports(session, user):
        g = report.group
        entry = groups.setdefault(g.group_id, {
            "code": g.group_code, "name": g.group_name, "icon": g.icon,
            "description": g.description, "reports": [],
        })
        entry["reports"].append({
            "code": report.report_code, "name": report.report_name, "type": report.report_type,
            "description": report.description,
        })
    return list(groups.values())
