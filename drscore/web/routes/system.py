"""Hệ thống: the administrators' pages of the portal, as petweb's - Nhật ký (the logs, filtered, exported to
Excel), the AI agent's overview and questions, the Telegram / Zalo notices. Administrators only."""

from __future__ import annotations

import io
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import String, cast, func, or_, select
from starlette.concurrency import run_in_threadpool

from drscore import activity, notify
from drscore.db import get_database
from drscore.db.models import AccessLog, ActivityLog, AiUsage, AuditLog, NoticeLog, User
from drscore.errors import DRSError
from drscore.settings import get_settings
from drscore.typhon import agent
from drscore.web.deps import current_user
from drscore.web.i18n import request_locale, translate
from drscore.web.templating import render

router = APIRouter()
LOG_LIMIT = 500
LOG_KINDS = ("activity", "access", "audit")


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise DRSError("ADMIN_ONLY")
    return user


def _day(value: str | None, default: date) -> date:
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return default


def _range(settings, start: date, end: date) -> tuple[datetime, datetime]:
    tz = settings.tz
    return (datetime.combine(start, time.min, tz).astimezone(timezone.utc),
            datetime.combine(end + timedelta(days=1), time.min, tz).astimezone(timezone.utc))


def _compact(value: Any, limit: int = 400) -> str:
    """The fields sent, as "name=value" pairs, for one cell."""
    if value is None:
        return ""
    if isinstance(value, dict):
        text = ", ".join(f"{k}={_compact(v, 120)}" for k, v in value.items())
    elif isinstance(value, list):
        text = "[" + ", ".join(_compact(v, 60) for v in value[:20]) + ("…" if len(value) > 20 else "") + "]"
    else:
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _log_rows(kind: str, filters: dict, locale: str) -> list[dict]:
    settings = get_settings().app
    since, until = _range(settings, filters["from"], filters["to"])
    q, user, action, refused = filters["q"], filters["user"], filters["action"], filters["refused"]
    with get_database().session() as s:
        if kind == "activity":
            stmt = select(ActivityLog).where(ActivityLog.logged_at >= since, ActivityLog.logged_at < until)
            if user:
                stmt = stmt.where(ActivityLog.username == user)
            if action:
                stmt = stmt.where(ActivityLog.action == action)
            if refused:
                stmt = stmt.where(ActivityLog.outcome != "OK")
            if q:
                like = f"%{q}%"
                stmt = stmt.where(or_(ActivityLog.path.ilike(like), ActivityLog.object_key.ilike(like),
                                      ActivityLog.client_ip.ilike(like), ActivityLog.username.ilike(like),
                                      cast(ActivityLog.detail_json, String).ilike(like)))
            rows = s.scalars(stmt.order_by(ActivityLog.activity_id.desc()).limit(LOG_LIMIT)).all()
            return [{"at": r.logged_at, "user": r.username, "what": activity.label(r.action, locale),
                     "object": r.object_key or "", "detail": _compact(r.detail_json) or (r.path + (f"?{r.query_text}" if r.query_text else "")),
                     "path": r.path, "ok": r.outcome, "status": r.status_code, "ms": r.duration_ms,
                     "ip": r.client_ip or "", "agent": _agent(r.user_agent)} for r in rows]
        if kind == "access":
            stmt = select(AccessLog).where(AccessLog.logged_at >= since, AccessLog.logged_at < until)
            if user:
                stmt = stmt.where(AccessLog.username == user)
            if refused:
                stmt = stmt.where(AccessLog.status != "OK")
            if q:
                like = f"%{q}%"
                stmt = stmt.where(or_(AccessLog.report_code.ilike(like), AccessLog.username.ilike(like),
                                      AccessLog.client_ip.ilike(like), AccessLog.error_code.ilike(like)))
            rows = s.scalars(stmt.order_by(AccessLog.log_id.desc()).limit(LOG_LIMIT)).all()
            return [{"at": r.logged_at, "user": r.username, "what": r.action, "object": r.report_code or "",
                     "detail": " · ".join(x for x in [_compact(r.params_json, 200), f"{r.row_count} rows" if r.row_count is not None else "",
                                                      r.view_key or "", r.error_code or ""] if x),
                     "path": "", "ok": r.status, "status": None, "ms": r.duration_ms, "ip": r.client_ip or "",
                     "agent": _agent(r.user_agent)} for r in rows]
        stmt = select(AuditLog).where(AuditLog.logged_at >= since, AuditLog.logged_at < until)
        if user:
            stmt = stmt.where(AuditLog.actor == user)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(AuditLog.object_key.ilike(like), AuditLog.object_type.ilike(like),
                                  AuditLog.actor.ilike(like)))
        rows = s.scalars(stmt.order_by(AuditLog.audit_id.desc()).limit(LOG_LIMIT)).all()
        return [{"at": r.logged_at, "user": r.actor, "what": r.action, "object": f"{r.object_type} {r.object_key}",
                 "detail": _diff(r.before_json, r.after_json), "path": "", "ok": "OK", "status": None, "ms": None,
                 "ip": "", "agent": ""} for r in rows]


