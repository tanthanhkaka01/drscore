"""Service layer for report designer drafts, testing, and approval workflow (spec section 6)."""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from drscore.audit import audit, row_snapshot
from drscore.authz.design import can_edit_report, design_rights, param_datasource_ids, require_content_rights
from drscore.datasources.connectors import SourceInfo
from drscore.db.engine import Database
from drscore.db.models import Datasource, Report, ReportColumn, ReportGroup, ReportParam, ReportDraft, User
from drscore.db.types import utcnow
from drscore.design.content import DraftContent, content_from_report, content_hash, validate_content
from drscore.errors import CATALOGUE, DRSError
from drscore.reports import executor
from drscore.reports.params import (
    ParamDef,
    binds_of,
    options_cache,
    parents_of,
    static_options,
    validate,
)
from drscore.reports.service import default_context, source_of
from drscore.reports.validate import check_report
from drscore.settings import AppSettings

log = logging.getLogger(__name__)

# Errors whose technical reason a designer sees: they come from the designer's own SQL and
# parameters. A connection error (DATASOURCE_UNAVAILABLE) may name hosts and is never detailed.
DETAILED_ERRORS = {"SOURCE_QUERY_FAILED", "SOURCE_TIMEOUT", "RESULT_TOO_LARGE", "REPORT_MISCONFIGURED"}
TEST_ROWS_SHOWN = 200


@dataclass
class DraftTest:
    ok: bool
    columns: list[dict[str, str]]
    row_count: int | None
    duration_ms: int | None
    rows: list[list[Any]]
    error_code: str | None = None
    error: str | None = None


def list_drafts(session: Session, user: User, status: str | None = None) -> list[ReportDraft]:
    """Lists drafts visible to the user: all drafts for admins, own drafts for others."""
    stmt = select(ReportDraft)
    if not user.is_admin:
        stmt = stmt.where(ReportDraft.author == user.username)
    if status is not None:
        stmt = stmt.where(ReportDraft.status == status)

    if user.is_admin:
        stmt = stmt.order_by(
            case((ReportDraft.status == "PENDING", 0), else_=1),
            ReportDraft.updated_at.desc(),
            ReportDraft.draft_id.desc(),
        )
    else:
        stmt = stmt.order_by(ReportDraft.updated_at.desc(), ReportDraft.draft_id.desc())

    return list(session.scalars(stmt))


def editable_reports(session: Session, user: User) -> list[Report]:
    """Live active reports the user may propose changes to."""
    rights = design_rights(session, user)
    if not rights.is_designer:
        return []
    reports = session.scalars(
        select(Report)
        .join(ReportGroup, ReportGroup.group_id == Report.group_id)
        .where(Report.is_active.is_(True), ReportGroup.is_active.is_(True))
        .order_by(ReportGroup.sort_order, ReportGroup.group_name, Report.sort_order, Report.report_name)
    ).all()
    result = []
    for r in reports:
        if can_edit_report(rights, r, param_datasource_ids(session, r.report_id)):
            result.append(r)
    return result


def _normalised(content: dict[str, Any] | DraftContent) -> dict[str, Any]:
    """The content as it is stored: every field present, so its hash does not depend on how it was
    sent. Call it on content ``validate_content`` accepted."""
    model = content if isinstance(content, DraftContent) else DraftContent.model_validate(content)
    return model.model_dump(mode="json")


