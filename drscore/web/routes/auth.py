"""Local login and logout (spec sections 9.1, 9.3, 16)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from drscore.audit import log_access
from drscore.auth import service as auth
from drscore.errors import DRSError
from drscore.settings import get_settings
from drscore.web.deps import SESSION_COOKIE, client_ip, current_session, db_session
from drscore.web.i18n import error_message, request_locale
from drscore.web.routes.pages import safe_next
from drscore.web.schemas import LoginRequest
from drscore.web.templating import render

router = APIRouter()


async def _credentials(request: Request) -> tuple[str, str, bool, str | None]:
    """(username, password, came from an HTML form, page to go to after login)."""
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = LoginRequest.model_validate(await request.json())
        except (ValidationError, ValueError):
            raise DRSError("INVALID_CREDENTIALS")
        return body.username, body.password, False, None
    form = await request.form()
    return (str(form.get("username") or ""), str(form.get("password") or ""), True,
            str(form.get("next") or "") or None)


@router.post("/login")
async def login(request: Request, session: Session = Depends(db_session)) -> Response:
    settings = get_settings().app
    username, password, is_form, next_target = await _credentials(request)
    if not settings.auth.local.enabled:
        raise DRSError("INVALID_CREDENTIALS")
    ip = client_ip(request)
    try:
        user = auth.authenticate(session, username, password, settings)
    except DRSError as exc:
        session.commit()  # keep the failure counter / lock-out
        log_access(auth.normalise_username(username), "LOGIN", "DENIED", error_code=exc.code, client_ip=ip)
        if is_form:
            locale = request_locale(request)
            return render(request, "login.html", status_code=exc.http_status, with_menu=False,
                          next=safe_next(next_target), error=error_message(locale, exc.code), notice=None,
                          username=username)
        raise
    token, csrf = auth.create_session(session, user, "local", settings, ip, request.headers.get("user-agent"))
    session.commit()
    log_access(user.username, "LOGIN", "OK", client_ip=ip)

    response: Response
    if is_form:
        # Straight to the page where the password is replaced: every other page would send there.
        target = "/account" if user.must_change_password else safe_next(next_target)
        response = RedirectResponse(target, status_code=303)
    else:
        response = JSONResponse({"username": user.username, "csrf_token": csrf,
                                 "must_change_password": user.must_change_password})
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", secure=settings.session.cookie_secure,
        max_age=settings.session.absolute_hours * 3600, path="/",
    )
    return response


@router.post("/logout")
def logout(request: Request, session: Session = Depends(db_session)) -> Response:
    info = current_session(request, session)
    auth.end_session(session, request.cookies.get(SESSION_COOKIE))
    session.commit()  # before log_access, which writes on its own connection
    if info:
        log_access(info.username, "LOGOUT", "OK", client_ip=client_ip(request))
    wants_json = "application/json" in request.headers.get("accept", "")
    response: Response = JSONResponse({"ok": True}) if wants_json else RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