def _diff(before: Any, after: Any) -> str:
    if isinstance(before, dict) and isinstance(after, dict):
        changed = [f"{k}: {_compact(before.get(k), 60)} → {_compact(after.get(k), 60)}"
                   for k in after if before.get(k) != after.get(k) and k not in ("updated_at",)]
        return _compact("; ".join(changed), 400)
    return _compact(after if after is not None else before)


def _agent(ua: str | None) -> str:
    if not ua:
        return ""
    browser = next((b for b in ("Edg", "OPR", "Chrome", "Firefox", "Safari") if b in ua), "")
    browser = {"Edg": "Edge", "OPR": "Opera"}.get(browser, browser)
    system = next((o for o in ("Windows", "Android", "iPhone", "iPad", "Mac OS", "Linux") if o in ua), "")
    return " · ".join(x for x in (browser, system.replace("Mac OS", "macOS")) if x) or ua[:40]


def _filters(request: Request) -> dict:
    settings = get_settings().app
    today = datetime.now(settings.tz).date()
    qp = request.query_params
    kind = qp.get("kind") if qp.get("kind") in LOG_KINDS else "activity"
    return {"kind": kind, "from": _day(qp.get("from"), today - timedelta(days=6)), "to": _day(qp.get("to"), today),
            "user": (qp.get("user") or "").strip()[:100], "action": (qp.get("action") or "").strip()[:60],
            "q": (qp.get("q") or "").strip()[:100], "refused": qp.get("refused") == "1"}


@router.get("/system/log")
def system_log(request: Request, user: User = Depends(admin_user)):
    locale = request_locale(request)
    f = _filters(request)
    rows = _log_rows(f["kind"], f, locale)
    with get_database().session() as s:
        users = list(s.scalars(select(User.username).order_by(User.username)))
    actions = sorted(((k, activity.label(k, locale)) for k in activity.ACTIONS), key=lambda x: x[1])
    return render(request, "system_log.html", rows=rows, f=f, users=users, actions=actions, limit=LOG_LIMIT,
                  tz=get_settings().app.tz, query=request.url.query)


