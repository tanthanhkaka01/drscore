"""Admin pages (milestone 8), built on sqladmin (BSD-3-Clause, a Tabler-based admin for SQLAlchemy).

Every table an administrator maintains has a page: report groups, reports, parameters, columns,
row filters, datasources, users, user attributes, roles, group and report grants. Logs and the
snapshot history are read-only. Every change made here is written to ``drs_audit_log``.

Only DRS administrators get in: sqladmin's authentication hook reads the normal DRS session. The
admin pages follow the user's language (cookie ``drs_locale``); sqladmin's own strings and the
labels below are translated in ``drscore/admin/translations/vi.po``.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from markupsafe import escape
from pydantic import ValidationError
from sqladmin import Admin, ModelView, action
from sqladmin import i18n as sqladmin_i18n
from sqladmin.authentication import AuthenticationBackend
from sqladmin.flash import flash
from sqladmin.i18n import I18nConfig, lazy_gettext as _
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from wtforms import PasswordField, SelectField, TextAreaField

from drscore.audit import audit, row_snapshot
from drscore.auth import service as auth
from drscore.db.engine import Database
from drscore.db.models import (
    AccessLog,
    AuditLog,
    Datasource,
    GrantGroup,
    GrantReport,
    Report,
    ReportColumn,
    ReportGroup,
    ReportParam,
    ReportRowFilter,
    ReportSnapshotHistory,
    Role,
    User,
    UserAttribute,
)
from drscore.errors import DRSError
from drscore.settings import AppSettings

TRANSLATIONS = Path(__file__).resolve().parent / "translations"
TEMPLATES = Path(__file__).resolve().parent / "templates"

REPORT_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,49}$")
GROUP_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,49}$")
PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class L(str):
    """A column label: compares and hashes as its English text (sqladmin keys dictionaries by
    label), shows in the user's language when rendered."""

    def __str__(self) -> str:
        return sqladmin_i18n.gettext(str.__str__(self))

    def __html__(self) -> str:
        return str(escape(str(self)))


class AdminInputError(ValueError):
    """A value an administrator typed is not accepted; sqladmin shows the message on the form."""


def _install_vietnamese() -> None:
    """Adds the Vietnamese catalogue to sqladmin's translations (it ships no Vietnamese)."""
    from babel.messages.mofile import write_mo
    from babel.messages.pofile import read_po
    from babel.support import Translations

    with (TRANSLATIONS / "vi.po").open("rb") as fh:
        catalog = read_po(fh, locale="vi")
    buffer = io.BytesIO()
    write_mo(buffer, catalog)
    buffer.seek(0)
    sqladmin_i18n.translations["vi"] = Translations(fp=buffer, domain="admin")
    if "vi" not in sqladmin_i18n.SUPPORTED_LOCALES:
        sqladmin_i18n.SUPPORTED_LOCALES.append("vi")


# --------------------------------------------------------------------------------------------
# Authentication: the DRS session, administrators only

class DrsAdminAuth(AuthenticationBackend):
    def __init__(self, database: Database, settings: AppSettings, secret: str):
        super().__init__(secret_key=secret, session_cookie="drs_admin_flash", same_site="lax",
                         https_only=settings.session.cookie_secure)
        self.database = database
        self.settings = settings

    async def login(self, request: Request) -> bool:
        return False  # sign-in happens on the portal's own page

    async def logout(self, request: Request) -> Response:
        return RedirectResponse("/", status_code=303)

    async def authenticate(self, request: Request) -> Response | bool:
        def load():
            with self.database.session() as s:
                return auth.load_session(s, request.cookies.get("drs_session"), self.settings)

        info = await run_in_threadpool(load)
        if info is None:
            return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
        if info.must_change_password:  # the known password administers nothing: replace it first
            return RedirectResponse("/account", status_code=303)
        if not info.is_admin:
            return Response("403 - administrators only", status_code=403, media_type="text/plain")
        request.state.admin_user = info.username
        return True


# --------------------------------------------------------------------------------------------
# Audit

