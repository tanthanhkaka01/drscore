"""Request and response models of the JSON API (spec section 16)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------------
# Requests

class LoginRequest(_Model):
    username: str = Field(max_length=200)
    password: str = Field(max_length=1000)


ViewKey = Literal["grid", "html", "bi"]


class RunRequest(_Model):
    params: dict[str, Any] = {}
    refresh: bool = False
    view: ViewKey | None = None  # the report's default view when not given


# --------------------------------------------------------------------------------------------
# Responses

class ErrorBody(_Model):
    code: str
    message: str
    detail: Any = None


class ErrorResponse(_Model):
    error: ErrorBody


class LoginResponse(_Model):
    username: str
    csrf_token: str


class MeResponse(_Model):
    username: str
    display_name: str
    is_admin: bool
    locale: Literal["vi", "en"]
    csrf_token: str


class MenuReport(_Model):
    code: str
    name: str
    type: Literal["GRID", "HTML", "BI"]
    description: str | None = None


class MenuGroup(_Model):
    code: str
    name: str
    icon: str | None = None
    description: str | None = None
    reports: list[MenuReport]


class MenuResponse(_Model):
    groups: list[MenuGroup]


class ParamOption(_Model):
    value: str
    label: str


class ParamInfo(_Model):
    name: str
    label: str
    data_type: Literal["text", "int", "decimal", "date", "datetime", "bool"]
    input_kind: Literal["input", "select", "multiselect"]
    required: bool
    default: Any = None  # in the text form the input takes (list for a multiselect)
    options: list[ParamOption] | None = None
    depends_on: list[str] | None = None  # the parameters its options query uses: it is filled again when they change
    min_value: str | None = None
    max_value: str | None = None
    max_length: int | None = None


class ReportInfo(_Model):
    code: str
    name: str
    type: Literal["GRID", "HTML", "BI"]
    group: str | None = None
    group_name: str | None = None
    description: str | None = None


class ViewInfo(_Model):
    """One tab of the report page."""

    key: ViewKey
    ok: bool
    error: ErrorBody | None = None  # why the tab cannot show (DESIGN_NOT_SET, ...)
    bi: dict[str, Any] | None = None  # the dashboard of the bi tab


class ReportDefinition(_Model):
    report: ReportInfo
    params: list[ParamInfo]
    can_export: bool
    can_refresh: bool
    auto_run: bool  # no parameter waits for the user
    views: list[ViewInfo]
    default_view: ViewKey


class SnapshotInfo(_Model):
    id: int
    created_at: datetime
    expires_at: datetime
    created_at_display: str  # in app.timezone, for the "data as of" line
    cache_hit: bool
    is_stale: bool


class GridColumnInfo(_Model):
    name: str
    label: str
    type: str
    format: str
    width: int | None
    align: str
    sortable: bool
    filterable: bool
    frozen: bool
    footer: str | None


class RunResponse(_Model):
    report: ReportInfo
    snapshot: SnapshotInfo | None = None  # none for the bi view
    view: ViewKey
    params: dict[str, Any]
    columns: list[GridColumnInfo] | None = None
    rows: list[list[Any]] | None = None
    row_count: int
    html_url: str | None = None
    bi: dict[str, Any] | None = None
