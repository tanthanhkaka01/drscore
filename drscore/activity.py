"""The activity log (``drs_activity_log``): every request a user makes, with its outcome.

Written by ``ActivityMiddleware`` for every change (POST / PUT / PATCH / DELETE, and the admin pages'
actions), every page opened (``[activity] page_opens``) and every refused or failed request, on the
portal, the design pages, the admin pages and the public links. What was sent is kept with it -
the form or JSON fields, never a password, token, key or CSRF token, each value cut at
``[activity] max_field_chars`` - so the log says what was changed as well as that it was.

``drs_access_log`` (report opens, runs, exports) and ``drs_audit_log`` (rows before / after a change)
stay as they are: this log is the trail of the requests that led to them.

Writing it never holds up or breaks a request: the row is written after the response is sent, in its
own transaction, and a failure goes to the application log only.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable
from urllib.parse import parse_qsl

from starlette.concurrency import run_in_threadpool

log = logging.getLogger(__name__)

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
SKIP_PREFIXES = ("/static/", "/admin/statics/", "/healthz", "/favicon")
BODY_LIMIT = 256 * 1024  # bytes of a request body read for the log; a larger body is noted, not kept
SECRET_KEY_RE = re.compile(r"pass|secret|token|api[_-]?key|csrf|credential", re.I)

# What each action is called on the log page: (Vietnamese, English).
ACTIONS: dict[str, tuple[str, str]] = {
    "auth.login": ("Đăng nhập", "Signed in"),
    "auth.logout": ("Đăng xuất", "Signed out"),
    "auth.sso": ("Đăng nhập SSO", "SSO sign-in"),
    "page.open": ("Mở trang", "Opened a page"),
    "api.read": ("Trang đọc dữ liệu", "A page read data"),
    "account.password": ("Đổi mật khẩu", "Changed the password"),
    "locale.switch": ("Đổi ngôn ngữ", "Switched the language"),
    "report.open": ("Mở báo cáo", "Opened a report"),
    "report.run": ("Chạy báo cáo", "Ran a report"),
    "report.export": ("Xuất báo cáo", "Exported a report"),
    "report.grid": ("Xem lưới báo cáo", "Viewed a report's grid"),
    "report.html": ("Xem bố cục HTML", "Viewed an HTML layout"),
    "report.options": ("Tải lựa chọn tham số", "Loaded a parameter's options"),
    "report.bi_token": ("Mở dashboard", "Opened a dashboard"),
    "report.bi_members": ("Chạy báo cáo của dashboard", "Ran a dashboard's reports"),
    "design.new": ("Thiết kế: tạo báo cáo mới", "Design: new report"),
    "design.edit": ("Thiết kế: đề xuất sửa báo cáo", "Design: proposed a change"),
    "design.save": ("Thiết kế: lưu bản nháp", "Design: saved a draft"),
    "design.test": ("Thiết kế: chạy thử", "Design: test run"),
    "design.fill_columns": ("Thiết kế: điền cột", "Design: filled the columns"),
    "design.submit": ("Thiết kế: gửi duyệt", "Design: submitted for approval"),
    "design.withdraw": ("Thiết kế: rút lại", "Design: withdrew"),
    "design.delete": ("Thiết kế: xóa bản nháp", "Design: deleted a draft"),
    "design.publish": ("Thiết kế: xuất bản", "Design: published"),
    "design.approve": ("Thiết kế: duyệt", "Design: approved"),
    "design.reject": ("Thiết kế: từ chối", "Design: rejected"),
    "bi.dataset": ("BI: tạo dataset", "BI: made the dataset"),
    "bi.link": ("BI: gắn dashboard", "BI: linked a dashboard"),
    "bi.params": ("BI: lưu tham số dashboard", "BI: saved the dashboard's parameters"),
    "bi.unlink": ("BI: gỡ dashboard", "BI: removed the dashboard"),
    "bi.sync": ("BI: đồng bộ Superset", "BI: synced with Superset"),
    "bi.superset": ("BI: mở Superset", "BI: opened Superset"),
    "public.open": ("Link công khai: mở", "Public link: opened"),
    "public.run": ("Link công khai: chạy báo cáo", "Public link: report run"),
    "public.export": ("Link công khai: xuất file", "Public link: file exported"),
    "publiclink.add": ("Tạo link công khai", "Public link made"),
    "publiclink.change": ("Sửa / tắt / bật link công khai", "Public link changed / off / on"),
    "public.view": ("Link công khai: xem", "Public link: viewed"),
    "public.list_save": ("Link công khai: lưu danh sách chọn", "Public link: saved the choice list"),
    "feedback.create": ("Góp ý: gửi", "Feedback: sent"),
    "feedback.comment": ("Góp ý: trả lời", "Feedback: answered"),
    "feedback.update": ("Góp ý: đổi trạng thái", "Feedback: changed"),
    "feedback.checked": ("Góp ý: đã kiểm tra", "Feedback: checked"),
    "feedback.file": ("Góp ý: mở file", "Feedback: opened a file"),
    "ai.ask": ("Trợ lý AI: hỏi", "AI agent: asked"),
    "ai.conversation": ("Trợ lý AI: mở cuộc trò chuyện", "AI agent: opened a conversation"),
    "log.export": ("Nhật ký: xuất Excel", "Log: exported to Excel"),
    "notify.test": ("Thông báo: gửi thử", "Notices: sent a test"),
    "admin.create": ("Quản trị: thêm", "Admin: added"),
    "admin.edit": ("Quản trị: sửa", "Admin: changed"),
    "admin.delete": ("Quản trị: xóa", "Admin: deleted"),
    "admin.action": ("Quản trị: thao tác", "Admin: action"),
    "admin.export": ("Quản trị: xuất", "Admin: exported"),
    "admin.import": ("Quản trị: nhập", "Admin: imported"),
    "change": ("Thao tác khác", "Other change"),
}


def label(action: str, locale: str) -> str:
    names = ACTIONS.get(action)
    return (names[0] if locale == "vi" else names[1]) if names else action


Body = dict[str, Any] | None
# (methods, path pattern, action or a function of the sent fields, the group giving the object key)
Route = tuple[set[str], re.Pattern, str | Callable[[Body], str], int | None]
_GET = {"GET", "HEAD"}
_POST = {"POST"}


def _r(methods: set[str], pattern: str, action: str | Callable[[Body], str], key: int | None = None) -> Route:
    return methods, re.compile(f"^{pattern}$"), action, key


def _design_save(body: Body) -> str:
    chosen = str((body or {}).get("action") or "save")
    return {"test": "design.test", "fill_columns": "design.fill_columns"}.get(chosen, "design.save")


ROUTES: list[Route] = [
    _r(_POST, r"/login", "auth.login"),
    _r(_POST, r"/logout", "auth.logout"),
    _r(_GET | _POST, r"/auth/sso/.*", "auth.sso"),
    _r(_POST, r"/account/password", "account.password"),
    _r(_GET, r"/locale/([a-z]{2})", "locale.switch", 1),
    _r(_GET, r"/reports/([^/]+)", "report.open", 1),
    _r(_GET, r"/reports/([^/]+)/html", "report.html", 1),
    _r(_POST, r"/api/reports/([^/]+)/run", "report.run", 1),
    _r(_GET, r"/api/reports/([^/]+)/export", "report.export", 1),
    _r(_GET, r"/api/reports/([^/]+)/grid", "report.grid", 1),
    _r(_GET, r"/api/reports/([^/]+)/params/[^/]+/options", "report.options", 1),
    _r(_POST, r"/api/reports/([^/]+)/bi-token", "report.bi_token", 1),
    _r(_POST, r"/api/reports/([^/]+)/bi-members", "report.bi_members", 1),
    _r(_POST, r"/design/new", "design.new"),
    _r(_POST, r"/design/reports/([^/]+)/edit", "design.edit", 1),
    _r(_POST, r"/design/drafts/(\d+)", _design_save, 1),
    _r(_POST, r"/design/drafts/(\d+)/submit", "design.submit", 1),
    _r(_POST, r"/design/drafts/(\d+)/withdraw", "design.withdraw", 1),
    _r(_POST, r"/design/drafts/(\d+)/delete", "design.delete", 1),
    _r(_POST, r"/design/drafts/(\d+)/publish", "design.publish", 1),
    _r(_POST, r"/design/drafts/(\d+)/approve", "design.approve", 1),
    _r(_POST, r"/design/drafts/(\d+)/reject", "design.reject", 1),
    _r(_POST, r"/design/reports/([^/]+)/bi/dataset", "bi.dataset", 1),
    _r(_POST, r"/design/reports/([^/]+)/bi/link", "bi.link", 1),
    _r(_POST, r"/design/reports/([^/]+)/bi/params", "bi.params", 1),
    _r(_POST, r"/design/reports/([^/]+)/bi/unlink", "bi.unlink", 1),
    _r(_POST, r"/design/reports/([^/]+)/bi/sync", "bi.sync", 1),
    _r(_GET, r"/design/reports/([^/]+)/superset(/dashboard)?", "bi.superset", 1),
    _r(_GET, r"/p/([^/]+)", "public.open", 1),
    _r(_GET, r"/p/([^/]+)/(html|bi-token|list)", "public.view", 1),
    _r(_POST, r"/p/([^/]+)/list", "public.list_save", 1),
    _r(_POST, r"/p/([^/]+)/api/run", "public.run", 1),
    _r(_GET, r"/p/([^/]+)/api/params/[^/]+/options", "public.view", 1),
    _r(_GET, r"/p/([^/]+)/export", "public.export", 1),
    _r(_GET, r"/system/links", "page.open"),
    _r(_POST, r"/system/links", "publiclink.add"),
    _r(_POST, r"/system/links/(\d+)/[a-z]+", "publiclink.change", 1),
    _r(_POST, r"/feedback", "feedback.create"),
    _r(_POST, r"/feedback/(\d+)/comments", "feedback.comment", 1),
    _r(_POST, r"/feedback/(\d+)", "feedback.update", 1),
    _r(_POST, r"/feedback/(\d+)/checked", "feedback.checked", 1),
    _r(_GET, r"/feedback/(\d+)/files/\d+", "feedback.file", 1),
    _r(_POST, r"/api/ai/ask", "ai.ask"),
    _r(_GET, r"/api/ai/conversations/(\d+)", "ai.conversation", 1),
    _r(_GET, r"/system/log\.xlsx", "log.export"),
    _r(_POST, r"/system/notices/test", "notify.test"),
    _r(_POST, r"/admin/([^/]+)/create", "admin.create", 1),
    _r(_POST, r"/admin/([^/]+)/edit/(.+)", "admin.edit", 1),
    _r({"DELETE"}, r"/admin/([^/]+)/delete", "admin.delete", 1),
    _r(_GET | _POST, r"/admin/([^/]+)/action/[^/]+", "admin.action", 1),
    _r(_GET, r"/admin/([^/]+)/export/[^/]+", "admin.export", 1),
    _r(_POST, r"/admin/([^/]+)/import", "admin.import", 1),
]

# GET requests that are themselves an action (they change something, or read data) and are always logged.
ALWAYS = {"locale.switch", "report.export", "log.export", "admin.action", "admin.export", "bi.superset", "public.open",
          "public.export",
          "feedback.file", "auth.sso"}
# GET requests a page makes by itself (scripts, frames): logged only when refused or failed, since
# drs_access_log already has the report's VIEW / RUN rows.
QUIET = {"api.read", "report.grid", "report.html", "report.options", "public.view", "ai.conversation"}


def _public_path(path: str) -> str:
    """A public link's token is the right to read: only its first characters are logged."""
    return re.sub(r"^/p/([A-Za-z0-9_-]{6})[A-Za-z0-9_-]*", r"/p/\1…", path)