class AuditedView(ModelView):
    """A model page whose every create / update / delete goes to drs_audit_log."""

    page_size = 50
    page_size_options = [25, 50, 100, 200]
    can_export = False
    database: Database
    settings: AppSettings

    key_attr: str | None = None  # the business key written to the audit log, e.g. report_code

    def object_key(self, obj: Any) -> str:
        return str(getattr(obj, self.key_attr)) if self.key_attr else str(obj)

    def actor(self, request: Request) -> str:
        return getattr(request.state, "admin_user", "admin")

    def check(self, data: dict, obj: Any, is_created: bool, request: Request) -> None:
        """Validation of one view; raise AdminInputError to refuse the save."""

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        request.state.audit_before = None if is_created else row_snapshot(model)
        self.check(data, model, is_created, request)

    async def after_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        def write():
            with self.database.session() as s:
                audit(s, self.actor(request), "CREATE" if is_created else "UPDATE", model.__tablename__,
                      self.object_key(model), before=getattr(request.state, "audit_before", None),
                      after=row_snapshot(model))
        await run_in_threadpool(write)

    async def on_model_delete(self, model: Any, request: Request) -> None:
        request.state.audit_before = row_snapshot(model)
        request.state.audit_key = self.object_key(model)

    async def after_model_delete(self, model: Any, request: Request) -> None:
        def write():
            with self.database.session() as s:
                audit(s, self.actor(request), "DELETE", model.__tablename__,
                      getattr(request.state, "audit_key", "?"), before=getattr(request.state, "audit_before", None))
        await run_in_threadpool(write)


class ReadOnlyView(ModelView):
    column_labels = {
        "logged_at": L("Time"), "username": L("User"), "action": L("Action"), "report_code": L("Report"),
        "view_key": L("View"), "status": L("Status"), "error_code": L("Error"), "error_message": L("Error message"),
        "row_count": L("Rows"), "duration_ms": L("Duration (ms)"), "client_ip": L("Client IP"),
        "user_agent": L("Browser"), "request_id": L("Request id"), "params_json": L("Parameters"),
        "cache_hit": L("From cache"), "snapshot_id": L("Snapshot"), "actor": L("By"), "object_type": L("Table"),
        "object_key": L("Object"), "before_json": L("Before"), "after_json": L("After"),
        "created_at": L("Created"), "archived_at": L("Moved to history"), "archive_reason": L("Reason"),
        "expires_at": L("Expired at"), "created_by": L("Run by"),
    }
    can_create = False
    can_edit = False
    can_delete = False
    can_export = False
    page_size = 50
    category = _("Logs")


def _back(request: Request, identity: str) -> RedirectResponse:
    target = request.headers.get("referer") or f"/admin/{identity}/list"
    return RedirectResponse(target, status_code=303)


def _pks(request: Request) -> list[str]:
    return [p for p in request.query_params.get("pks", "").split(",") if p]


def _choices(values) -> list[tuple[str, str]]:
    return [(v, v) for v in values]


def _problem(exc: DRSError, about_defaults: str) -> str:
    """Why an action that runs a report with its default values failed. A parameter that is required
    and has no default is not an error of the report: the message says what can be done."""
    if exc.code != "PARAM_INVALID":
        return f"{exc.code}: {exc.admin_detail or exc.message}"
    return (f"{exc.code}: {'; '.join((exc.detail or {}).values())}. This runs with the default values: "
            f"give these parameters a default, {about_defaults}.")


# --------------------------------------------------------------------------------------------
# Reports

class GroupAdmin(AuditedView, model=ReportGroup):
    key_attr = "group_code"
    name = _("Report group")
    name_plural = _("Report groups")
    icon = "fa-solid fa-folder"
    category = _("Reports")
    column_list = [ReportGroup.group_code, ReportGroup.group_name, ReportGroup.icon, ReportGroup.sort_order,
                   ReportGroup.is_active]
    column_searchable_list = [ReportGroup.group_code, ReportGroup.group_name]
    column_sortable_list = [ReportGroup.group_code, ReportGroup.sort_order]
    column_default_sort = [(ReportGroup.sort_order, False)]
    form_columns = [ReportGroup.group_code, ReportGroup.group_name, ReportGroup.description, ReportGroup.icon,
                    ReportGroup.sort_order, ReportGroup.is_active]
    column_labels = {"group_code": L("Code"), "group_name": L("Name"), "description": L("Description"),
                     "icon": L("Icon"), "sort_order": L("Order"), "is_active": L("Active")}

    def check(self, data, obj, is_created, request):
        if not GROUP_CODE_RE.match(data.get("group_code") or ""):
            raise AdminInputError("Code: upper-case letters, digits and _, starting with a letter")


