"""Public links: a report opened by a secret URL, without signing in (drscore.publiclink).

Three addresses, all GET and without a session: the page, the guest token of its dashboard (view
``bi``) and its HTML design (view ``html``). None trusts the browser with anything but the token and
the editable parameters: the run, the snapshot and the dashboard all come from the link. Any link
that cannot be used - unknown, revoked, expired, ``[public] enabled = false`` - answers the same
"not found".

A link allowed to tick a choice list (drscore.choicelist) has one more address, ``/p/{token}/list``:
GET answers the list and its ticks, POST saves the ticks the page sends - JSON only, from the page's
own origin, only items of that list - and writes the list's table for the reports again.

An HTML page never waits for its source: the page shows the newest snapshot the link has, even an
old one, and fetches a fresh copy behind it (public.js), which takes its place once it has loaded.

A link that is not public (``is_public`` False, ORD 402) is the same address for signed-in readers
only: without a session it opens the sign-in page, which comes back to it; the report runs as the
reader, whose own rights are checked first (403 without them). ``[public] enabled = false`` closes
the public links only.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy import select
from starlette.background import BackgroundTask
from pydantic import BaseModel

from drscore import choicelist, publiclink
from drscore.audit import log_access
from drscore import siteurl
from drscore.db import get_database
from drscore.db.models import ReportParam, User
from drscore.errors import DRSError
from drscore.settings import get_settings
from drscore.web import views
from drscore.web.deps import client_ip, current_session
from drscore.web.routes.pages import SECURITY_HEADERS_CSP, origin_of
from drscore.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter()

PRIVATE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}
MAX_GRID_ROWS = 5000


def _headers(response, frame_origin: str | None = None, framers: str | None = None):
    """The link's page headers. ``framers``: the other sites the link lets show it in a frame
    (``frame_ancestors``); X-Frame-Options then is left out (app.py), the CSP says who may."""
    response.headers.update(PRIVATE)
    csp = SECURITY_HEADERS_CSP
    if frame_origin:
        csp = csp.replace("frame-ancestors 'self'", f"frame-src 'self' {frame_origin}; frame-ancestors 'self'")
    if frame_origin or framers:
        response.headers["Content-Security-Policy"] = publiclink.frame_csp(csp, framers)
    return response


def _html_headers(nonce: str, framers: str | None = None) -> dict[str, str]:
    """The design's page (as /reports/{code}/html): its scripts run only with this nonce. It is the
    frame of the link's page, so the sites that may frame that page are its ancestors too."""
    return PRIVATE | {"Content-Security-Policy": publiclink.frame_csp(
        f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'", framers)}


def _not_found(request: Request):
    settings = get_settings().app
    return _headers(render(request, "public.html", status_code=404, with_menu=False,
                           locale=settings.public.locale or None, theme=settings.public.theme,
                           title=None, missing=True, page=None))


def _link_of(token: str) -> dict[str, Any] | None:
    """What a request needs of the link, read in one short session; None when it cannot be used."""
    database = get_database()
    with database.session() as s:
        link = publiclink.resolve(s, token)
        if link is None:
            return None
        report = link.report
        editable = []
        for name in link.editable_json or []:
            p = next((p for p in report_params(s, report.report_id) if p.param_name == name), None)
            if p is not None:
                editable.append({"name": name, "label": p.label or name, "type": p.data_type,
                                 "default": _default_text(p), "options": [o["value"] for o in _options(p)]})
        return {"id": link.link_id, "hint": link.token_hint, "code": report.report_code, "name": report.report_name,
                "run_as": link.run_as.username, "public": link.is_public, "view": link.view_key, "title": link.title,
                "refresh": link.refresh_seconds, "editable": editable, "link": link, "frame": link.frame_ancestors,
                "list": link.choice_list.list_code if link.choice_list and link.choice_list.is_active else None}


def _usable(found: dict[str, Any] | None) -> bool:
    """Found, and - for a public link - public links on ([public] enabled)."""
    return found is not None and (get_settings().app.public.enabled or not found["public"])


def _reader(request: Request, found: dict[str, Any]) -> tuple[str, str]:
    """(the user the report runs as, the name the access log keeps). A public link runs as its
    ``run_as``. One that is not runs as the signed-in reader: no session raises AUTH_REQUIRED (a
    page then opens the sign-in page, which comes back here), no right on the report FORBIDDEN."""
    if found["public"]:
        return found["run_as"], f"public:{found['hint']}"
    from drscore.authz.permissions import require_report

    with get_database().session() as s:
        info = current_session(request, s)
        user = s.get(User, info.user_id) if info else None
        if user is None or not user.is_active:
            raise DRSError("AUTH_REQUIRED")
        if user.must_change_password:
            raise DRSError("PASSWORD_CHANGE_REQUIRED")
        require_report(s, user, found["code"])
        return user.username, user.username


def report_params(session, report_id: int) -> list[ReportParam]:
    from sqlalchemy import select

    return list(session.scalars(select(ReportParam).where(ReportParam.report_id == report_id, ReportParam.is_active)
                                .order_by(ReportParam.sort_order)))


def _default_text(p: ReportParam) -> str:
    """The parameter's default as the reader's input shows it (``@month_start-2m`` -> ``2026-08-01``), so the
    bar shows the values the report runs with; '' when it has none or depends on the reader (``@user.``)."""
    from datetime import datetime

    from drscore.reports.params import DefaultContext, ParamDef, default_for, to_text

    try:
        value = default_for(ParamDef.of(p), DefaultContext(now=datetime.now(get_settings().app.tz).replace(tzinfo=None)))
    except Exception:  # a default that cannot be read: an empty input, the run says why
        return ""
    if value is None or isinstance(value, list):
        return ""
    text = to_text(value)
    return text if isinstance(text, str) else str(text)


def _options(p: ReportParam) -> list[dict[str, Any]]:
    from drscore.reports.params import ParamDef, static_options

    try:
        return static_options(ParamDef.of(p)) if p.options_json else []
    except Exception:  # an options list that cannot be read: a plain text input instead
        return []


@router.get("/p/{token}")
def public_page(token: str, request: Request):
    settings = get_settings().app
    found = _link_of(token)
    if not _usable(found):
        return _not_found(request)
    who, actor = _reader(request, found)
    database = get_database()
    with database.session() as s:
        publiclink.touch(s, found["id"])
        params = publiclink.params_for(found["link"], dict(request.query_params))
    code, ip = found["code"], client_ip(request)
    page: dict[str, Any] = {"name": found["title"] or found["name"], "refresh": found["refresh"],
                            "editable": [dict(e, value=params.get(e["name"]) or e["default"])
                                         for e in found["editable"]],
                            "error": None, "bi": None, "grid": None}
    frame_origin = None
    try:
        if found["view"] == "html":
            # No run here: the frame shows the newest snapshot at once, public.js asks for a fresh one.
            query = {e["name"]: params[e["name"]] for e in found["editable"] if e["name"] in params}
            newest = views.newest_snapshot(database, settings, who, code, "html", params)
            base = f"{publiclink.PATH}{token}/html"
            page["html"] = {"src": base + "?" + urlencode(query | {"cached": "1"}),
                            "fresh": base + (f"?{urlencode(query)}" if query else ""),
                            "current": newest is not None and newest[1]}
            log_access(actor, "VIEW", "OK", report_code=code, client_ip=ip,
                       view_key=found["view"], params_json=params)
            return _headers(render(request, "public.html", with_menu=False, locale=settings.public.locale or None,
                                   theme=settings.public.theme, title=page["name"], missing=False, page=page),
                            framers=found["frame"])
        if found["view"] == "bi":
            answer = views.run(database, settings, who, code, params, False, ip, view="bi")
            bi = answer.bi or {}
            if bi.get("mode") == "LINK":
                page["bi"] = {"mode": "LINK", "url": bi["url"]}
                frame_origin = origin_of(bi["url"])
            else:
                query = {e["name"]: params[e["name"]] for e in found["editable"] if e["name"] in params}
                page["bi"] = {"mode": "EMBED", "dashboard_id": bi["dashboard_id"], "superset_url": bi["superset_url"],
                              "token_url": f"{publiclink.PATH}{token}/bi-token" + (f"?{urlencode(query)}" if query else "")}
                frame_origin = origin_of(bi["superset_url"])
        else:
            return _report_page(request, token, found, who, actor, params)
        if answer.snapshot is not None:
            page["as_of"] = answer.snapshot.created_at_display
    except DRSError as exc:
        log.info("public link %s... of %s: %s %s", found["hint"], code, exc.code, exc.admin_detail or "")
        page["error"] = {"code": exc.code, "message": exc.message,
                         "detail": exc.detail if exc.code == "PARAM_INVALID" else None}
    log_access(actor, "VIEW", "ERROR" if page["error"] else "OK", report_code=code,
               client_ip=ip, view_key=found["view"], params_json=params)
    return _headers(render(request, "public.html", with_menu=False, locale=settings.public.locale or None,
                           theme=settings.public.theme,
                           title=page["name"], missing=False, page=page), frame_origin, found["frame"])


def _link_definition(found: dict[str, Any], who: str, params: dict[str, Any]):
    """The report's definition as the link's user has it (its rights: export, refresh), cut to what the
    link allows: the grid only, and on the form only the parameters the reader may change - each showing
    the link's own value when it sets one. The others run with the link's values (``params_for``)."""
    definition = views.definition(get_database(), get_settings().app, who, found["code"])
    editable = {e["name"] for e in found["editable"]}
    shown = [p.model_copy(update={"default": str(params[p.name])} if p.name in params and p.default != params[p.name]
                          and not isinstance(p.default, list) else {})
             for p in definition.params if p.name in editable]
    grid = [v for v in definition.views if v.key == "grid"]
    return definition.model_copy(update={
        "params": shown, "views": grid, "default_view": "grid",
        "auto_run": all(not p.required or p.default not in (None, "", []) for p in shown)})


def _report_page(request: Request, token: str, found: dict[str, Any], who: str, actor: str,
                 params: dict[str, Any]):
    """A table link shows the portal's own report page - the form, the buttons, the grid with its
    filters, sorting and pages - without the menu. Its calls go to /p/<token>/api."""
    from drscore.web.i18n import request_locale
    from drscore.web.templating import client_messages

    settings = get_settings().app
    locale = settings.public.locale or request_locale(request)
    definition = _link_definition(found, who, params)
    page_data = {"definition": definition.model_dump(mode="json"),
                 "messages": client_messages(locale, ("report.", "grid.", "error.")), "locale": locale,
                 "api_base": f"{publiclink.PATH}{token}/api", "export_base": f"{publiclink.PATH}{token}/export"}
    log_access(actor, "OPEN", "OK", report_code=found["code"], client_ip=client_ip(request), view_key="grid")
    return _headers(render(request, "public_report.html", with_menu=False, locale=locale,
                           title=found["title"] or found["name"], definition=definition, page_data=page_data,
                           api_base=page_data["api_base"], bi=None, refresh=found["refresh"]),
                    framers=found["frame"])


class PublicRun(BaseModel):
    params: dict[str, Any] = {}
    refresh: bool = False
    view: str | None = None


@router.post("/p/{token}/api/run")
def public_api_run(token: str, request: Request, body: PublicRun):
    """The report page's run, for a table link: the reader's values only for the parameters the link lets
    them change, as the link's user (its row filters; refresh only with its grant)."""
    settings = get_settings().app
    found = _link_of(token)
    if not _usable(found) or found["view"] != "grid":
        return JSONResponse({"error": {"code": "NOT_FOUND"}}, status_code=404, headers=PRIVATE)
    origin = origin_of(request.headers["origin"]) if request.headers.get("origin") else ""
    own = set(siteurl.portal_origins(settings)) | {origin_of(f"{request.url.scheme}://{request.url.netloc}")}
    if origin and origin not in own:
        return JSONResponse({"error": {"code": "FORBIDDEN", "message": "another site"}}, status_code=403,
                            headers=PRIVATE)
    try:
        who, actor = _reader(request, found)
        params = publiclink.params_for(found["link"], {k: v for k, v in body.params.items()})
        answer = views.run(get_database(), settings, who, found["code"], params, body.refresh, client_ip(request),
                           view="grid")
    except DRSError as exc:
        log_access(actor if "actor" in locals() else "public", "VIEW", "ERROR", report_code=found["code"],
                   client_ip=client_ip(request), view_key="grid", error_code=exc.code)
        return JSONResponse({"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}},
                            status_code=exc.http_status, headers=PRIVATE)
    log_access(actor, "VIEW", "OK", report_code=found["code"], client_ip=client_ip(request), view_key="grid",
               params_json=params, row_count=answer.row_count)
    with get_database().session() as s:
        publiclink.touch(s, found["id"])
    return JSONResponse(answer.model_dump(mode="json", exclude_none=True), headers=PRIVATE)


@router.get("/p/{token}/api/params/{name}/options", response_class=HTMLResponse)
def public_api_options(token: str, name: str, request: Request):
    """A select that depends on another parameter, as on the report page - for a parameter the link lets
    the reader change."""
    from drscore.web.i18n import request_locale, translate
    from drscore.web.templating import templates

    found = _link_of(token)
    if not _usable(found) or name not in {e["name"] for e in found["editable"]}:
        return HTMLResponse("", status_code=404, headers=PRIVATE)
    who, _ = _reader(request, found)
    query = request.query_params
    submitted = {key: query.getlist(key) if len(query.getlist(key)) > 1 else query[key] for key in query.keys()}
    param, chosen = views.param_options(get_database(), get_settings().app, who, found["code"], name,
                                        publiclink.params_for(found["link"], submitted))
    macro = templates.get_template("_param_options.html").module.option_list
    return HTMLResponse(str(macro(param, chosen, translate(request_locale(request), "report.any"))), headers=PRIVATE)


@router.get("/p/{token}/export")
def public_export(token: str, request: Request, format: str = "xlsx", cols: str | None = None):
    """The link's grid as a file - XLSX, CSV or TXT - with every row of the run the page shows (the same
    parameters, read from the cache when it is still valid), when the user the link runs as may export;
    ``cols``: the columns the reader shows, in their order (views.export_columns)."""
    settings = get_settings().app
    found = _link_of(token)
    if not _usable(found) or found["view"] != "grid":
        return _not_found(request)
    who, actor = _reader(request, found)
    database = get_database()
    query = {k: v for k, v in request.query_params.items() if k not in ("format", "cols")}
    with database.session() as s:
        publiclink.touch(s, found["id"])
        params = publiclink.params_for(found["link"], query)
    code, ip = found["code"], client_ip(request)
    try:
        answer = views.run(database, settings, who, code, params, False, ip, view="grid")
        if answer.snapshot is None:
            raise DRSError("SNAPSHOT_GONE")
        file = views.export(database, settings, who, code, format.lower(), answer.snapshot.id, ip,
                            cols=(cols or "")[:4000] or None)
    except DRSError as exc:
        log.info("public link %s... export of %s: %s %s", found["hint"], code, exc.code, exc.admin_detail or "")
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.http_status,
                            headers=PRIVATE)
    headers = PRIVATE | {"Content-Disposition": f'attachment; filename="{file.filename}"'}
    if file.path is not None:
        return FileResponse(file.path, media_type=file.media_type, headers=headers,
                            background=BackgroundTask(os.unlink, file.path))
    return StreamingResponse(file.chunks, media_type=file.media_type, headers=headers)


@router.get("/p/{token}/bi-token")
def public_bi_token(token: str, request: Request):
    """A guest token for the link's dashboard, for the snapshot of the link's own run."""
    settings = get_settings().app
    found = _link_of(token)
    if not _usable(found) or found["view"] != "bi":
        return JSONResponse({"error": {"code": "REPORT_NOT_FOUND"}}, status_code=404, headers=PRIVATE)
    database = get_database()
    with database.session() as s:
        params = publiclink.params_for(found["link"], dict(request.query_params))
    code, ip = found["code"], client_ip(request)
    try:
        who, _ = _reader(request, found)
        answer = views.run(database, settings, who, code, params, False, ip, view="bi")
        members = None
        if (answer.bi or {}).get("members"):
            members = views.bi_members_run(database, settings, who, code, params, False, ip)
        snapshot = answer.snapshot.id if answer.snapshot is not None else None
        token_value = views.bi_token(database, settings, who, code, ip, snapshot_id=snapshot, members=members)
    except DRSError as exc:
        log.info("public link %s... token for %s: %s %s", found["hint"], code, exc.code, exc.admin_detail or "")
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.http_status,
                            headers=PRIVATE)
    return JSONResponse({"token": token_value}, headers=PRIVATE)


@router.get("/p/{token}/html")
def public_html(token: str, request: Request, cached: int = 0):
    """The link's HTML design. ``cached=1``: the newest snapshot the link has, even out of its
    retention, without waiting for the source (the first paint of the page); otherwise the report's
    current data, run if the cache has none. A failure answers a page marked ``data-drs-error``, so
    the page that asked for a fresh copy keeps the one it shows."""
    settings = get_settings().app
    found = _link_of(token)
    if not _usable(found) or found["view"] != "html":
        return _not_found(request)
    database = get_database()
    with database.session() as s:
        params = publiclink.params_for(found["link"], dict(request.query_params))
    code, ip = found["code"], client_ip(request)
    nonce = secrets.token_urlsafe(16)
    try:
        who, _ = _reader(request, found)
        newest = views.newest_snapshot(database, settings, who, code, "html", params) if cached else None
        if newest is not None:
            snapshot_id = newest[0]
        else:
            answer = views.run(database, settings, who, code, params, False, ip, view="html")
            snapshot_id = answer.snapshot.id
        body = views.html_page(database, settings, who, code, snapshot_id, nonce, ip)
    except DRSError as exc:
        log.info("public link %s... page of %s: %s %s", found["hint"], code, exc.code, exc.admin_detail or "")
        page = {"name": found["title"] or found["name"], "refresh": None, "editable": [], "bi": None, "grid": None,
                "error": {"code": exc.code, "message": exc.message,
                          "detail": exc.detail if exc.code == "PARAM_INVALID" else None}}
        return _headers(render(request, "public.html", status_code=exc.http_status, with_menu=False,
                               locale=settings.public.locale or None, theme=settings.public.theme,
                               title=page["name"], missing=False, page=page), framers=found["frame"])
    return HTMLResponse(body, headers=_html_headers(nonce, found["frame"]))


# --------------------------------------------------------------------------------------------
# The choice list a link may tick


class ListTicks(BaseModel):
    checked: dict[str, bool]


def _list_not_found():
    return JSONResponse({"error": {"code": "LIST_NOT_FOUND"}}, status_code=404, headers=PRIVATE)


def _list_state(code: str) -> dict[str, Any] | None:
    with get_database().session() as s:
        try:
            return choicelist.state(choicelist.find(s, code))
        except choicelist.ListError:
            return None


@router.get("/p/{token}/list")
def public_list(token: str, request: Request):
    """The list the link may tick, and its ticks now (not from a snapshot: the page shows what is saved)."""
    found = _link_of(token)
    if not _usable(found) or not found["list"]:
        return _list_not_found()
    try:
        _reader(request, found)
    except DRSError as exc:
        return JSONResponse({"error": {"code": exc.code}}, status_code=exc.http_status, headers=PRIVATE)
    body = _list_state(found["list"])
    return JSONResponse(body, headers=PRIVATE) if body is not None else _list_not_found()


@router.post("/p/{token}/list")
def public_list_save(token: str, ticks: ListTicks, request: Request):
    """Saves the ticks a reader of the link changed. A JSON body only (a form of another site cannot
    send one without asking first), and an Origin, when the browser sends one, of this server."""
    settings = get_settings().app
    found = _link_of(token)
    if not _usable(found) or not found["list"]:
        return _list_not_found()
    try:
        _, actor = _reader(request, found)
    except DRSError as exc:
        return JSONResponse({"error": {"code": exc.code}}, status_code=exc.http_status, headers=PRIVATE)
    if not (request.headers.get("content-type") or "").lower().startswith("application/json"):
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "JSON only"}}, status_code=415,
                            headers=PRIVATE)
    origin = origin_of(request.headers["origin"]) if request.headers.get("origin") else ""
    own = set(siteurl.portal_origins(settings.app)) | {origin_of(f"{request.url.scheme}://{request.url.netloc}")}
    if origin and origin not in own:
        return JSONResponse({"error": {"code": "FORBIDDEN", "message": "another site"}}, status_code=403,
                            headers=PRIVATE)
    database, ip, code = get_database(), client_ip(request), found["list"]
    try:
        with database.session() as s:
            changed = choicelist.tick(s, actor, code, ticks.checked, where=ip)
    except choicelist.ListError as exc:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": str(exc)}}, status_code=400,
                            headers=PRIVATE)
    try:
        choicelist.publish(database, settings, code)
    except Exception:
        log.exception("public link %s...: list %s saved, its table not written", found["hint"], code)
        return JSONResponse({"error": {"code": "LIST_NOT_PUBLISHED", "message": (
            "The ticks are saved, but the reports cannot see them yet: tell the DRS administrator.")}},
            status_code=500, headers=PRIVATE)
    log.info("public link %s... ticked list %s from %s: %s", found["hint"], code, ip, ", ".join(changed) or "nothing")
    return JSONResponse((_list_state(code) or {}) | {"changed": changed}, headers=PRIVATE)
