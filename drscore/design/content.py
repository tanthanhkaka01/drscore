"""Draft report definition and content validation (spec section 3)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from drscore.db.models import (
    AGGREGATES,
    ALIGNS,
    COLUMN_DATA_TYPES,
    MULTI_BIND_MODES,
    PARAM_DATA_TYPES,
    PARAM_INPUT_KINDS,
    Report,
    ReportColumn,
    ReportParam,
)
from drscore.render.grid import FORMAT_RE
from drscore.reports.params import DefaultContext, ParamDef, ValueError_, binds_of, default_for
from drscore.reports.validate import PARAM_NAME_RE, REPORT_CODE_RE


class DraftParam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    param_name: str
    label: str
    data_type: Literal["text", "int", "decimal", "date", "datetime", "bool"]
    input_kind: Literal["input", "select", "multiselect"] = "input"
    is_required: bool = True  # owner decision 2026-10-07
    default_value: str | None = None
    options_json: Any = None
    options_query: str | None = None
    options_datasource: str | None = None  # datasource CODE or None
    multi_bind_mode: Literal["expand", "csv"] = "expand"
    min_value: str | None = None
    max_value: str | None = None
    max_length: int | None = None
    regex: str | None = None
    is_active: bool = True


class DraftColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    label: str
    data_type: Literal["text", "int", "decimal", "float", "bool", "date", "datetime", "time"] | None = None
    display_format: str | None = None
    width_px: int | None = None
    align: Literal["left", "center", "right"] | None = None
    is_visible: bool = True
    is_exported: bool = True
    is_sortable: bool = True
    is_filterable: bool = True
    is_frozen: bool = False
    footer_aggregate: Literal["sum", "avg", "count", "min", "max"] | None = None


class DraftContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    group: str
    datasource: str
    query_text: str = Field(min_length=1)
    cache_ttl_seconds: int = Field(default=0, ge=0)
    params: list[DraftParam] = Field(default_factory=list)
    columns: list[DraftColumn] = Field(default_factory=list)


def validate_content(code: str, content: dict[str, Any] | DraftContent) -> dict[str, str]:
    """Validates a draft report definition. Returns {field: message}; empty means valid."""
    errors: dict[str, str] = {}
    if not REPORT_CODE_RE.match(code or ""):
        errors["report_code"] = "The report code must match ^[A-Z][A-Z0-9_]{1,49}$."

    if isinstance(content, DraftContent):
        draft = content
    elif isinstance(content, dict):
        try:
            draft = DraftContent.model_validate(content)
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"]) or "content"
                if loc not in errors:
                    errors[loc] = err["msg"]
            return errors
    else:
        errors["content"] = "Content must be a dictionary or DraftContent."
        return errors

    seen_params: set[str] = set()
    for i, p in enumerate(draft.params):
        if not PARAM_NAME_RE.match(p.param_name or ""):
            errors[f"params.{i}.param_name"] = f"Parameter name {p.param_name!r} must match ^[a-z][a-z0-9_]*$."
        elif p.param_name in seen_params:
            errors[f"params.{i}.param_name"] = f"Parameter name {p.param_name!r} is duplicated."
        else:
            seen_params.add(p.param_name)

        if p.default_value:
            try:
                default_for(
                    ParamDef(
                        name=p.param_name,
                        label=p.label or "x",
                        data_type=p.data_type,
                        input_kind=p.input_kind,
                        default_value=p.default_value,
                    ),
                    DefaultContext(now=datetime.now()),
                )
            except ValueError_ as exc:
                errors[f"params.{i}.default_value"] = f"Default value: {exc}"

    seen_columns: set[str] = set()
    for i, c in enumerate(draft.columns):
        if not c.field_name or not c.field_name.strip():
            errors[f"columns.{i}.field_name"] = "Column field name cannot be empty."
        elif c.field_name in seen_columns:
            errors[f"columns.{i}.field_name"] = f"Column field name {c.field_name!r} is duplicated."
        else:
            seen_columns.add(c.field_name)

        if c.display_format and not FORMAT_RE.match(c.display_format):
            errors[f"columns.{i}.display_format"] = f"Invalid display format {c.display_format!r}."

    binds = binds_of(draft.query_text or "")
    active_params = {p.param_name for p in draft.params if p.is_active}
    all_params = {p.param_name for p in draft.params}
    for b in binds:
        if b not in active_params:
            if b in all_params:
                errors["query_text"] = f"The query uses :{b}, but parameter {b} is inactive."
            else:
                errors["query_text"] = f"The query uses :{b}, which is not an active parameter of the report."
            break

    return errors


def content_hash(code: str, content: dict[str, Any] | DraftContent) -> str:
    """Returns the sha256 hex digest of the canonical JSON representation of code + content."""
    if isinstance(content, DraftContent):
        c_dict = content.model_dump()
    else:
        c_dict = content
    data = {"code": code, "content": c_dict}
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def content_from_report(session: Session, report: Report) -> dict[str, Any]:
    """Returns the live report definition formatted as draft content."""
    params = list(
        session.scalars(
            select(ReportParam)
            .where(ReportParam.report_id == report.report_id)
            .order_by(ReportParam.sort_order, ReportParam.param_id)
        )
    )
    columns = list(
        session.scalars(
            select(ReportColumn)
            .where(ReportColumn.report_id == report.report_id)
            .order_by(ReportColumn.sort_order, ReportColumn.field_name)
        )
    )
    return {
        "report_name": report.report_name,
        "description": report.description,
        "group": report.group.group_code,
        "datasource": report.datasource.datasource_code if report.datasource else "",
        "query_text": report.query_text or "",
        "cache_ttl_seconds": report.cache_ttl_seconds or 0,
        "params": [
            {
                "param_name": p.param_name,
                "label": p.label,
                "data_type": p.data_type,
                "input_kind": p.input_kind,
                "is_required": p.is_required,
                "default_value": p.default_value,
                "options_json": p.options_json,
                "options_query": p.options_query,
                "options_datasource": p.options_datasource.datasource_code if p.options_datasource else None,
                "multi_bind_mode": p.multi_bind_mode,
                "min_value": p.min_value,
                "max_value": p.max_value,
                "max_length": p.max_length,
                "regex": p.regex,
                "is_active": p.is_active,
            }
            for p in params
        ],
        "columns": [
            {
                "field_name": c.field_name,
                "label": c.label,
                "data_type": c.data_type,
                "display_format": c.display_format,
                "width_px": c.width_px,
                "align": c.align,
                "is_visible": c.is_visible,
                "is_exported": c.is_exported,
                "is_sortable": c.is_sortable,
                "is_filterable": c.is_filterable,
                "is_frozen": c.is_frozen,
                "footer_aggregate": c.footer_aggregate,
            }
            for c in columns
        ],
    }