class ReportAdmin(AuditedView, model=Report):
    key_attr = "report_code"
    name = _("Report")
    name_plural = _("Reports")
    icon = "fa-solid fa-table"
    category = _("Reports")
    column_list = [Report.report_code, Report.report_name, Report.group, Report.report_type, Report.datasource,
                   Report.cache_ttl_seconds, Report.sort_order, Report.is_active]
    column_searchable_list = [Report.report_code, Report.report_name]
    column_sortable_list = [Report.report_code, Report.report_name, Report.sort_order]
    column_default_sort = [(Report.report_code, False)]
    form_columns = [Report.report_code, Report.report_name, Report.description, Report.group, Report.report_type,
                    Report.datasource, Report.query_text, Report.grid_enabled, Report.html_design_uri,
                    Report.bi_design_uri, Report.bi_dataset_table, Report.cache_ttl_seconds,
                    Report.serve_stale_on_error, Report.timeout_seconds, Report.max_rows, Report.is_restricted,
                    Report.sort_order, Report.is_active]
    form_overrides = {"query_text": TextAreaField, "report_type": SelectField}
    form_args = {"report_type": {"choices": _choices(("GRID", "HTML", "BI"))}}
    form_widget_args = {"query_text": {"rows": 12, "class": "form-control font-monospace"}}
    column_labels = {
        "report_code": L("Code"), "report_name": L("Name"), "description": L("Description"), "group": L("Group"),
        "report_type": L("Opens on"), "datasource": L("Datasource"), "query_text": L("SQL / procedure"),
        "grid_enabled": L("Grid view"), "html_design_uri": L("HTML design"), "bi_design_uri": L("BI dashboard"),
        "bi_dataset_table": L("BI dataset table"), "cache_ttl_seconds": L("Retention (s)"),
        "serve_stale_on_error": L("Serve old data on error"), "timeout_seconds": L("Timeout (s)"),
        "max_rows": L("Max rows"), "is_restricted": L("Restricted"), "sort_order": L("Order"), "is_active": L("Active"),
    }
    column_details_exclude_list = [Report.report_id, Report.group_id, Report.datasource_id]

    def check(self, data, obj, is_created, request):
        if not REPORT_CODE_RE.match(data.get("report_code") or ""):
            raise AdminInputError("Code: ^[A-Z][A-Z0-9_]{1,49}$")
        actor = self.actor(request)
        if is_created:
            obj.created_by = actor
        obj.updated_by = actor

    async def after_model_change(self, data, model, is_created, request):
        await super().after_model_change(data, model, is_created, request)
        await self._flash_findings(request, model.report_id)

    async def _flash_findings(self, request: Request, report_id: int) -> None:
        from drscore.reports.validate import check_report

        def findings():
            with self.database.session() as s:
                return check_report(s, s.get(Report, report_id), self.settings)
        for level, message in await run_in_threadpool(findings):
            if level in ("ERROR", "WARN"):
                flash(request, message, "danger" if level == "ERROR" else "warning", title=level)

    @action(name="check", label=_("Check"), add_in_detail=True, add_in_list=True)
    async def validate_action(self, request: Request) -> Response:
        for pk in _pks(request):
            report_id = int(pk)
            await self._flash_findings(request, report_id)
        flash(request, "OK", "success", title=str(_("Check")))
        return _back(request, self.identity)

    @action(name="test-run", label=_("Test run"), add_in_detail=True, add_in_list=True)
    async def test_run(self, request: Request) -> Response:
        """Runs the report as the administrator (default parameters, grid view) and reports the
        number of rows, or the error with its technical reason."""
        from drscore.reports.service import run_for_user

        for pk in _pks(request):
            def run(report_id=int(pk)):
                with self.database.session() as s:
                    code = s.get(Report, report_id).report_code
                try:
                    served = run_for_user(self.database, self.settings, self.actor(request), code, {},
                                          refresh=True, view="grid")
                    return code, None, f"{len(served.rows)} rows, {len(served.outcome.columns)} columns"
                except DRSError as exc:
                    return code, exc.code, _problem(exc, 'or use "Open report" to choose them')
            code, error, message = await run_in_threadpool(run)
            flash(request, message, "danger" if error else "success", title=code)
        return _back(request, self.identity)

    @action(name="open", label=_("Open report"), add_in_detail=True, add_in_list=True)
    async def open_report(self, request: Request) -> Response:
        """To the report as a user gets it - the page where its parameters are chosen. "Test run"
        cannot choose: it runs with the default values only."""
        pks = _pks(request)

        def code():
            with self.database.session() as s:
                report = s.get(Report, int(pks[0]))
                return report.report_code if report else None
        found = await run_in_threadpool(code) if pks else None
        return RedirectResponse(f"/reports/{found}", status_code=303) if found else _back(request, self.identity)

    @action(name="load-bi-dataset", label=_("Create BI table"), add_in_detail=True, add_in_list=True)
    async def load_bi_dataset(self, request: Request) -> Response:
        """Runs the report with its default parameters and writes the result to the table named in
        "BI dataset table" (what ``cache warm`` does). A dashboard is designed on that table, and
        until the dashboard is registered the report has no Dashboard tab that would create it."""
        from drscore.bi import dataset as bi_dataset
        from drscore.reports.service import get_result, prepare

        for pk in _pks(request):
            def load(report_id=int(pk)):
                code = str(report_id)
                try:
                    with self.database.session() as s:
                        report = s.get(Report, report_id)
                        code, table = report.report_code, report.bi_dataset_table
                        if not table:
                            return code, "warning", 'fill in "BI dataset table" first'
                        prep = prepare(s, report, {}, self.settings)
                    outcome = get_result(self.database, prep, self.settings, username=self.actor(request))
                    state = "loaded" if bi_dataset.refresh(self.database, report_id, table, outcome) else "unchanged"
                    return code, "success", (f"{bi_dataset.display_name(self.database, table)}: {len(outcome.rows)} "
                                             f"rows, {len(outcome.columns)} columns ({state})")
                except DRSError as exc:
                    return code, "danger", _problem(exc, "the table is made from them")
            code, level, message = await run_in_threadpool(load)
            flash(request, message, level, title=code)
        return _back(request, self.identity)