def create_draft(
    session: Session,
    user: User,
    code: str,
    content: dict[str, Any] | DraftContent,
) -> ReportDraft:
    """Creates a new proposal draft for a new report."""
    rights = design_rights(session, user)
    if not rights.is_designer:
        raise DRSError("DESIGN_FORBIDDEN")

    errors = validate_content(code, content)
    if errors:
        raise DRSError("DRAFT_INVALID", detail=errors)

    require_content_rights(session, rights, content)

    # Code must not be an existing live report
    live = session.scalars(select(Report).where(Report.report_code == code)).first()
    if live is not None:
        raise DRSError("DRAFT_INVALID", detail={"report_code": f"Report {code} already exists."})

    # Code must not have an open draft
    open_draft = session.scalars(
        select(ReportDraft).where(ReportDraft.report_code == code, ReportDraft.status.in_(("DRAFT", "PENDING")))
    ).first()
    if open_draft is not None:
        raise DRSError("DRAFT_INVALID", detail={"report_code": f"An open draft already exists for {code}."})

    content_dict = _normalised(content)
    draft = ReportDraft(
        report_id=None,
        report_code=code,
        status="DRAFT",
        content_json=content_dict,
        author=user.username,
    )
    session.add(draft)
    session.flush()

    audit(session, user.username, "DRAFT_CREATE", "drs_report_draft", f"{code}#{draft.draft_id}", after=content_dict)
    return draft


def draft_from_report(session: Session, user: User, code: str) -> ReportDraft:
    """Creates or returns an open draft for modifying an existing report."""
    report = session.scalars(select(Report).where(Report.report_code == code)).one_or_none()
    if report is None:
        raise DRSError("REPORT_NOT_FOUND")

    rights = design_rights(session, user)
    if not can_edit_report(rights, report, param_datasource_ids(session, report.report_id)):
        raise DRSError("DESIGN_FORBIDDEN")

    open_draft = session.scalars(
        select(ReportDraft).where(ReportDraft.report_code == code, ReportDraft.status.in_(("DRAFT", "PENDING")))
    ).first()
    if open_draft is not None:
        if open_draft.author == user.username:
            return open_draft
        raise DRSError("DRAFT_STATE", detail=f"Draft for {code} is already open by {open_draft.author}.")

    content_dict = content_from_report(session, report)
    draft = ReportDraft(
        report_id=report.report_id,
        report_code=code,
        status="DRAFT",
        content_json=content_dict,
        author=user.username,
    )
    session.add(draft)
    session.flush()

    audit(session, user.username, "DRAFT_CREATE", "drs_report_draft", f"{code}#{draft.draft_id}", after=content_dict)
    return draft


def get_draft(session: Session, user: User, draft_id: int) -> ReportDraft:
    """Gets a draft by id; only author or admin may see it."""
    draft = session.get(ReportDraft, draft_id)
    if draft is None:
        raise DRSError("DRAFT_NOT_FOUND")
    if not (user.is_admin or draft.author == user.username):
        raise DRSError("DRAFT_NOT_FOUND")
    return draft


def save_draft(
    session: Session,
    user: User,
    draft_id: int,
    code: str,
    content: dict[str, Any] | DraftContent,
) -> ReportDraft:
    """Saves changes to an existing draft."""
    draft = get_draft(session, user, draft_id)
    if draft.author != user.username:
        raise DRSError("DESIGN_FORBIDDEN")
    if draft.status not in ("DRAFT", "REJECTED"):
        raise DRSError("DRAFT_STATE")

    if draft.report_id is not None and code != draft.report_code:
        raise DRSError("DRAFT_STATE", detail="Cannot change report code for an existing report proposal.")

    errors = validate_content(code, content)
    if errors:
        raise DRSError("DRAFT_INVALID", detail=errors)

    rights = design_rights(session, user)
    require_content_rights(session, rights, content)

    if draft.report_id is None and code != draft.report_code:
        live = session.scalars(select(Report).where(Report.report_code == code)).first()
        if live is not None:
            raise DRSError("DRAFT_INVALID", detail={"report_code": f"Report {code} already exists."})
        open_draft = session.scalars(
            select(ReportDraft).where(
                ReportDraft.report_code == code,
                ReportDraft.status.in_(("DRAFT", "PENDING")),
                ReportDraft.draft_id != draft.draft_id,
            )
        ).first()
        if open_draft is not None:
            raise DRSError("DRAFT_INVALID", detail={"report_code": f"An open draft already exists for {code}."})

    before = draft.content_json
    content_dict = _normalised(content)

    draft.report_code = code
    draft.content_json = content_dict
    draft.status = "DRAFT"
    session.flush()

    audit(session, user.username, "DRAFT_UPDATE", "drs_report_draft", f"{code}#{draft.draft_id}", before=before, after=content_dict)
    return draft


