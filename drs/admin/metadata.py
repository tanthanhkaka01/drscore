"""``metadata export`` / ``metadata import``: report definitions as one JSON file (spec section 18).

The way reports designed on SQLite reach PostgreSQL (or one DRS reaches another). The file holds
datasources (connection settings), report groups, reports with their parameters, columns and row
filters, users (name, e-mail, attributes), roles with their members, and grants. Rows refer to each
other by code (``group_code``, ``datasource_code``, ``username``...), never by id, so ids may differ
between the two databases.

Never in the file: password hashes, encrypted datasource passwords (``enc:``), sessions, caches,
logs. A datasource whose password was encrypted arrives without one (``datasource set-password``);
an ``env:NAME`` reference is a name, not a secret, and is kept. Users arrive without a password.

Import upserts by code: an existing row is updated, a missing one created; nothing outside the file
is deleted, except that the parameters, columns and row filters of a report in the file become
exactly those of the file. Every created / updated row is written to the audit log. The import is
one transaction: on any error nothing is changed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import Numeric, select
from sqlalchemy.orm import Session

from drs.audit import audit, row_snapshot
from drs.db.models import (
    Datasource,
    GrantGroup,
    GrantReport,
    Report,
    ReportColumn,
    ReportGroup,
    ReportParam,
    ReportRowFilter,
    Role,
    User,
    UserAttribute,
)

FORMAT = "drs-metadata"
VERSION = 1

# Columns that never leave the database: ids and foreign-key ids (replaced by codes), secrets,
# timestamps and counters that belong to the database the row lives in.
NEVER = {
    "datasource_id", "group_id", "report_id", "param_id", "column_id", "row_filter_id", "user_id", "role_id",
    "grant_id", "options_datasource_id", "password_hash", "secret_ref", "created_at", "updated_at",
    "created_by", "updated_by", "granted_at", "failed_login_count", "locked_until", "last_login_at",
    "must_change_password",  # about a password, and passwords do not travel
}


STAMPS = {"updated_at", "updated_by"}  # not a change by themselves


class MetadataError(ValueError):
    """The file cannot be imported; nothing was changed."""


def _columns(model) -> list[str]:
    return [a.key for a in sa_inspect(model).column_attrs if a.key not in NEVER]


def _values(obj) -> dict[str, Any]:
    out = {}
    for key in _columns(type(obj)):
        value = getattr(obj, key)
        out[key] = str(value) if isinstance(value, Decimal) else value
    return out


# --------------------------------------------------------------------------------------------
# File format (validated with Pydantic on import)

class _Row(BaseModel):
    """A table row: the columns of the model, by name; unknown names are refused."""

    model_config = ConfigDict(extra="allow")
    MODEL: ClassVar[Any] = None

    @model_validator(mode="after")
    def _known_columns(self):
        allowed = set(_columns(self.MODEL)) | set(type(self).model_fields)
        unknown = sorted(set(self.model_extra or {}) - allowed)
        if unknown:
            raise ValueError(f"unknown field(s) for {self.MODEL.__tablename__}: {', '.join(unknown)}")
        return self

    def columns(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class DatasourceRow(_Row):
    MODEL: ClassVar[Any] = Datasource
    datasource_code: str
    secret_env: str | None = None  # the NAME of an environment variable holding the password


class GroupRow(_Row):
    MODEL: ClassVar[Any] = ReportGroup
    group_code: str


class ParamRow(_Row):
    MODEL: ClassVar[Any] = ReportParam
    param_name: str
    options_datasource: str | None = None


class ColumnRow(_Row):
    MODEL: ClassVar[Any] = ReportColumn
    field_name: str


class RowFilterRow(_Row):
    MODEL: ClassVar[Any] = ReportRowFilter
    column_name: str


class ReportRow(_Row):
    MODEL: ClassVar[Any] = Report
    report_code: str
    group: str
    datasource: str | None = None
    params: list[ParamRow] = []
    columns_: list[ColumnRow] = Field([], alias="columns")
    row_filters: list[RowFilterRow] = []


class UserRow(_Row):
    MODEL: ClassVar[Any] = User
    username: str
    attributes: dict[str, list[str]] = {}


class RoleRow(_Row):
    MODEL: ClassVar[Any] = Role
    role_code: str
    members: list[str] = []


class GrantRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str  # group_code or report_code
    user: str | None = None
    role: str | None = None
    can_export: bool = True
    can_refresh: bool = False

    @model_validator(mode="after")
    def _one_principal(self):
        if (self.user is None) == (self.role is None):
            raise ValueError(f"grant on {self.target}: exactly one of user or role")
        return self


class MetadataFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["drs-metadata"]
    version: Literal[1]
    exported_at: str | None = None
    exported_from: str | None = None
    datasources: list[DatasourceRow] = []
    groups: list[GroupRow] = []
    reports: list[ReportRow] = []
    users: list[UserRow] = []
    roles: list[RoleRow] = []
    group_grants: list[GrantRow] = []
    report_grants: list[GrantRow] = []


# --------------------------------------------------------------------------------------------
# Export

def export(s: Session, *, reports: Iterable[str] | None = None, source: str | None = None) -> dict[str, Any]:
    """The metadata as a JSON-ready dict. ``reports``: only these report codes (with their group,
    datasources, grants and the users / roles those grants name)."""
    wanted = set(reports or ())
    report_rows = list(s.scalars(select(Report).order_by(Report.report_code)))
    if wanted:
        missing = wanted - {r.report_code for r in report_rows}
        if missing:
            raise MetadataError(f"unknown report(s): {', '.join(sorted(missing))}")
        report_rows = [r for r in report_rows if r.report_code in wanted]
    report_ids = {r.report_id for r in report_rows}

    group_grants = list(s.scalars(select(GrantGroup).order_by(GrantGroup.grant_id)))
    report_grants = [g for g in s.scalars(select(GrantReport).order_by(GrantReport.grant_id))
                     if g.report_id in report_ids]
    if wanted:
        group_ids = {r.group_id for r in report_rows}
        groups = [g for g in s.scalars(select(ReportGroup).order_by(ReportGroup.group_code)) if g.group_id in group_ids]
        group_grants = [g for g in group_grants if g.group_id in group_ids]
    else:
        groups = list(s.scalars(select(ReportGroup).order_by(ReportGroup.group_code)))

    params = {rid: [] for rid in report_ids}
    for p in s.scalars(select(ReportParam).order_by(ReportParam.sort_order, ReportParam.param_name)):
        if p.report_id in params:
            params[p.report_id].append(p)
    columns = {rid: [] for rid in report_ids}
    for c in s.scalars(select(ReportColumn).order_by(ReportColumn.sort_order, ReportColumn.field_name)):
        if c.report_id in columns:
            columns[c.report_id].append(c)
    filters = {rid: [] for rid in report_ids}
    for f in s.scalars(select(ReportRowFilter).order_by(ReportRowFilter.column_name)):
        if f.report_id in filters:
            filters[f.report_id].append(f)

    ds_ids = {r.datasource_id for r in report_rows if r.datasource_id}
    ds_ids |= {p.options_datasource_id for ps in params.values() for p in ps if p.options_datasource_id}
    datasources = [d for d in s.scalars(select(Datasource).order_by(Datasource.datasource_code))
                   if not wanted or d.datasource_id in ds_ids]

    roles = list(s.scalars(select(Role).order_by(Role.role_code)))
    users = list(s.scalars(select(User).order_by(User.username)))
    if wanted:
        role_ids = {g.role_id for g in [*group_grants, *report_grants] if g.role_id}
        roles = [r for r in roles if r.role_id in role_ids]
        user_ids = {g.user_id for g in [*group_grants, *report_grants] if g.user_id}
        user_ids |= {u.user_id for r in roles for u in r.users}
        users = [u for u in users if u.user_id in user_ids]

    def datasource(d: Datasource) -> dict:
        row = _values(d)
        if d.secret_ref and d.secret_ref.startswith("env:"):
            row["secret_env"] = d.secret_ref[4:]
        return row

    def report(r: Report) -> dict:
        return {
            **_values(r), "group": r.group.group_code, "datasource": r.datasource.datasource_code if r.datasource else None,
            "params": [{**_values(p), "options_datasource": p.options_datasource.datasource_code
                        if p.options_datasource else None} for p in params[r.report_id]],
            "columns": [_values(c) for c in columns[r.report_id]],
            "row_filters": [_values(f) for f in filters[r.report_id]],
        }

    def user(u: User) -> dict:
        attributes: dict[str, list[str]] = {}
        for a in sorted(u.attributes, key=lambda a: (a.attr_name, a.attr_value)):
            attributes.setdefault(a.attr_name, []).append(a.attr_value)
        return {**_values(u), "attributes": attributes}

    def grant(g, target: str) -> dict:
        return {"target": target, "user": g.user.username if g.user else None,
                "role": g.role.role_code if g.role else None, "can_export": g.can_export, "can_refresh": g.can_refresh}

    return {
        "format": FORMAT, "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exported_from": source,
        "datasources": [datasource(d) for d in datasources],
        "groups": [_values(g) for g in groups],
        "reports": [report(r) for r in report_rows],
        "users": [user(u) for u in users],
        "roles": [{**_values(r), "members": sorted(u.username for u in r.users)} for r in roles],
        "group_grants": [grant(g, g.group.group_code) for g in group_grants],
        "report_grants": [grant(g, g.report.report_code) for g in report_grants],
    }


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n"


# --------------------------------------------------------------------------------------------
# Import

@dataclass
class ImportResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: int = 0
    removed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def parse(text: str) -> MetadataFile:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MetadataError(f"not a JSON file: {exc}") from exc
    try:
        return MetadataFile.model_validate(raw)
    except ValidationError as exc:
        lines = [f"{'.'.join(map(str, e['loc'])) or 'file'}: {e['msg']}" for e in exc.errors()[:20]]
        raise MetadataError("the file is not a DRS metadata export:\n  " + "\n  ".join(lines)) from exc


class _Importer:
    def __init__(self, s: Session, actor: str):
        self.s = s
        self.actor = actor
        self.result = ImportResult()

    def upsert(self, model, key: dict[str, Any], values: dict[str, Any], label: str, **refs):
        obj = self.s.scalars(select(model).filter_by(**key)).first()
        before = row_snapshot(obj) if obj is not None else None
        if obj is None:
            obj = model(**key)
            self.s.add(obj)
        for name, value in {**values, **refs}.items():
            column = sa_inspect(model).columns.get(name)
            if column is not None and value is not None and isinstance(column.type, Numeric):
                value = Decimal(str(value))
            setattr(obj, name, value)
        changed = before is None or {k: v for k, v in row_snapshot(obj).items() if k not in STAMPS} != \
            {k: v for k, v in before.items() if k not in STAMPS}
        if changed and hasattr(obj, "updated_by"):
            obj.updated_by = self.actor
        self.s.flush()
        after = row_snapshot(obj)
        if before is None:
            self.result.created.append(label)
            audit(self.s, self.actor, "IMPORT", model.__tablename__, label, after=after)
        elif changed:
            self.result.updated.append(label)
            audit(self.s, self.actor, "IMPORT", model.__tablename__, label, before=before, after=after)
        else:
            self.result.unchanged += 1
        return obj

    def lookup(self, model, column: str, code: str, what: str):
        obj = self.s.scalars(select(model).where(getattr(model, column) == code)).first()
        if obj is None:
            raise MetadataError(f"{what} {code} is neither in the file nor in this database")
        return obj

    def children(self, model, report: Report, key: str, rows: list, label: str, extra=lambda row: {}):
        """Makes the parameters / columns / row filters of a report exactly those of the file."""
        keep = {getattr(row, key) for row in rows}
        for old in self.s.scalars(select(model).where(model.report_id == report.report_id)):
            if getattr(old, key) not in keep:
                self.result.removed.append(f"{label} {report.report_code}.{getattr(old, key)}")
                audit(self.s, self.actor, "IMPORT_DELETE", model.__tablename__,
                      f"{report.report_code}.{getattr(old, key)}", before=row_snapshot(old))
                self.s.delete(old)
        self.s.flush()
        for row in rows:
            self.upsert(model, {"report_id": report.report_id, key: getattr(row, key)}, row.columns(),
                        f"{label} {report.report_code}.{getattr(row, key)}", **extra(row))


def import_metadata(s: Session, data: MetadataFile, actor: str) -> ImportResult:
    """Applies a parsed file in the caller's transaction (roll it back for a dry run)."""
    from drs.reports.validate import BI_TABLE_RE, PARAM_NAME_RE, REPORT_CODE_RE

    imp = _Importer(s, actor)
    for r in data.reports:
        if not REPORT_CODE_RE.match(r.report_code):
            raise MetadataError(f"report {r.report_code}: the code does not match ^[A-Z][A-Z0-9_]{{1,49}}$")
        bad = [p.param_name for p in r.params if not PARAM_NAME_RE.match(p.param_name)]
        if bad:
            raise MetadataError(f"report {r.report_code}: parameter name(s) {', '.join(bad)} not ^[a-z][a-z0-9_]*$")
        table = r.columns().get("bi_dataset_table")
        if table and not BI_TABLE_RE.match(table):
            raise MetadataError(f"report {r.report_code}: bi_dataset_table {table!r} is not a plain name")

    for d in data.datasources:
        existing = s.scalars(select(Datasource).where(Datasource.datasource_code == d.datasource_code)).first()
        refs = {}
        if d.secret_env:
            refs["secret_ref"] = f"env:{d.secret_env}"
        elif existing is None and d.columns().get("auth_method", "password") == "password" \
                and d.columns().get("db_type") != "sqlite":
            imp.result.notes.append(f"datasource {d.datasource_code}: no password - run "
                                    f"`python -m drs datasource set-password {d.datasource_code}`")
        imp.upsert(Datasource, {"datasource_code": d.datasource_code}, d.columns(),
                   f"datasource {d.datasource_code}", **refs)

    for g in data.groups:
        imp.upsert(ReportGroup, {"group_code": g.group_code}, g.columns(), f"group {g.group_code}")

    for r in data.reports:
        group = imp.lookup(ReportGroup, "group_code", r.group, "group")
        ds = imp.lookup(Datasource, "datasource_code", r.datasource, "datasource") if r.datasource else None
        existing = s.scalars(select(Report).where(Report.report_code == r.report_code)).first()
        refs = {"group_id": group.group_id, "datasource_id": ds.datasource_id if ds else None}
        if existing is None:
            refs["created_by"] = actor
        report = imp.upsert(Report, {"report_code": r.report_code}, r.columns(), f"report {r.report_code}", **refs)

        def param_refs(p: ParamRow):
            src = imp.lookup(Datasource, "datasource_code", p.options_datasource, "datasource") \
                if p.options_datasource else None
            return {"options_datasource_id": src.datasource_id if src else None}

        imp.children(ReportParam, report, "param_name", r.params, "parameter", param_refs)
        imp.children(ReportColumn, report, "field_name", r.columns_, "column")
        imp.children(ReportRowFilter, report, "column_name", r.row_filters, "row filter")

    for u in data.users:
        existing = s.scalars(select(User).where(User.username == u.username)).first()
        values = u.columns()
        if existing is not None:
            values.pop("is_admin", None)  # an import never changes who is an administrator
        elif values.get("is_admin"):
            imp.result.notes.append(f"user {u.username}: created as administrator, without a password")
        user = imp.upsert(User, {"username": u.username}, values, f"user {u.username}")
        have = {(a.attr_name, a.attr_value) for a in user.attributes}
        for name, attr_values in u.attributes.items():
            for value in attr_values:
                if (name, value) not in have:
                    s.add(UserAttribute(user_id=user.user_id, attr_name=name, attr_value=value))
                    imp.result.created.append(f"attribute {u.username}.{name}={value}")
                    audit(s, actor, "IMPORT", "drs_user_attribute", f"{u.username}.{name}={value}")
        s.flush()

    for r in data.roles:
        role = imp.upsert(Role, {"role_code": r.role_code}, r.columns(), f"role {r.role_code}")
        for username in r.members:
            user = imp.lookup(User, "username", username, "user")
            if user not in role.users:
                role.users.append(user)
                imp.result.created.append(f"member {r.role_code} <- {username}")
                audit(s, actor, "IMPORT", "drs_user_role", f"{r.role_code} <- {username}")
        s.flush()

    def grants(model, target_model, target_column, target_attr, rows: list[GrantRow], label: str):
        for g in rows:
            target = imp.lookup(target_model, target_column, g.target, label)
            key = {target_attr: getattr(target, target_attr)}
            who = f"user {g.user}" if g.user else f"role {g.role}"
            if g.user:
                key["user_id"] = imp.lookup(User, "username", g.user, "user").user_id
            else:
                key["role_id"] = imp.lookup(Role, "role_code", g.role, "role").role_id
            existing = s.scalars(select(model).filter_by(**key)).first()
            refs = {} if existing is not None else {"granted_by": actor}
            imp.upsert(model, key, {"can_export": g.can_export, "can_refresh": g.can_refresh},
                       f"{label} grant {g.target} -> {who}", **refs)

    grants(GrantGroup, ReportGroup, "group_code", "group_id", data.group_grants, "group")
    grants(GrantReport, Report, "report_code", "report_id", data.report_grants, "report")
    return imp.result