class ParamAdmin(AuditedView, model=ReportParam):
    name = _("Parameter")
    name_plural = _("Parameters")
    icon = "fa-solid fa-sliders"
    category = _("Reports")
    column_list = [ReportParam.report, ReportParam.param_name, ReportParam.label, ReportParam.data_type,
                   ReportParam.input_kind, ReportParam.is_required, ReportParam.default_value, ReportParam.sort_order,
                   ReportParam.is_active]
    column_searchable_list = [ReportParam.param_name, ReportParam.label]
    column_sortable_list = [ReportParam.param_name, ReportParam.sort_order]
    form_columns = [ReportParam.report, ReportParam.param_name, ReportParam.label, ReportParam.data_type,
                    ReportParam.input_kind, ReportParam.is_required, ReportParam.default_value,
                    ReportParam.options_json, ReportParam.options_query, ReportParam.options_datasource,
                    ReportParam.multi_bind_mode, ReportParam.min_value, ReportParam.max_value,
                    ReportParam.max_length, ReportParam.regex, ReportParam.sort_order, ReportParam.is_active]
    form_overrides = {"data_type": SelectField, "input_kind": SelectField, "multi_bind_mode": SelectField,
                      "options_query": TextAreaField}
    form_args = {"data_type": {"choices": _choices(("text", "int", "decimal", "date", "datetime", "bool"))},
                 "input_kind": {"choices": _choices(("input", "select", "multiselect"))},
                 "multi_bind_mode": {"choices": _choices(("expand", "csv"))},
                 # Owner, 2026-10-07: a new parameter is required unless the box is unticked.
                 "is_required": {"default": True}}
    form_widget_args = {"options_query": {"rows": 4, "class": "form-control font-monospace"}}
    column_labels = {"report": L("Report"), "param_name": L("Name in SQL"), "label": L("Label"),
                     "data_type": L("Type"), "input_kind": L("Input"), "is_required": L("Required"),
                     "default_value": L("Default value"), "options_json": L("Options (JSON)"),
                     "options_query": L("Options query"), "options_datasource": L("Options datasource"),
                     "multi_bind_mode": L("Multi-value binding"), "min_value": L("Minimum"),
                     "max_value": L("Maximum"), "max_length": L("Max length"), "regex": L("Pattern"),
                     "sort_order": L("Order"), "is_active": L("Active")}

    def object_key(self, obj):
        return f"{obj.report.report_code if obj.report else obj.report_id}.{obj.param_name}"

    def check(self, data, obj, is_created, request):
        from drscore.reports.params import DefaultContext, ParamDef, ValueError_, default_for

        if not PARAM_NAME_RE.match(data.get("param_name") or ""):
            raise AdminInputError("Name in SQL: ^[a-z][a-z0-9_]*$")
        if data.get("default_value"):
            try:
                default_for(ParamDef(name=data["param_name"], label=data.get("label") or "x",
                                     data_type=data.get("data_type") or "text",
                                     input_kind=data.get("input_kind") or "input",
                                     default_value=data["default_value"]), DefaultContext(now=datetime.now()))
            except ValueError_ as exc:
                raise AdminInputError(f"Default value: {exc}") from exc