def classify(method: str, path: str, body: Body) -> tuple[str, str | None]:
    """(action, object key) of a request."""
    for methods, pattern, action, key in ROUTES:
        if method in methods:
            m = pattern.match(path)
            if m:
                name = action(body) if callable(action) else action
                obj = m.group(key) if key else None
                if obj and name.startswith("public."):
                    obj = obj[:6] + "…"
                if name.startswith("admin.") and "pks" in (body or {}):
                    obj = f"{obj} {body['pks']}"[:200]
                return name, obj
    if method in _GET and path.startswith("/api/"):
        return "api.read", None  # a page's own JSON read: logged when refused or failed
    return ("page.open" if method in _GET else "change"), None


def should_log(method: str, action: str, status: int, page_opens: bool) -> bool:
    if status >= 400 or method in UNSAFE or action in ALWAYS:
        return True
    if action in QUIET or method not in _GET:
        return False
    return page_opens


def outcome_of(status: int) -> str:
    if status in (401, 403):
        return "DENIED"
    return "ERROR" if status >= 400 else "OK"


def _mask(value: Any, limit: int, key: str = "") -> Any:
    if key and SECRET_KEY_RE.search(key):
        return "***" if value not in (None, "", []) else value
    if isinstance(value, dict):
        return {k: _mask(v, limit, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v, limit, key) for v in value[:200]]
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"… ({len(value)} characters)"
    return value