def test_draft(
    database: Database,
    settings: AppSettings,
    user: User,
    draft_id: int,
    raw_params: dict[str, Any] | None,
) -> DraftTest:
    """Runs a query test on a draft datasource without caching or row filtering."""
    with database.session() as s:
        draft = get_draft(s, user, draft_id)
        if draft.status == "APPROVED":
            raise DRSError("DRAFT_STATE", detail="An approved draft is not tested again.")
        code = draft.report_code
        content = draft.content_json
        require_content_rights(s, design_rights(s, user), content)

    with database.session() as s:
        ds = s.scalars(select(Datasource).where(Datasource.datasource_code == content["datasource"])).one()
        src = SourceInfo.of(ds)

        # Build ParamDef tuples from active draft params
        active_params = [p for p in content.get("params", []) if p.get("is_active", True)]
        defs_list: list[ParamDef] = []
        for p in active_params:
            opt_ds_id = None
            if p.get("options_datasource"):
                opt_ds = s.scalars(select(Datasource).where(Datasource.datasource_code == p["options_datasource"])).one_or_none()
                opt_ds_id = opt_ds.datasource_id if opt_ds else None
            defs_list.append(
                ParamDef(
                    name=p["param_name"],
                    label=p.get("label") or p["param_name"],
                    data_type=p["data_type"],
                    input_kind=p.get("input_kind", "input"),
                    is_required=p.get("is_required", True),
                    default_value=p.get("default_value"),
                    options_json=p.get("options_json"),
                    options_query=p.get("options_query"),
                    options_datasource_id=opt_ds_id,
                    multi_bind_mode=p.get("multi_bind_mode", "expand"),
                    min_value=p.get("min_value"),
                    max_value=p.get("max_value"),
                    max_length=p.get("max_length"),
                    regex=p.get("regex"),
                )
            )
        defs = tuple(defs_list)
        order = {d.name: i for i, d in enumerate(defs)}

        query_text = content.get("query_text", "")
        used_binds = binds_of(query_text)
        def_names = {d.name for d in defs}
        unknown = [b for b in used_binds if b not in def_names]
        if unknown:
            raise DRSError("REPORT_MISCONFIGURED", admin_detail=f"the query uses :{', :'.join(unknown)}, which is not an active parameter of the report")

        def draft_options_loader(p: ParamDef, values: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            if not p.options_query:
                return static_options(p)
            opt_ds_id = p.options_datasource_id or ds.datasource_id
            opt_src = source_of(s, opt_ds_id, f"options of parameter {p.name}")
            for b in binds_of(p.options_query):
                if b not in order:
                    raise DRSError("REPORT_MISCONFIGURED", admin_detail=f"options of parameter {p.name}: the query uses :{b}, which is not an active parameter")
                if order[b] >= order.get(p.name, -1):
                    raise DRSError("REPORT_MISCONFIGURED", admin_detail=f"options of parameter {p.name}: the query uses :{b}, which must be placed before {p.name} (order)")
            parents = [d for d in defs if d.name in parents_of(p, defs)]
            bound = {d.name: (values or {}).get(d.name, [] if d.is_multi else None) for d in parents}
            key = (opt_src, p.options_query, tuple((k, repr(v)) for k, v in bound.items()))

            def run() -> list[dict[str, Any]]:
                result = executor.run_query(
                    opt_src, p.options_query, parents, bound,
                    timeout_seconds=30, max_rows=10000, max_bytes=10 * 1024 * 1024, tz=settings.tz
                )
                if len(result.columns) < 1:
                    raise DRSError("REPORT_MISCONFIGURED", admin_detail=f"options of {p.name}: no column")
                return [{"value": "" if r[0] is None else str(r[0]),
                         "label": str(r[1] if len(r) > 1 and r[1] is not None else r[0])} for r in result.rows]

            return options_cache.get(key, run)

        try:
            ctx = default_context(settings, user_attributes=None, now=None)
            values = validate(defs, raw_params, ctx, draft_options_loader)
            q_result = executor.run_query(
                src,
                query_text,
                defs,
                values,
                timeout_seconds=min(settings.limits.default_timeout_seconds, settings.limits.hard_timeout_seconds),
                max_rows=settings.limits.default_max_rows,
                max_bytes=settings.limits.max_snapshot_mb * 1024 * 1024,
                tz=settings.tz,
            )
        except DRSError as exc:
            err_code = exc.code
            if err_code in DETAILED_ERRORS:
                err_text = exc.admin_detail or exc.message
            elif err_code == "PARAM_INVALID" and isinstance(exc.detail, dict):
                err_text = "; ".join(str(v) for v in exc.detail.values())
            else:
                err_text = exc.message

            summary = {
                "ok": False,
                "at": utcnow().isoformat(),
                "row_count": None,
                "columns": [],
                "duration_ms": None,
                "error_code": err_code,
                "error": err_text,
            }
            with database.session() as s2:
                d = s2.get(ReportDraft, draft_id)
                d.tested_hash = None
                d.test_summary = summary
                audit(s2, user.username, "DRAFT_TEST", "drs_report_draft", f"{code}#{draft_id}", after=summary)
            return DraftTest(ok=False, columns=[], row_count=None, duration_ms=None, rows=[], error_code=err_code, error=err_text)
        except Exception:  # a defect, not the designer's SQL: the reason goes to the log only
            log.exception("test run of draft %s failed", draft_id)
            err_text = CATALOGUE["SOURCE_QUERY_FAILED"][1]
            summary = {
                "ok": False,
                "at": utcnow().isoformat(),
                "row_count": None,
                "columns": [],
                "duration_ms": None,
                "error_code": "ERROR",
                "error": err_text,
            }
            with database.session() as s2:
                d = s2.get(ReportDraft, draft_id)
                d.tested_hash = None
                d.test_summary = summary
                audit(s2, user.username, "DRAFT_TEST", "drs_report_draft", f"{code}#{draft_id}", after=summary)
            return DraftTest(ok=False, columns=[], row_count=None, duration_ms=None, rows=[], error_code="ERROR", error=err_text)

    tested_hash = content_hash(code, content)
    summary = {
        "ok": True,
        "at": utcnow().isoformat(),
        "row_count": len(q_result.rows),
        "columns": q_result.columns,
        "duration_ms": q_result.duration_ms,
        "error_code": None,
        "error": None,
    }
    with database.session() as s3:
        d = s3.get(ReportDraft, draft_id)
        d.tested_hash = tested_hash
        d.test_summary = summary
        audit(s3, user.username, "DRAFT_TEST", "drs_report_draft", f"{code}#{draft_id}", after=summary)

    return DraftTest(
        ok=True,
        columns=q_result.columns,
        row_count=len(q_result.rows),
        duration_ms=q_result.duration_ms,
        rows=q_result.rows[:TEST_ROWS_SHOWN],
    )


def submit_draft(session: Session, user: User, draft_id: int) -> ReportDraft:
    """Submits a tested draft for admin review."""
    draft = get_draft(session, user, draft_id)
    if draft.author != user.username:
        raise DRSError("DESIGN_FORBIDDEN")
    if draft.status != "DRAFT":
        raise DRSError("DRAFT_STATE", detail="Only drafts in DRAFT status can be submitted.")

    current_hash = content_hash(draft.report_code, draft.content_json)
    if draft.tested_hash != current_hash:
        raise DRSError("DRAFT_STATE", detail="Please run a successful test after the last change before submitting.")

    draft.status = "PENDING"
    draft.submitted_at = utcnow()
    session.flush()

    audit(
        session,
        user.username,
        "DRAFT_SUBMIT",
        "drs_report_draft",
        f"{draft.report_code}#{draft.draft_id}",
        after={"status": "PENDING", "submitted_at": draft.submitted_at.isoformat()},
    )
    return draft


def withdraw_draft(session: Session, user: User, draft_id: int) -> ReportDraft:
    """Withdraws a PENDING draft back to DRAFT."""
    draft = get_draft(session, user, draft_id)
    if draft.author != user.username:
        raise DRSError("DESIGN_FORBIDDEN")
    if draft.status != "PENDING":
        raise DRSError("DRAFT_STATE", detail="Only PENDING drafts can be withdrawn.")

    draft.status = "DRAFT"
    session.flush()

    audit(
        session,
        user.username,
        "DRAFT_WITHDRAW",
        "drs_report_draft",
        f"{draft.report_code}#{draft.draft_id}",
        after={"status": "DRAFT"},
    )
    return draft


def delete_draft(session: Session, user: User, draft_id: int) -> None:
    """Deletes a draft."""
    draft = get_draft(session, user, draft_id)
    if draft.status == "APPROVED":
        raise DRSError("DRAFT_STATE", detail="Approved drafts cannot be deleted.")

    if not user.is_admin:
        if draft.author != user.username:
            raise DRSError("DESIGN_FORBIDDEN")
        if draft.status not in ("DRAFT", "REJECTED"):
            raise DRSError("DRAFT_STATE", detail="Drafts can only be deleted in DRAFT or REJECTED status.")

    audit(session, user.username, "DRAFT_DELETE", "drs_report_draft", f"{draft.report_code}#{draft.draft_id}", before=draft.content_json)
    session.delete(draft)


def approve_draft(
    session: Session,
    admin: User,
    draft_id: int,
    note: str | None,
    settings: AppSettings,
) -> list[tuple[str, str]]:
    """Approves a PENDING draft and applies it to the live report tables."""
    if not admin.is_admin:
        raise DRSError("DESIGN_FORBIDDEN")

    draft = session.get(ReportDraft, draft_id)
    if draft is None:
        raise DRSError("DRAFT_NOT_FOUND")
    if draft.status != "PENDING":
        raise DRSError("DRAFT_STATE", detail="Only PENDING drafts can be approved.")

    content = draft.content_json
    errors = validate_content(draft.report_code, content)
    if errors:
        raise DRSError("DRAFT_INVALID", detail=errors)

    group = session.scalars(select(ReportGroup).where(ReportGroup.group_code == content["group"])).one_or_none()
    if group is None or not group.is_active:
        raise DRSError("DRAFT_INVALID", detail={"group": f"Group {content['group']} is missing or inactive."})

    ds = session.scalars(select(Datasource).where(Datasource.datasource_code == content["datasource"])).one_or_none()
    if ds is None or not ds.is_active:
        raise DRSError("DRAFT_INVALID", detail={"datasource": f"Datasource {content['datasource']} is missing or inactive."})

    for i, p in enumerate(content.get("params", [])):
        opt_code = p.get("options_datasource")
        if opt_code:
            opt_ds = session.scalars(select(Datasource).where(Datasource.datasource_code == opt_code)).one_or_none()
            if opt_ds is None or not opt_ds.is_active:
                raise DRSError("DRAFT_INVALID", detail={f"params.{i}.options_datasource": f"Datasource {opt_code} is missing or inactive."})

    if draft.report_id is None:
        # New report
        live = session.scalars(select(Report).where(Report.report_code == draft.report_code)).first()
        if live is not None:
            raise DRSError("DRAFT_INVALID", detail={"report_code": f"Report {draft.report_code} already exists."})

        report = Report(
            report_code=draft.report_code,
            report_name=content["report_name"],
            description=content.get("description"),
            group_id=group.group_id,
            datasource_id=ds.datasource_id,
            query_text=content["query_text"],
            cache_ttl_seconds=content.get("cache_ttl_seconds", 0),
            report_type="GRID",
            created_by=draft.author,
            updated_by=admin.username,
        )
        session.add(report)
        session.flush()
        draft.report_id = report.report_id
        audit(session, admin.username, "CREATE", "drs_report", report.report_code, after=row_snapshot(report))
    else:
        # Existing report change
        report = session.get(Report, draft.report_id)
        if report is None:
            raise DRSError("REPORT_NOT_FOUND")
        before_snap = row_snapshot(report)
        report.report_name = content["report_name"]
        report.description = content.get("description")
        report.group_id = group.group_id
        report.datasource_id = ds.datasource_id
        report.query_text = content["query_text"]
        report.cache_ttl_seconds = content.get("cache_ttl_seconds", 0)
        report.updated_by = admin.username
        session.flush()
        audit(session, admin.username, "UPDATE", "drs_report", report.report_code, before=before_snap, after=row_snapshot(report))

    # Params sync
    new_param_names = [p["param_name"] for p in content.get("params", [])]
    old_params = list(session.scalars(select(ReportParam).where(ReportParam.report_id == report.report_id)))
    for op in old_params:
        if op.param_name not in new_param_names:
            session.delete(op)
    session.flush()

    for idx, p_dict in enumerate(content.get("params", [])):
        p_name = p_dict["param_name"]
        existing_p = session.scalars(
            select(ReportParam).where(ReportParam.report_id == report.report_id, ReportParam.param_name == p_name)
        ).first()
        opt_ds_id = None
        if p_dict.get("options_datasource"):
            opt_ds = session.scalars(select(Datasource).where(Datasource.datasource_code == p_dict["options_datasource"])).one_or_none()
            opt_ds_id = opt_ds.datasource_id if opt_ds else None
        if existing_p is None:
            existing_p = ReportParam(report_id=report.report_id, param_name=p_name)
            session.add(existing_p)
        existing_p.label = p_dict["label"]
        existing_p.data_type = p_dict["data_type"]
        existing_p.input_kind = p_dict.get("input_kind", "input")
        existing_p.is_required = p_dict.get("is_required", True)
        existing_p.default_value = p_dict.get("default_value")
        existing_p.options_json = p_dict.get("options_json")
        existing_p.options_query = p_dict.get("options_query")
        existing_p.options_datasource_id = opt_ds_id
        existing_p.multi_bind_mode = p_dict.get("multi_bind_mode", "expand")
        existing_p.min_value = p_dict.get("min_value")
        existing_p.max_value = p_dict.get("max_value")
        existing_p.max_length = p_dict.get("max_length")
        existing_p.regex = p_dict.get("regex")
        existing_p.is_active = p_dict.get("is_active", True)
        existing_p.sort_order = Decimal(idx)
    session.flush()

    # Columns sync
    new_col_names = [c["field_name"] for c in content.get("columns", [])]
    old_cols = list(session.scalars(select(ReportColumn).where(ReportColumn.report_id == report.report_id)))
    for oc in old_cols:
        if oc.field_name not in new_col_names:
            session.delete(oc)
    session.flush()

    for idx, c_dict in enumerate(content.get("columns", [])):
        c_name = c_dict["field_name"]
        existing_c = session.scalars(
            select(ReportColumn).where(ReportColumn.report_id == report.report_id, ReportColumn.field_name == c_name)
        ).first()
        if existing_c is None:
            existing_c = ReportColumn(report_id=report.report_id, field_name=c_name)
            session.add(existing_c)
        existing_c.label = c_dict["label"]
        existing_c.data_type = c_dict.get("data_type")
        existing_c.display_format = c_dict.get("display_format")
        existing_c.width_px = c_dict.get("width_px")
        existing_c.align = c_dict.get("align")
        existing_c.is_visible = c_dict.get("is_visible", True)
        existing_c.is_exported = c_dict.get("is_exported", True)
        existing_c.is_sortable = c_dict.get("is_sortable", True)
        existing_c.is_filterable = c_dict.get("is_filterable", True)
        existing_c.is_frozen = c_dict.get("is_frozen", False)
        existing_c.footer_aggregate = c_dict.get("footer_aggregate")
        existing_c.sort_order = idx
    session.flush()

    draft.status = "APPROVED"
    draft.reviewed_by = admin.username
    draft.reviewed_at = utcnow()
    draft.review_note = note
    audit(
        session,
        admin.username,
        "DRAFT_APPROVE",
        "drs_report_draft",
        f"{draft.report_code}#{draft.draft_id}",
        after={"reviewed_by": admin.username, "review_note": note, "content": content},
    )

    findings = check_report(session, report, settings)
    return findings


def reject_draft(
    session: Session,
    admin: User,
    draft_id: int,
    note: str,
) -> ReportDraft:
    """Rejects a PENDING draft with a mandatory review note."""
    if not admin.is_admin:
        raise DRSError("DESIGN_FORBIDDEN")

    draft = session.get(ReportDraft, draft_id)
    if draft is None:
        raise DRSError("DRAFT_NOT_FOUND")
    if draft.status != "PENDING":
        raise DRSError("DRAFT_STATE", detail="Only PENDING drafts can be rejected.")

    if not note or not note.strip():
        raise DRSError("DRAFT_INVALID", detail={"review_note": "A review note is required when rejecting a draft."})

    draft.status = "REJECTED"
    draft.reviewed_by = admin.username
    draft.reviewed_at = utcnow()
    draft.review_note = note.strip()
    session.flush()

    audit(
        session,
        admin.username,
        "DRAFT_REJECT",
        "drs_report_draft",
        f"{draft.report_code}#{draft.draft_id}",
        after={"reviewed_by": admin.username, "review_note": note.strip()},
    )
    return draft


def diff_against_live(session: Session, draft: ReportDraft) -> dict[str, Any]:
    """Computes differences between draft and the live report definition for review."""
    content = draft.content_json
    if draft.report_id is None:
        prop_sql_lines = (content.get("query_text") or "").splitlines(keepends=True)
        query_diff = list(difflib.unified_diff([], prop_sql_lines, fromfile="live", tofile="proposed"))
        return {
            "is_new": True,
            "fields": {
                "report_name": (None, content.get("report_name")),
                "description": (None, content.get("description")),
                "group": (None, content.get("group")),
                "datasource": (None, content.get("datasource")),
                "cache_ttl_seconds": (None, content.get("cache_ttl_seconds", 0)),
            },
            "query_diff": query_diff,
            "params": {
                "added": [p["param_name"] for p in content.get("params", [])],
                "removed": [],
                "changed": [],
            },
            "columns": {
                "added": [c["field_name"] for c in content.get("columns", [])],
                "removed": [],
                "changed": [],
            },
        }

    report = session.get(Report, draft.report_id)
    if report is None:
        return {"error": "Live report missing"}

    live_content = content_from_report(session, report)

    field_changes: dict[str, tuple[Any, Any]] = {}
    for key in ("report_name", "description", "group", "datasource", "cache_ttl_seconds"):
        live_val = live_content.get(key)
        prop_val = content.get(key)
        if live_val != prop_val:
            field_changes[key] = (live_val, prop_val)

    live_sql_lines = (live_content.get("query_text") or "").splitlines(keepends=True)
    prop_sql_lines = (content.get("query_text") or "").splitlines(keepends=True)
    query_diff = list(difflib.unified_diff(live_sql_lines, prop_sql_lines, fromfile="live", tofile="proposed"))

    live_params = {p["param_name"]: p for p in live_content.get("params", [])}
    prop_params = {p["param_name"]: p for p in content.get("params", [])}
    params_added = sorted(set(prop_params) - set(live_params))
    params_removed = sorted(set(live_params) - set(prop_params))
    params_changed = sorted(name for name in set(live_params) & set(prop_params) if live_params[name] != prop_params[name])

    live_cols = {c["field_name"]: c for c in live_content.get("columns", [])}
    prop_cols = {c["field_name"]: c for c in content.get("columns", [])}
    cols_added = sorted(set(prop_cols) - set(live_cols))
    cols_removed = sorted(set(live_cols) - set(prop_cols))
    cols_changed = sorted(name for name in set(live_cols) & set(prop_cols) if live_cols[name] != prop_cols[name])

    return {
        "is_new": False,
        "fields": field_changes,
        "query_diff": query_diff,
        "params": {"added": params_added, "removed": params_removed, "changed": params_changed},
        "columns": {"added": cols_added, "removed": cols_removed, "changed": cols_changed},
    }