class ColumnAdmin(AuditedView, model=ReportColumn):
    name = _("Grid column")
    name_plural = _("Grid columns")
    icon = "fa-solid fa-table-columns"
    category = _("Reports")
    column_list = [ReportColumn.report, ReportColumn.field_name, ReportColumn.label, ReportColumn.display_format,
                   ReportColumn.width_px, ReportColumn.is_visible, ReportColumn.is_exported, ReportColumn.sort_order]
    column_searchable_list = [ReportColumn.field_name, ReportColumn.label]
    form_columns = [ReportColumn.report, ReportColumn.field_name, ReportColumn.label, ReportColumn.data_type,
                    ReportColumn.display_format, ReportColumn.width_px, ReportColumn.align, ReportColumn.is_visible,
                    ReportColumn.is_exported, ReportColumn.is_sortable, ReportColumn.is_filterable,
                    ReportColumn.is_frozen, ReportColumn.footer_aggregate, ReportColumn.sort_order]
    form_overrides = {"align": SelectField, "footer_aggregate": SelectField, "data_type": SelectField}
    form_args = {"align": {"choices": [("", "-")] + _choices(("left", "center", "right"))},
                 "footer_aggregate": {"choices": [("", "-")] + _choices(("sum", "avg", "count", "min", "max"))},
                 "data_type": {"choices": [("", "-")] + _choices(("text", "int", "decimal", "float", "bool", "date",
                                                                    "datetime", "time"))}}
    column_labels = {"report": L("Report"), "field_name": L("Result column"), "label": L("Label"),
                     "data_type": L("Type"), "display_format": L("Format"), "width_px": L("Width (px)"),
                     "align": L("Align"), "is_visible": L("Visible"), "is_exported": L("Exported"),
                     "is_sortable": L("Sortable"), "is_filterable": L("Filterable"), "is_frozen": L("Frozen"),
                     "footer_aggregate": L("Footer"), "sort_order": L("Order")}

    def object_key(self, obj):
        return f"{obj.report.report_code if obj.report else obj.report_id}.{obj.field_name}"

    def check(self, data, obj, is_created, request):
        from drscore.render.grid import FORMAT_RE

        for key in ("align", "footer_aggregate", "data_type"):
            if data.get(key) == "":
                data[key] = None
        if data.get("display_format") and not FORMAT_RE.match(data["display_format"]):
            raise AdminInputError("Format: text, integer, number:N, percent:N, date, datetime, time")


class RowFilterAdmin(AuditedView, model=ReportRowFilter):
    name = _("Row filter")
    name_plural = _("Row filters")
    icon = "fa-solid fa-filter"
    category = _("Reports")
    column_list = [ReportRowFilter.report, ReportRowFilter.column_name, ReportRowFilter.attr_name]
    form_columns = [ReportRowFilter.report, ReportRowFilter.column_name, ReportRowFilter.attr_name]
    column_labels = {"report": L("Report"), "column_name": L("Result column"), "attr_name": L("User attribute")}

    def object_key(self, obj):
        return f"{obj.report.report_code if obj.report else obj.report_id}.{obj.column_name}"


# --------------------------------------------------------------------------------------------
# Datasources