def read_body(content_type: str, raw: bytes, truncated: bool, limit: int) -> Body:
    """The fields sent, as JSON values, secrets masked. A file upload keeps only its size."""
    if truncated:
        return {"_body": f"more than {BODY_LIMIT} bytes, not kept"}
    if not raw:
        return None
    ctype = content_type.split(";")[0].strip().lower()
    try:
        if ctype == "application/json":
            data = json.loads(raw.decode("utf-8"))
            data = data if isinstance(data, dict) else {"_json": data}
        elif ctype == "application/x-www-form-urlencoded":
            data: dict[str, Any] = {}
            for k, v in parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True):
                data[k] = [*data[k], v] if isinstance(data.get(k), list) else ([data[k], v] if k in data else v)
        elif ctype == "multipart/form-data":
            return {"_multipart": f"{len(raw)} bytes (files are kept by the feature that received them)"}
        else:
            return {"_body": f"{ctype or 'unknown'}, {len(raw)} bytes"}
    except (ValueError, UnicodeDecodeError):
        return {"_body": f"{ctype}, {len(raw)} bytes, not readable"}
    data.pop("csrf_token", None)
    return _mask(data, limit)


def _username(scope: dict, body: Body, action: str) -> str:
    state = scope.get("state") or {}
    auth = state.get("auth")
    if auth is not None:
        return auth.username
    if state.get("admin_user"):
        return state["admin_user"]
    if action == "auth.login" and body and isinstance(body.get("username"), str):
        return body["username"].strip().lower()[:100] or "-"
    return "-"


