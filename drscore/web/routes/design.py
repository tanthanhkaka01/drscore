"""Report designer web routes (spec section 7)."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from drscore.authz.design import design_rights
from drscore.db import get_database
from drscore.db.models import COLUMN_DATA_TYPES, Datasource, ReportGroup, User
from drscore.design import service as design_service
from drscore.design.content import content_hash
from drscore.errors import DRSError
from drscore.settings import get_settings
from drscore.web.deps import current_user, db_session
from drscore.web.templating import render

router = APIRouter()

# Parameter fields the editor shows as one JSON text per row ("Advanced"): static options, the
# binding of a multi-value parameter, bounds and pattern. Without them a save would drop them.
ADVANCED_KEYS = ("options_json", "multi_bind_mode", "min_value", "max_value", "max_length", "regex")


def _advanced_json(p: dict[str, Any]) -> str:
    """The advanced fields of a parameter that differ from their defaults, as JSON ('' when none)."""
    values = {k: p.get(k) for k in ADVANCED_KEYS if p.get(k) is not None}
    if values.get("multi_bind_mode") == "expand":
        del values["multi_bind_mode"]
    return json.dumps(values, ensure_ascii=False) if values else ""


def _parse_advanced(raw: str) -> dict[str, Any]:
    """The advanced fields typed in a row. Raises ValueError with the message shown beside it."""
    if not raw.strip():
        return {}
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(values, dict):
        raise ValueError('a JSON object is expected, e.g. {"options_json": ["A", "B"]}')
    unknown = sorted(set(values) - set(ADVANCED_KEYS))
    if unknown:
        raise ValueError(f"unknown key(s) {', '.join(unknown)}; allowed: {', '.join(ADVANCED_KEYS)}")
    for key in ("min_value", "max_value"):  # stored as text, typed as numbers or dates
        if values.get(key) is not None and not isinstance(values[key], str):
            values[key] = str(values[key])
    return values


def _parse_editor_form(form: Any) -> tuple[str, dict[str, Any], dict[str, str]]:
    code = str(form.get("report_code", "")).strip()
    report_name = str(form.get("report_name", "")).strip()
    description = str(form.get("description", "")).strip() or None
    group = str(form.get("group", "")).strip()
    datasource = str(form.get("datasource", "")).strip()

    try:
        cache_ttl = int(form.get("cache_ttl_seconds", 0))
    except (ValueError, TypeError):
        cache_ttl = 0

    query_text = str(form.get("query_text", "")).strip()

    errors: dict[str, str] = {}

    # Parse parameters
    param_indices: set[int] = set()
    for k in form.keys():
        m = re.match(r"^params-(\d+)-", k)
        if m:
            param_indices.add(int(m.group(1)))

    params = []
    for idx in sorted(param_indices):
        p_name = str(form.get(f"params-{idx}-param_name", "")).strip()
        if not p_name:
            continue
        p_label = str(form.get(f"params-{idx}-label", "")).strip() or p_name
        p_data_type = str(form.get(f"params-{idx}-data_type", "text")).strip()
        p_kind = str(form.get(f"params-{idx}-input_kind", "input")).strip()
        p_req = f"params-{idx}-is_required" in form
        p_def = str(form.get(f"params-{idx}-default_value", "")).strip() or None
        p_opt_q = str(form.get(f"params-{idx}-options_query", "")).strip() or None
        p_opt_ds = str(form.get(f"params-{idx}-options_datasource", "")).strip() or None
        p_active = f"params-{idx}-is_active" in form
        try:
            advanced = _parse_advanced(str(form.get(f"params-{idx}-advanced", "")))
        except ValueError as exc:
            errors[f"params.{len(params)}.advanced"] = str(exc)
            advanced = {}

        params.append({
            "param_name": p_name,
            "label": p_label,
            "data_type": p_data_type,
            "input_kind": p_kind,
            "is_required": p_req,
            "default_value": p_def,
            "options_json": advanced.get("options_json"),
            "options_query": p_opt_q,
            "options_datasource": p_opt_ds,
            "multi_bind_mode": advanced.get("multi_bind_mode", "expand"),
            "min_value": advanced.get("min_value"),
            "max_value": advanced.get("max_value"),
            "max_length": advanced.get("max_length"),
            "regex": advanced.get("regex"),
            "is_active": p_active,
        })

    # Parse columns
    col_indices: set[int] = set()
    for k in form.keys():
        m = re.match(r"^columns-(\d+)-", k)
        if m:
            col_indices.add(int(m.group(1)))

    columns = []
    for idx in sorted(col_indices):
        c_name = str(form.get(f"columns-{idx}-field_name", "")).strip()
        if not c_name:
            continue
        c_label = str(form.get(f"columns-{idx}-label", "")).strip() or c_name
        c_type = str(form.get(f"columns-{idx}-data_type", "")).strip() or None
        c_fmt = str(form.get(f"columns-{idx}-display_format", "")).strip() or None
        c_width_raw = form.get(f"columns-{idx}-width_px")
        c_width = int(c_width_raw) if c_width_raw and str(c_width_raw).isdigit() else None
        c_align = str(form.get(f"columns-{idx}-align", "")).strip() or None
        c_vis = f"columns-{idx}-is_visible" in form
        c_exp = f"columns-{idx}-is_exported" in form
        c_sort = f"columns-{idx}-is_sortable" in form
        c_filt = f"columns-{idx}-is_filterable" in form
        c_froz = f"columns-{idx}-is_frozen" in form
        c_agg = str(form.get(f"columns-{idx}-footer_aggregate", "")).strip() or None

        columns.append({
            "field_name": c_name,
            "label": c_label,
            "data_type": c_type,
            "display_format": c_fmt,
            "width_px": c_width,
            "align": c_align,
            "is_visible": c_vis,
            "is_exported": c_exp,
            "is_sortable": c_sort,
            "is_filterable": c_filt,
            "is_frozen": c_froz,
            "footer_aggregate": c_agg,
        })

    content = {
        "report_name": report_name,
        "description": description,
        "group": group,
        "datasource": datasource,
        "cache_ttl_seconds": cache_ttl,
        "query_text": query_text,
        "params": params,
        "columns": columns,
    }
    return code, content, errors


def _extract_test_params(form: Any, params: list[dict[str, Any]]) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for p in params:
        if not p.get("is_active", True):
            continue
        name = p.get("param_name")
        if not name:
            continue
        key = f"test__{name}"
        if key in form:
            val = str(form[key]).strip()
            if val != "":
                if p.get("input_kind") == "multiselect":
                    raw[name] = [v.strip() for v in val.split(",") if v.strip()]
                else:
                    raw[name] = val
    return raw


def _test_params_display(form: Any) -> dict[str, str]:
    return {k[6:]: str(v) for k, v in form.items() if k.startswith("test__")}


@router.get("/design")
def list_drafts(request: Request, user: User = Depends(current_user), session: Session = Depends(db_session)):
    rights = design_rights(session, user)
    if not rights.is_designer:
        raise DRSError("DESIGN_FORBIDDEN")

    drafts = design_service.list_drafts(session, user)
    editable_reps = design_service.editable_reports(session, user)
    msg = request.query_params.get("msg")
    return render(
        request, "design_list.html",
        drafts=drafts, editable_reports=editable_reps, is_admin=user.is_admin, msg=msg,
    )


@router.get("/design/new")
def new_draft(request: Request, user: User = Depends(current_user), session: Session = Depends(db_session)):
    rights = design_rights(session, user)
    if not rights.is_designer:
        raise DRSError("DESIGN_FORBIDDEN")

    groups = list(session.scalars(
        select(ReportGroup).where(ReportGroup.group_id.in_(rights.group_ids), ReportGroup.is_active.is_(True))
        .order_by(ReportGroup.sort_order, ReportGroup.group_name)
    ))
    datasources = list(session.scalars(
        select(Datasource).where(Datasource.datasource_id.in_(rights.datasource_ids), Datasource.is_active.is_(True))
        .order_by(Datasource.datasource_name)
    ))

    default_content = {
        "report_name": "",
        "description": "",
        "group": groups[0].group_code if groups else "",
        "datasource": datasources[0].datasource_code if datasources else "",
        "query_text": "",
        "cache_ttl_seconds": 0,
        "params": [],
        "columns": [],
    }
    return render(
        request, "design_edit.html", advanced=_advanced_json,
        is_new=True, is_editor=True, draft=None, code="",
        content=default_content, groups=groups, datasources=datasources,
        errors={}, test_result=None, test_params={},
        diff=None, msg=None, is_tested=False,
    )


@router.post("/design/new")
async def create_new_draft(request: Request, user: User = Depends(current_user), session: Session = Depends(db_session)):
    rights = design_rights(session, user)
    if not rights.is_designer:
        raise DRSError("DESIGN_FORBIDDEN")

    form = await request.form()
    code, content, form_errors = _parse_editor_form(form)
    try:
        if form_errors:
            raise DRSError("DRAFT_INVALID", detail=form_errors)
        draft = design_service.create_draft(session, user, code, content)
        session.commit()
        return RedirectResponse(f"/design/drafts/{draft.draft_id}", status_code=303)
    except DRSError as exc:
        if exc.code == "DRAFT_INVALID":
            groups = list(session.scalars(
                select(ReportGroup).where(ReportGroup.group_id.in_(rights.group_ids), ReportGroup.is_active.is_(True))
                .order_by(ReportGroup.sort_order, ReportGroup.group_name)
            ))
            datasources = list(session.scalars(
                select(Datasource).where(Datasource.datasource_id.in_(rights.datasource_ids), Datasource.is_active.is_(True))
                .order_by(Datasource.datasource_name)
            ))
            return render(
                request, "design_edit.html", advanced=_advanced_json, status_code=422,
                is_new=True, is_editor=True, draft=None, code=code,
                content=content, groups=groups, datasources=datasources,
                errors=exc.detail if isinstance(exc.detail, dict) else {"general": str(exc.detail or exc.message)},
                test_result=None, test_params={},
                diff=None, msg=None, is_tested=False,
            )
        raise


@router.post("/design/reports/{code}/edit")
def edit_report(code: str, request: Request, user: User = Depends(current_user), session: Session = Depends(db_session)):
    rights = design_rights(session, user)
    if not rights.is_designer:
        raise DRSError("DESIGN_FORBIDDEN")

    draft = design_service.draft_from_report(session, user, code)
    session.commit()
    return RedirectResponse(f"/design/drafts/{draft.draft_id}", status_code=303)


@router.get("/design/drafts/{draft_id}")
def view_draft(draft_id: int, request: Request, user: User = Depends(current_user), session: Session = Depends(db_session)):
    rights = design_rights(session, user)
    if not rights.is_designer:
        raise DRSError("DESIGN_FORBIDDEN")

    draft = design_service.get_draft(session, user, draft_id)
    is_editor = (draft.author == user.username and draft.status in ("DRAFT", "REJECTED"))

    groups = list(session.scalars(
        select(ReportGroup).where(ReportGroup.group_id.in_(rights.group_ids), ReportGroup.is_active.is_(True))
        .order_by(ReportGroup.sort_order, ReportGroup.group_name)
    ))
    datasources = list(session.scalars(
        select(Datasource).where(Datasource.datasource_id.in_(rights.datasource_ids), Datasource.is_active.is_(True))
        .order_by(Datasource.datasource_name)
    ))

    diff = None
    if user.is_admin and draft.status == "PENDING":
        diff = design_service.diff_against_live(session, draft)

    msg = request.query_params.get("msg")
    current_hash = content_hash(draft.report_code, draft.content_json)
    is_tested = bool(draft.tested_hash and draft.tested_hash == current_hash)

    return render(
        request, "design_edit.html", advanced=_advanced_json,
        is_new=False, is_editor=is_editor, draft=draft, code=draft.report_code,
        content=draft.content_json, groups=groups, datasources=datasources,
        errors={}, test_result=None, test_params={},
        diff=diff, msg=msg, is_tested=is_tested,
    )


@router.post("/design/drafts/{draft_id}")
async def update_draft(draft_id: int, request: Request, user: User = Depends(current_user), session: Session = Depends(db_session)):
    rights = design_rights(session, user)
    if not rights.is_designer:
        raise DRSError("DESIGN_FORBIDDEN")

    draft = design_service.get_draft(session, user, draft_id)
    if not (draft.author == user.username and draft.status in ("DRAFT", "REJECTED")):
        raise DRSError("DRAFT_STATE")

    form = await request.form()
    action = str(form.get("action", "save")).strip()
    code, content, form_errors = _parse_editor_form(form)
    if draft.report_id is not None:
        code = draft.report_code

    groups = list(session.scalars(
        select(ReportGroup).where(ReportGroup.group_id.in_(rights.group_ids), ReportGroup.is_active.is_(True))
        .order_by(ReportGroup.sort_order, ReportGroup.group_name)
    ))
    datasources = list(session.scalars(
        select(Datasource).where(Datasource.datasource_id.in_(rights.datasource_ids), Datasource.is_active.is_(True))
        .order_by(Datasource.datasource_name)
    ))

    try:
        if form_errors:
            raise DRSError("DRAFT_INVALID", detail=form_errors)
        if action == "save":
            design_service.save_draft(session, user, draft_id, code, content)
            session.commit()
            return RedirectResponse(f"/design/drafts/{draft_id}?msg=saved", status_code=303)

        elif action == "test":
            design_service.save_draft(session, user, draft_id, code, content)
            session.commit()
            raw_params = _extract_test_params(form, content.get("params", []))
            test_result = design_service.test_draft(get_database(), get_settings().app, user, draft_id, raw_params)
            session.expire_all()
            draft = design_service.get_draft(session, user, draft_id)
            current_hash = content_hash(draft.report_code, draft.content_json)
            is_tested = bool(draft.tested_hash and draft.tested_hash == current_hash)
            return render(
                request, "design_edit.html", advanced=_advanced_json, status_code=200,
                is_new=False, is_editor=True, draft=draft, code=draft.report_code,
                content=draft.content_json, groups=groups, datasources=datasources,
                errors={}, test_result=test_result, test_params=_test_params_display(form),
                is_tested=is_tested,
            )

        elif action == "columns_from_test":
            design_service.save_draft(session, user, draft_id, code, content)
            session.commit()
            session.expire_all()
            draft = design_service.get_draft(session, user, draft_id)
            if draft.test_summary and draft.test_summary.get("ok"):
                test_cols = draft.test_summary.get("columns", [])
                existing_names = {c.get("field_name") for c in draft.content_json.get("columns", [])}
                new_cols = list(draft.content_json.get("columns", []))
                changed = False
                for tc in test_cols:
                    c_name = tc.get("name")
                    c_type = tc.get("type")
                    if c_name and c_name not in existing_names:
                        new_cols.append({
                            "field_name": c_name,
                            "label": c_name,
                            "data_type": c_type if c_type in COLUMN_DATA_TYPES else None,
                            "display_format": None,
                            "width_px": None,
                            "align": None,
                            "is_visible": True,
                            "is_exported": True,
                            "is_sortable": True,
                            "is_filterable": True,
                            "is_frozen": False,
                            "footer_aggregate": None,
                        })
                        existing_names.add(c_name)
                        changed = True
                if changed:
                    new_content = dict(draft.content_json)
                    new_content["columns"] = new_cols
                    design_service.save_draft(session, user, draft_id, draft.report_code, new_content)
                    session.commit()
            return RedirectResponse(f"/design/drafts/{draft_id}?msg=columns_filled", status_code=303)

        else:
            raise DRSError("PARAM_INVALID")

    except DRSError as exc:
        if exc.code == "DRAFT_INVALID":
            current_hash = content_hash(code, content)
            is_tested = bool(draft.tested_hash and draft.tested_hash == current_hash)
            return render(
                request, "design_edit.html", advanced=_advanced_json, status_code=422,
                is_new=False, is_editor=True, draft=draft, code=code,
                content=content, groups=groups, datasources=datasources,
                errors=exc.detail if isinstance(exc.detail, dict) else {"general": str(exc.detail or exc.message)},
                test_result=None, test_params=_test_params_display(form),
                is_tested=is_tested,
            )
        raise


@router.post("/design/drafts/{draft_id}/submit")
def submit_draft(draft_id: int, request: Request, user: User = Depends(current_user), session: Session = Depends(db_session)):
    rights = design_rights(session, user)
    if not rights.is_designer:
        raise DRSError("DESIGN_FORBIDDEN")

    design_service.submit_draft(session, user, draft_id)
    session.commit()
    return RedirectResponse(f"/design/drafts/{draft_id}?msg=submitted", status_code=303)


@router.post("/design/drafts/{draft_id}/withdraw")
def withdraw_draft(draft_id: int, request: Request, user: User = Depends(current_user), session: Session = Depends(db_session)):
    rights = design_rights(session, user)
    if not rights.is_designer:
        raise DRSError("DESIGN_FORBIDDEN")

    design_service.withdraw_draft(session, user, draft_id)
    session.commit()
    return RedirectResponse(f"/design/drafts/{draft_id}?msg=withdrawn", status_code=303)


@router.post("/design/drafts/{draft_id}/delete")
def delete_draft(draft_id: int, request: Request, user: User = Depends(current_user), session: Session = Depends(db_session)):
    rights = design_rights(session, user)
    if not rights.is_designer:
        raise DRSError("DESIGN_FORBIDDEN")

    design_service.delete_draft(session, user, draft_id)
    session.commit()
    return RedirectResponse("/design?msg=deleted", status_code=303)


@router.post("/design/drafts/{draft_id}/approve")
async def approve_draft(draft_id: int, request: Request, user: User = Depends(current_user), session: Session = Depends(db_session)):
    rights = design_rights(session, user)
    if not rights.is_designer:
        raise DRSError("DESIGN_FORBIDDEN")

    form = await request.form()
    note = str(form.get("note", "")).strip() or None
    design_service.approve_draft(session, user, draft_id, note, get_settings().app)
    session.commit()
    return RedirectResponse(f"/design/drafts/{draft_id}?msg=approved", status_code=303)


@router.post("/design/drafts/{draft_id}/reject")
async def reject_draft(draft_id: int, request: Request, user: User = Depends(current_user), session: Session = Depends(db_session)):
    rights = design_rights(session, user)
    if not rights.is_designer:
        raise DRSError("DESIGN_FORBIDDEN")

    form = await request.form()
    note = str(form.get("note", "")).strip()
    design_service.reject_draft(session, user, draft_id, note)
    session.commit()
    return RedirectResponse(f"/design/drafts/{draft_id}?msg=rejected", status_code=303)