class DatasourceAdmin(AuditedView, model=Datasource):
    key_attr = "datasource_code"
    name = _("Datasource")
    name_plural = _("Datasources")
    icon = "fa-solid fa-database"
    category = _("Datasources")
    column_list = [Datasource.datasource_code, Datasource.datasource_name, Datasource.db_type, Datasource.host,
                   Datasource.database_name, Datasource.username, Datasource.is_active]
    column_searchable_list = [Datasource.datasource_code, Datasource.datasource_name, Datasource.host]
    form_columns = [Datasource.datasource_code, Datasource.datasource_name, Datasource.description,
                    Datasource.db_type, Datasource.host, Datasource.port, Datasource.instance_name,
                    Datasource.database_name, Datasource.oracle_connect_by, Datasource.default_schema,
                    Datasource.auth_method, Datasource.username, Datasource.ssl_mode,
                    Datasource.trust_server_certificate, Datasource.connect_timeout_seconds, Datasource.driver,
                    Datasource.options_json, Datasource.is_active]
    column_details_exclude_list = [Datasource.secret_ref, Datasource.datasource_id]
    form_overrides = {"db_type": SelectField, "auth_method": SelectField, "ssl_mode": SelectField,
                      "oracle_connect_by": SelectField}
    form_args = {"db_type": {"choices": _choices(("mssql", "oracle", "postgresql", "sqlite"))},
                 "auth_method": {"choices": _choices(("password", "windows", "none"))},
                 "ssl_mode": {"choices": [("", "-")] + _choices(("disable", "prefer", "require", "verify"))},
                 "oracle_connect_by": {"choices": [("", "-")] + _choices(("service_name", "sid"))}}
    column_labels = {
        "datasource_code": L("Code"), "datasource_name": L("Name"), "description": L("Description"),
        "db_type": L("Type"), "host": L("Server"), "port": L("Port"), "instance_name": L("SQL Server instance"),
        "database_name": L("Database / service"), "oracle_connect_by": L("Oracle connect by"),
        "default_schema": L("Default schema"), "auth_method": L("Authentication"), "username": L("User"),
        "ssl_mode": L("SSL"), "trust_server_certificate": L("Trust server certificate"),
        "connect_timeout_seconds": L("Connect timeout (s)"), "driver": L("Driver"),
        "options_json": L("Other options (JSON)"), "is_active": L("Active"), "new_password": L("New password"),
    }

    async def scaffold_form(self, rules=None):
        form = await super().scaffold_form(rules)
        form.new_password = PasswordField(_("New password"), render_kw={"class": "form-control",
                                          "autocomplete": "new-password"}, description=_(
            "Leave empty to keep the stored password. Stored encrypted, never shown."))
        return form

    def check(self, data, obj, is_created, request):
        from drscore.datasources import secrets
        from drscore.datasources.spec import DatasourceSpec

        password = data.pop("new_password", None)
        for key in ("ssl_mode", "oracle_connect_by"):
            if data.get(key) == "":
                data[key] = None
        try:
            spec = DatasourceSpec(
                code=data.get("datasource_code"), name=data.get("datasource_name"), description=data.get("description"),
                db_type=data.get("db_type"), host=data.get("host") or None, port=data.get("port"),
                instance_name=data.get("instance_name") or None, database=data.get("database_name") or "",
                oracle_connect_by=data.get("oracle_connect_by"), default_schema=data.get("default_schema") or None,
                auth_method=data.get("auth_method") or "password", username=data.get("username") or None,
                ssl_mode=data.get("ssl_mode"), trust_server_certificate=bool(data.get("trust_server_certificate")),
                connect_timeout_seconds=data.get("connect_timeout_seconds"), driver=data.get("driver") or None,
                options={str(k): str(v) for k, v in (data.get("options_json") or {}).items()})
        except ValidationError as exc:
            raise AdminInputError("; ".join(f"{'.'.join(map(str, e['loc'])) or 'datasource'}: {e['msg']}"
                                            for e in exc.errors())) from exc
        data["auth_method"] = spec.auth_method
        if password:
            obj.secret_ref = secrets.encrypt(password)
        if spec.auth_method != "password":
            obj.secret_ref = None

    @action(name="test-connection", label=_("Test connection"), add_in_detail=True, add_in_list=True)
    async def test_connection(self, request: Request) -> Response:
        from drscore.datasources.connectors import SourceInfo
        from drscore.reports import executor

        for pk in _pks(request):
            def test(datasource_id=int(pk)):
                with self.database.session() as s:
                    src = SourceInfo.of(s.get(Datasource, datasource_id))
                try:
                    r = executor.run_query(src, executor.ping_sql(src.db_type), (), {}, timeout_seconds=30,
                                           max_rows=1, max_bytes=1024, tz=self.settings.tz)
                    return src.code, None, f"OK, {r.duration_ms} ms"
                except DRSError as exc:
                    return src.code, exc.code, f"{exc.code}: {exc.admin_detail or exc.message}"
            code, error, message = await run_in_threadpool(test)
            flash(request, message, "danger" if error else "success", title=code)
        return _back(request, self.identity)


# --------------------------------------------------------------------------------------------
# Users, roles, grants