def _session_user(cookie_header: str) -> str | None:
    """The user of the request's session cookie, for a route that did not load the session (/locale)."""
    from http.cookies import SimpleCookie

    from drscore.auth.service import load_session
    from drscore.db import get_database
    from drscore.settings import get_settings

    try:
        jar = SimpleCookie()
        jar.load(cookie_header)
        token = jar.get("drs_session")
        if token is None:
            return None
        with get_database().session() as s:
            info = load_session(s, token.value, get_settings().app)
        return info.username if info else None
    except Exception:
        return None


def write(row: dict) -> None:
    from drscore.db import get_database
    from drscore.db.models import ActivityLog

    try:
        with get_database().session() as s:
            s.add(ActivityLog(**row))
    except Exception:
        log.exception("could not write the activity log")


class ActivityMiddleware:
    """Pure ASGI, so the row is written once the whole response has been sent."""

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        cfg = self.settings.app.activity
        if scope["type"] != "http" or not cfg.enabled or scope["path"].startswith(SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return
        method = scope["method"]
        chunks: list[bytes] = []
        size = 0
        truncated = False

        async def receive_and_keep():
            nonlocal size, truncated
            message = await receive()
            if message["type"] == "http.request" and method in UNSAFE and not truncated:
                part = message.get("body", b"")
                size += len(part)
                if size > BODY_LIMIT:
                    truncated = True
                    chunks.clear()
                else:
                    chunks.append(part)
            return message

        status = 500
        started = time.perf_counter()

        async def send_and_watch(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive_and_keep, send_and_watch)
        finally:
            try:
                await self._log(scope, method, status, started, b"".join(chunks), truncated)
            except Exception:
                log.exception("could not build the activity log row")

    async def _log(self, scope, method, status, started, raw, truncated):
        cfg = self.settings.app.activity
        path = scope["path"]
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        body = read_body(headers.get("content-type", ""), raw, truncated, cfg.max_field_chars) if method in UNSAFE else None
        query = scope.get("query_string", b"").decode("latin-1")
        if method == "DELETE" and query:  # sqladmin sends the rows to delete in the query string
            body = {**(body or {}), **dict(parse_qsl(query))}
        action, obj = classify(method, path, body)
        if action == "admin.action" and query:
            body = {**(body or {}), **dict(parse_qsl(query))}
            action, obj = classify(method, path, body)
        if not should_log(method, action, status, cfg.page_opens):
            return
        username = _username(scope, body, action)
        if username == "-" and "cookie" in headers and not action.startswith(("auth.", "public.")):
            username = await run_in_threadpool(_session_user, headers["cookie"]) or "-"
        from drscore.logsetup import request_id

        rid = request_id.get()
        client = scope.get("client")
        row = {
            "username": username[:100], "action": action, "object_key": (obj or None) and obj[:200],
            "method": method[:10], "path": _public_path(path)[:400],
            "query_text": (_mask_query(query, cfg.max_field_chars) or None) if query else None,
            "status_code": status, "outcome": outcome_of(status), "detail_json": body,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "client_ip": (client[0] if client else None),
            "user_agent": (headers.get("user-agent") or "")[:400] or None,
            "request_id": None if rid in ("-", None) else rid[:32],
        }
        await run_in_threadpool(write, row)


def _mask_query(query: str, limit: int) -> str:
    pairs = [(k, "***" if SECRET_KEY_RE.search(k) else v) for k, v in parse_qsl(query, keep_blank_values=True)]
    from urllib.parse import urlencode

    return urlencode(pairs)[:2000]
