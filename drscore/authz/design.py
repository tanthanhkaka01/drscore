"""Designer rights and checks (spec section 4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from drscore.authz.permissions import _principal_filter, principals_of
from drscore.db.models import Datasource, GrantDatasource, GrantGroup, Report, ReportGroup, ReportParam, User
from drscore.design.content import DraftContent
from drscore.errors import DRSError


@dataclass(frozen=True)
class DesignRights:
    is_admin: bool
    group_ids: frozenset[int]        # groups the user may design in (active groups only)
    datasource_ids: frozenset[int]   # datasources the user may query (active datasources only)

    @property
    def is_designer(self) -> bool:
        return self.is_admin or bool(self.group_ids and self.datasource_ids)


def design_rights(session: Session, user: User) -> DesignRights:
    """Computes the design rights of a user."""
    if not user.is_active:
        return DesignRights(is_admin=False, group_ids=frozenset(), datasource_ids=frozenset())
    if user.is_admin:
        groups = session.scalars(select(ReportGroup.group_id).where(ReportGroup.is_active.is_(True))).all()
        datasources = session.scalars(select(Datasource.datasource_id).where(Datasource.is_active.is_(True))).all()
        return DesignRights(is_admin=True, group_ids=frozenset(groups), datasource_ids=frozenset(datasources))

    p = principals_of(session, user)
    group_ids = session.scalars(
        select(GrantGroup.group_id)
        .join(ReportGroup, ReportGroup.group_id == GrantGroup.group_id)
        .where(_principal_filter(GrantGroup, p), GrantGroup.can_design.is_(True), ReportGroup.is_active.is_(True))
    ).all()
    datasource_ids = session.scalars(
        select(GrantDatasource.datasource_id)
        .join(Datasource, Datasource.datasource_id == GrantDatasource.datasource_id)
        .where(_principal_filter(GrantDatasource, p), Datasource.is_active.is_(True))
    ).all()
    return DesignRights(is_admin=False, group_ids=frozenset(group_ids), datasource_ids=frozenset(datasource_ids))


def param_datasource_ids(session: Session, report_id: int) -> list[int]:
    """The datasources the options queries of a report's parameters run on."""
    return list(session.scalars(select(ReportParam.options_datasource_id).where(
        ReportParam.report_id == report_id, ReportParam.options_datasource_id.is_not(None))))


def can_edit_report(rights: DesignRights, report: Report, param_datasource_ids: Iterable[int | None]) -> bool:
    """True when the user may propose changes to this report. Restricted reports are for
    administrators only: their SQL is often the sensitive part (salaries)."""
    if rights.is_admin:
        return True
    if report.is_restricted:
        return False
    if report.group_id not in rights.group_ids:
        return False
    if report.datasource_id is None or report.datasource_id not in rights.datasource_ids:
        return False
    return all(ds_id is None or ds_id in rights.datasource_ids for ds_id in param_datasource_ids)


def require_content_rights(
    session: Session,
    rights: DesignRights,
    content: dict[str, Any] | DraftContent,
) -> tuple[ReportGroup, Datasource]:
    """Resolves group and datasource codes and asserts rights. Raises DESIGN_FORBIDDEN on any violation."""
    if isinstance(content, DraftContent):
        group_code = content.group
        ds_code = content.datasource
        params = content.params
    else:
        group_code = content.get("group", "")
        ds_code = content.get("datasource", "")
        params = content.get("params", [])

    group = session.scalars(select(ReportGroup).where(ReportGroup.group_code == group_code)).one_or_none()
    if group is None or not group.is_active or group.group_id not in rights.group_ids:
        raise DRSError("DESIGN_FORBIDDEN")

    ds = session.scalars(select(Datasource).where(Datasource.datasource_code == ds_code)).one_or_none()
    if ds is None or not ds.is_active or ds.datasource_id not in rights.datasource_ids:
        raise DRSError("DESIGN_FORBIDDEN")

    for p in params:
        opt_ds_code = p.options_datasource if hasattr(p, "options_datasource") else p.get("options_datasource")
        if opt_ds_code:
            opt_ds = session.scalars(select(Datasource).where(Datasource.datasource_code == opt_ds_code)).one_or_none()
            if opt_ds is None or not opt_ds.is_active or opt_ds.datasource_id not in rights.datasource_ids:
                raise DRSError("DESIGN_FORBIDDEN")

    return group, ds