class UserAdmin(AuditedView, model=User):
    key_attr = "username"
    name = _("User")
    name_plural = _("Users")
    icon = "fa-solid fa-user"
    category = _("Users and access")
    column_list = [User.username, User.display_name, User.email, User.is_admin, User.is_active, User.roles,
                   User.last_login_at]
    column_searchable_list = [User.username, User.display_name, User.email]
    column_sortable_list = [User.username, User.last_login_at]
    form_columns = [User.username, User.display_name, User.email, User.sso_subject, User.is_admin, User.is_active,
                    User.must_change_password, User.roles]
    column_details_exclude_list = [User.password_hash, User.user_id]
    column_labels = {"username": L("User name"), "display_name": L("Name"), "email": L("E-mail"),
                     "sso_subject": L("SSO subject"), "is_admin": L("Administrator"), "is_active": L("Active"),
                     "must_change_password": L("Must change password"),
                     "roles": L("Roles"), "last_login_at": L("Last sign-in"), "new_password": L("New password"),
                     "failed_login_count": L("Failed sign-ins"), "locked_until": L("Locked until")}

    async def scaffold_form(self, rules=None):
        form = await super().scaffold_form(rules)
        form.new_password = PasswordField(_("New password"), render_kw={"class": "form-control",
                                          "autocomplete": "new-password"}, description=_(
            "Leave empty to keep the current password. Changing it ends the user's sessions."))
        return form

    def check(self, data, obj, is_created, request):
        password = data.pop("new_password", None)
        data["username"] = auth.normalise_username(data.get("username") or "")
        if not re.match(r"^[a-z0-9][a-z0-9._@-]{0,99}$", data["username"]):
            raise AdminInputError("User name: letters, digits, '.', '_', '@', '-'")
        if password:
            try:
                auth.check_password_policy(password, self.settings)
            except auth.PasswordPolicyError as exc:
                raise AdminInputError(str(exc)) from exc
            obj.password_hash = auth.hash_password(password)
            request.state.end_sessions = True
        if not is_created and obj.is_active and data.get("is_active") is False:
            request.state.end_sessions = True

    async def after_model_change(self, data, model, is_created, request):
        await super().after_model_change(data, model, is_created, request)
        if getattr(request.state, "end_sessions", False) and not is_created:
            def end():
                with self.database.session() as s:
                    auth.end_all_sessions(s, model.user_id)
            await run_in_threadpool(end)

    @action(name="unlock", label=_("Unlock"), add_in_detail=True, add_in_list=True)
    async def unlock(self, request: Request) -> Response:
        for pk in _pks(request):
            def do(user_id=int(pk)):
                with self.database.session() as s:
                    user = s.get(User, user_id)
                    user.failed_login_count, user.locked_until = 0, None
                    audit(s, self.actor(request), "USER_UNLOCK", "drs_user", user.username)
                    return user.username
            flash(request, await run_in_threadpool(do), "success", title=str(_("Unlock")))
        return _back(request, self.identity)


class AttributeAdmin(AuditedView, model=UserAttribute):
    name = _("User attribute")
    name_plural = _("User attributes")
    icon = "fa-solid fa-tags"
    category = _("Users and access")
    column_list = [UserAttribute.user, UserAttribute.attr_name, UserAttribute.attr_value]
    column_searchable_list = [UserAttribute.attr_name, UserAttribute.attr_value]
    form_columns = [UserAttribute.user, UserAttribute.attr_name, UserAttribute.attr_value]
    column_labels = {"user": L("User"), "attr_name": L("Attribute"), "attr_value": L("Value (* = every value)")}

    def object_key(self, obj):
        return f"{obj.user.username if obj.user else obj.user_id}.{obj.attr_name}={obj.attr_value}"


class RoleAdmin(AuditedView, model=Role):
    key_attr = "role_code"
    name = _("Role")
    name_plural = _("Roles")
    icon = "fa-solid fa-users"
    category = _("Users and access")
    column_list = [Role.role_code, Role.role_name, Role.is_active, Role.users]
    column_searchable_list = [Role.role_code, Role.role_name]
    form_columns = [Role.role_code, Role.role_name, Role.is_active, Role.users]
    column_labels = {"role_code": L("Code"), "role_name": L("Name"), "is_active": L("Active"), "users": L("Members")}

    def check(self, data, obj, is_created, request):
        data["role_code"] = (data.get("role_code") or "").upper()
        if not GROUP_CODE_RE.match(data["role_code"]):
            raise AdminInputError("Code: upper-case letters, digits and _, starting with a letter")


class _GrantAdmin(AuditedView):
    category = _("Users and access")
    column_labels = {"group": L("Group"), "report": L("Report"), "user": L("User"), "role": L("Role"),
                     "can_export": L("Can export"), "can_refresh": L("Can refresh"), "granted_by": L("Granted by"),
                     "granted_at": L("Granted at")}

    def object_key(self, obj):
        target = getattr(obj, "group", None) or getattr(obj, "report", None)
        who = f"user {obj.user}" if obj.user else f"role {obj.role}"
        return f"{target} -> {who}"

    def check(self, data, obj, is_created, request):
        if bool(data.get("user")) == bool(data.get("role")):
            raise AdminInputError("Choose exactly one of User or Role")
        obj.granted_by = self.actor(request)