@router.get("/system/log.xlsx")
def system_log_export(request: Request, user: User = Depends(admin_user)):
    import xlsxwriter

    locale = request_locale(request)
    f = _filters(request)
    rows = _log_rows(f["kind"], f, locale)
    tz = get_settings().app.tz
    buffer = io.BytesIO()
    book = xlsxwriter.Workbook(buffer, {"in_memory": True, "strings_to_formulas": False})
    sheet = book.add_worksheet(f["kind"])
    bold = book.add_format({"bold": True, "bg_color": "#c6e0b4"})
    heads = [translate(locale, k) for k in ("system.col_time", "system.col_user", "system.col_what", "system.col_object",
                                            "system.col_detail", "system.col_result", "system.col_ip", "system.col_device")]
    for c, h in enumerate(heads):
        sheet.write(0, c, h, bold)
    for i, r in enumerate(rows, start=1):
        values = [r["at"].astimezone(tz).strftime("%Y-%m-%d %H:%M:%S"), r["user"], r["what"], r["object"], r["detail"],
                  r["ok"] + (f" ({r['status']})" if r["status"] else ""), r["ip"], r["agent"]]
        for c, v in enumerate(values):
            sheet.write_string(i, c, str(v))
    sheet.set_column(0, 0, 19)
    sheet.set_column(1, 3, 18)
    sheet.set_column(4, 4, 70)
    sheet.set_column(5, 7, 16)
    sheet.freeze_panes(1, 0)
    book.close()
    name = f"drs_{f['kind']}_{f['from']}_{f['to']}.xlsx"
    return StreamingResponse(iter([buffer.getvalue()]), headers={"Content-Disposition": f'attachment; filename="{name}"'},
                             media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --------------------------------------------------------------------------------------------
# The AI agent

def _ai_overview() -> dict:
    settings = get_settings().app
    database = get_database()
    month = agent._month_start(settings)
    day = agent._day_start(settings)
    since_30 = day - timedelta(days=29)
    tokens = AiUsage.input_tokens + AiUsage.output_tokens + AiUsage.cache_read_tokens + AiUsage.cache_write_tokens
    with database.session() as s:
        def totals(since):
            r = s.execute(select(func.count(AiUsage.usage_id), func.coalesce(func.sum(tokens), 0),
                                 func.coalesce(func.sum(AiUsage.cost_usd), 0),
                                 func.coalesce(func.sum(AiUsage.input_tokens), 0), func.coalesce(func.sum(AiUsage.output_tokens), 0),
                                 func.coalesce(func.sum(AiUsage.cache_read_tokens), 0), func.coalesce(func.sum(AiUsage.cache_write_tokens), 0))
                          .where(AiUsage.asked_at >= since)).one()
            refused = s.scalar(select(func.count(AiUsage.usage_id)).where(AiUsage.asked_at >= since, AiUsage.status == "refused"))
            errors = s.scalar(select(func.count(AiUsage.usage_id)).where(AiUsage.asked_at >= since, AiUsage.status == "error"))
            return {"questions": r[0], "tokens": int(r[1]), "cost": float(r[2]), "input": int(r[3]), "output": int(r[4]),
                    "cache_read": int(r[5]), "cache_write": int(r[6]), "refused": refused or 0, "errors": errors or 0}

        by_user = s.execute(select(AiUsage.username, func.count(AiUsage.usage_id), func.count(AiUsage.usage_id),
                                   func.coalesce(func.sum(tokens), 0), func.coalesce(func.sum(AiUsage.cost_usd), 0),
                                   func.max(AiUsage.asked_at))
                            .where(AiUsage.asked_at >= month).group_by(AiUsage.username)
                            .order_by(func.sum(AiUsage.cost_usd).desc())).all()
        names = dict(s.execute(select(User.username, User.display_name)).all())
        by_model = s.execute(select(AiUsage.model, func.count(AiUsage.usage_id), func.coalesce(func.sum(tokens), 0),
                                    func.coalesce(func.sum(AiUsage.cost_usd), 0))
                             .where(AiUsage.asked_at >= month, AiUsage.model.is_not(None)).group_by(AiUsage.model)).all()
        recent = s.scalars(select(AiUsage).where(AiUsage.asked_at >= since_30)).all()
        latest = s.scalars(select(AiUsage).order_by(AiUsage.usage_id.desc()).limit(100)).all()
        all_time = s.execute(select(func.count(AiUsage.usage_id), func.coalesce(func.sum(tokens), 0),
                                    func.coalesce(func.sum(AiUsage.cost_usd), 0))).one()
    days: dict[str, dict] = {}
    for u in recent:
        key = u.asked_at.astimezone(settings.tz).strftime("%Y-%m-%d")
        d = days.setdefault(key, {"day": key, "questions": 0, "refused": 0, "tokens": 0, "cost": 0.0})
        d["questions"] += 1
        d["refused"] += 1 if u.status == "refused" else 0
        d["tokens"] += u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_write_tokens
        d["cost"] += float(u.cost_usd)
    state = agent.availability(settings)
    return {
        "state": state, "settings": settings.ai, "month": totals(month), "today": totals(day),
        "all_time": {"questions": all_time[0], "tokens": int(all_time[1]), "cost": float(all_time[2])},
        "by_user": [{"username": r[0], "name": names.get(r[0]) or "", "questions": r[1],
                     "tokens": int(r[3]), "cost": float(r[4]), "last": r[5]} for r in by_user],
        "by_day": sorted(days.values(), key=lambda d: d["day"], reverse=True),
        "by_model": [{"model": r[0], "questions": r[1], "tokens": int(r[2]), "cost": float(r[3])} for r in by_model],
        "latest": [{"id": u.usage_id, "at": u.asked_at, "user": u.username, "purpose": u.purpose, "status": u.status,
                    "question": u.question, "answer": u.answer or "", "error": u.error_message or "",
                    "tools": ", ".join(t.get("name", "?") for t in (u.tools_json or [])), "model": u.model or "",
                    "tokens": u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_write_tokens,
                    "cost": float(u.cost_usd), "ms": u.duration_ms} for u in latest],
    }


@router.get("/system/ai")
def system_ai(request: Request, tab: str = "overview", user: User = Depends(admin_user)):
    data = _ai_overview()
    return render(request, "system_ai.html", d=data, tab=tab if tab in ("overview", "history", "settings") else "overview",
                  tz=get_settings().app.tz, ai_name=get_settings().app.ai.name)


# --------------------------------------------------------------------------------------------
# Notices

@router.get("/system/notices")
def system_notices(request: Request, msg: str | None = None, user: User = Depends(admin_user)):
    settings = get_settings().app
    with get_database().session() as s:
        rows = s.scalars(select(NoticeLog).order_by(NoticeLog.notice_id.desc()).limit(200)).all()
        notices = [{"at": n.sent_at, "channel": n.channel, "event": n.event, "ok": n.is_ok, "text": n.text_sent,
                    "error": n.error_message or ""} for n in rows]
    return render(request, "system_notices.html", channels=notify.channels(settings, only_ready=False),
                  events=settings.notify.events, notices=notices, msg=msg, tz=settings.tz)


@router.post("/system/notices/test")
async def system_notices_test(request: Request, user: User = Depends(admin_user)):
    settings = get_settings().app
    form = await request.form()
    name = str(form.get("channel") or "")
    ready = [c for c in notify.channels(settings) if c.name == name]
    if not ready:
        return RedirectResponse("/system/notices?msg=not_ready", status_code=303)
    text = translate(request_locale(request), "system.test_text", app=settings.app.name, user=user.username)
    ok, _ = await run_in_threadpool(notify.send_now, settings, ready[0], "test", text)
    return RedirectResponse(f"/system/notices?msg={'sent' if ok else 'failed'}", status_code=303)


# --------------------------------------------------------------------------------------------
# Public links (1.7.2, owner 2026-11-09: "drscore is mainly worked through its pages") - everything
# `python -m drscore publiclink ...` does, on a page: list, make (the address shown once), switch off / on,
# public or sign-in. The same functions as the CLI (drscore.publiclink), so the same checks and audit.

def admin_errors() -> type[Exception]:
    from drscore.admin.service import AdminError

    return AdminError


def _link_form_data(s, report_code: str | None) -> dict:
    from drscore.db.models import ChoiceList, Report, ReportParam

    reports = [{"code": r.report_code, "name": r.report_name}
               for r in s.scalars(select(Report).where(Report.is_active.is_(True)).order_by(Report.report_code))]
    users = [u.username for u in s.scalars(select(User).where(User.is_active.is_(True), User.is_admin.is_(False))
                                           .order_by(User.username))]
    lists = [c.list_code for c in s.scalars(select(ChoiceList).where(ChoiceList.is_active.is_(True))
                                            .order_by(ChoiceList.list_code))]
    params: list[dict] = []
    report = next((r for r in reports if r["code"] == report_code), None)
    if report:
        rep = s.scalars(select(Report).where(Report.report_code == report_code)).one()
        params = [{"name": p.param_name, "label": p.label, "type": p.data_type, "default": p.default_value or ""}
                  for p in s.scalars(select(ReportParam).where(ReportParam.report_id == rep.report_id,
                                                               ReportParam.is_active.is_(True))
                                     .order_by(ReportParam.sort_order))]
    return {"reports": reports, "users": users, "lists": lists, "report": report, "params": params}


def _links_page(request: Request, *, report_code: str | None = None, made: dict | None = None,
                error: str | None = None, msg: str | None = None, form: dict | None = None, status_code: int = 200):
    from drscore import publiclink, siteurl

    settings = get_settings().app
    with get_database().session() as s:
        data = _link_form_data(s, report_code)
        links = [publiclink.describe(link) for link in reversed(publiclink.list_all(s))]
    return render(request, "system_links.html", status_code=status_code, links=links, made=made, error=error,
                  msg=msg, form=form or {}, tz=settings.tz, public_on=settings.public.enabled,
                  default_user=(settings.public.default_user or "").strip(),
                  addresses=siteurl.portal_origins(settings), **data)


@router.get("/system/links")
def system_links(request: Request, report: str | None = None, msg: str | None = None,
                 user: User = Depends(admin_user)):
    return _links_page(request, report_code=report or None, msg=msg)


@router.post("/system/links")
async def system_links_create(request: Request, user: User = Depends(admin_user)):
    from drscore import publiclink, siteurl

    settings = get_settings().app
    form = await request.form()
    code = str(form.get("report") or "")
    fixed = {k[6:]: str(v) for k, v in form.items() if k.startswith("fixed:") and str(v).strip() != ""}
    editable = [k[9:] for k in form.keys() if k.startswith("editable:")]
    title = str(form.get("title") or "").strip() or None
    view = str(form.get("view") or "grid")
    try:
        refresh = int(form["refresh"]) if str(form.get("refresh") or "").strip() else None
        expires = None
        if str(form.get("expires") or "").strip():
            expires = datetime.combine(date.fromisoformat(str(form["expires"])), time(23, 59, 59), settings.tz
                                       ).astimezone(timezone.utc)
        run_as = str(form.get("run_as") or "")
        with get_database().session() as s:
            if run_as and run_as == (settings.public.default_user or "").strip():
                # The default link account gets the report it is linked to, with the buttons chosen.
                from drscore.admin import service as admin_service

                admin_service.grant(s, user.username, "report", code, username=run_as,
                                    can_export=form.get("can_export") == "on", can_refresh=form.get("can_refresh") == "on")
                s.flush()  # the link's check below reads the grant
            link, token = publiclink.create(
                s, user.username, settings, report_code=code, run_as=run_as,
                params=fixed or None, editable=editable or None,
                view=view, title=title, refresh_seconds=refresh, expires_at=expires,
                choice_list=str(form.get("choice_list") or "") or None, is_public=form.get("public") == "yes",
                framers=str(form.get("frame") or "") or None)
            made = {"id": link.link_id, "report": code,
                    "urls": [origin + publiclink.PATH + token for origin in siteurl.portal_origins(settings)]}
    except (publiclink.LinkError, ValueError, admin_errors()) as exc:
        return _links_page(request, report_code=code or None, error=str(exc), form=dict(form), status_code=422)
    return _links_page(request, made=made)


@router.post("/system/links/{link_id}/{action}")
async def system_links_action(link_id: int, action: str, request: Request, user: User = Depends(admin_user)):
    from drscore import publiclink

    settings = get_settings().app
    form = await request.form() if action == "frame" else {}
    try:
        with get_database().session() as s:
            if action == "off":
                publiclink.revoke(s, user.username, str(link_id))
            elif action == "on":
                publiclink.enable(s, user.username, settings, str(link_id))
            elif action in ("public", "signin"):
                publiclink.change(s, user.username, settings, str(link_id), is_public=action == "public")
            elif action == "frame":  # the sites that may show the link in a frame ('' = this portal only)
                publiclink.change(s, user.username, settings, str(link_id), framers=str(form.get("frame") or "") or None)
            else:
                raise DRSError("PARAM_INVALID")
    except publiclink.LinkError as exc:
        return _links_page(request, error=f"#{link_id}: {exc}", status_code=422)
    return RedirectResponse(f"/system/links?msg={action}", status_code=303)