class GrantGroupAdmin(_GrantAdmin, model=GrantGroup):
    name = _("Group grant")
    name_plural = _("Group grants")
    icon = "fa-solid fa-key"
    column_list = [GrantGroup.group, GrantGroup.user, GrantGroup.role, GrantGroup.can_export, GrantGroup.can_refresh,
                   GrantGroup.granted_by, GrantGroup.granted_at]
    form_columns = [GrantGroup.group, GrantGroup.user, GrantGroup.role, GrantGroup.can_export, GrantGroup.can_refresh]


class GrantReportAdmin(_GrantAdmin, model=GrantReport):
    name = _("Report grant")
    name_plural = _("Report grants")
    icon = "fa-solid fa-key"
    column_list = [GrantReport.report, GrantReport.user, GrantReport.role, GrantReport.can_export,
                   GrantReport.can_refresh, GrantReport.granted_by, GrantReport.granted_at]
    form_columns = [GrantReport.report, GrantReport.user, GrantReport.role, GrantReport.can_export,
                    GrantReport.can_refresh]


# --------------------------------------------------------------------------------------------
# Logs (read-only)

class AccessLogAdmin(ReadOnlyView, model=AccessLog):
    name = _("Access log")
    name_plural = _("Access log")
    icon = "fa-solid fa-eye"
    column_list = [AccessLog.logged_at, AccessLog.username, AccessLog.action, AccessLog.report_code,
                   AccessLog.view_key, AccessLog.status, AccessLog.error_code, AccessLog.row_count,
                   AccessLog.duration_ms, AccessLog.client_ip]
    column_searchable_list = [AccessLog.username, AccessLog.report_code, AccessLog.action]
    column_sortable_list = [AccessLog.logged_at, AccessLog.username, AccessLog.report_code]
    column_default_sort = [(AccessLog.log_id, True)]


class AuditLogAdmin(ReadOnlyView, model=AuditLog):
    name = _("Audit log")
    name_plural = _("Audit log")
    icon = "fa-solid fa-clipboard-list"
    column_list = [AuditLog.logged_at, AuditLog.actor, AuditLog.action, AuditLog.object_type, AuditLog.object_key]
    column_searchable_list = [AuditLog.actor, AuditLog.object_key, AuditLog.object_type]
    column_sortable_list = [AuditLog.logged_at, AuditLog.actor]
    column_default_sort = [(AuditLog.audit_id, True)]


class HistoryAdmin(ReadOnlyView, model=ReportSnapshotHistory):
    name = _("Snapshot history")
    name_plural = _("Snapshot history")
    icon = "fa-solid fa-clock-rotate-left"
    column_list = [ReportSnapshotHistory.snapshot_id, ReportSnapshotHistory.report_code,
                   ReportSnapshotHistory.created_at, ReportSnapshotHistory.archived_at,
                   ReportSnapshotHistory.archive_reason, ReportSnapshotHistory.row_count]
    column_searchable_list = [ReportSnapshotHistory.report_code]
    column_default_sort = [(ReportSnapshotHistory.snapshot_id, True)]
    column_details_exclude_list = [ReportSnapshotHistory.rows_json]


VIEWS = [GroupAdmin, ReportAdmin, ParamAdmin, ColumnAdmin, RowFilterAdmin, DatasourceAdmin, UserAdmin, AttributeAdmin,
         RoleAdmin, GrantGroupAdmin, GrantReportAdmin, AccessLogAdmin, AuditLogAdmin, HistoryAdmin]


def mount(app, database: Database, settings: AppSettings, secret: str) -> Admin:
    """Mounts the admin pages at /admin."""
    _install_vietnamese()
    admin = Admin(
        app, session_maker=database.sessionmaker, base_url="/admin", title=f"{settings.app.name} - Admin",
        templates_dir=str(TEMPLATES), authentication_backend=DrsAdminAuth(database, settings, secret),
        i18n_config=I18nConfig(default_locale=settings.app.default_locale, language_cookie_name="drs_locale",
                               language_header_name=None),
    )
    for view in VIEWS:
        view.database = database
        view.settings = settings
        admin.add_view(view)
    return admin
