"""Portal pages (spec section 15)."""

from __future__ import annotations

import secrets
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from drscore.audit import audit, log_access
from drscore.auth import service as auth
from drscore.auth.passwords import verify_password
from drscore.db import get_database
from drscore.db.models import User
from drscore.settings import get_settings
from drscore.web import views
from drscore.errors import DRSError
from drscore.web.deps import SESSION_COOKIE, client_ip, current_session, current_user, db_session, signed_in_user
from drscore.web.i18n import LOCALE_COOKIE, LOCALES, error_message, request_locale, translate
from drscore.web.templating import client_messages, render

router = APIRouter()

SECURITY_HEADERS_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; font-src 'self'; frame-ancestors 'self'; form-action 'self'; base-uri 'self'"
)


def safe_next(target: str | None) -> str:
    """Only a path on this site: never an absolute URL (open redirect)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


@router.get("/login")
def login_page(request: Request, next: str | None = None, changed: int = 0,
               session: Session = Depends(db_session)):
    if current_session(request, session) is not None:
        return RedirectResponse(safe_next(next), status_code=303)
    notice = translate(request_locale(request), "account.changed") if changed else None
    return render(request, "login.html", next=safe_next(next), error=None, notice=notice, username="")


@router.get("/")
def home(request: Request, user: User = Depends(current_user)):
    return render(request, "home.html")


@router.get("/reports/{code}")
def report_page(code: str, request: Request, user: User = Depends(current_user)):
    settings = get_settings().app
    database = get_database()
    try:
        definition = views.definition(database, settings, user.username, code)
    except DRSError as exc:  # every attempt to open a report is logged, refused ones too
        log_access(user.username, "OPEN", "DENIED" if exc.code == "FORBIDDEN" else "ERROR", report_code=code,
                   error_code=exc.code, error_message=exc.admin_detail or exc.message)
        raise
    log_access(user.username, "OPEN", "OK", report_code=code, view_key=definition.default_view)
    locale = request_locale(request)
    # Each tab of the report (grid / html / bi) carries its own state: a tab without a usable
    # design shows the error of section 13, the other tabs work.
    bi = next((v.bi for v in definition.views if v.key == "bi" and v.ok), None)
    page_data = {
        "definition": definition.model_dump(mode="json"),
        "messages": client_messages(locale, ("report.", "grid.", "error.")),
        "locale": locale,
    }
    response = render(request, "report.html", definition=definition, page_data=page_data, bi=bi,
                      is_admin=user.is_admin,
                      active_report=code, active_group=definition.report.group)
    if bi:
        # The dashboard's server (the linked BI tool, or Superset) is the only other origin the page
        # may frame.
        origin = origin_of(bi["url"] if bi["mode"] == "LINK" else bi["superset_url"])
        response.headers["Content-Security-Policy"] = SECURITY_HEADERS_CSP.replace(
            "frame-ancestors 'self'", f"frame-src 'self' {origin}; frame-ancestors 'self'")
    return response


@router.get("/reports/{code}/html")
def report_html(code: str, request: Request, snapshot_id: int, user: User = Depends(current_user)):
    """The rendered HTML design (iframe content). Scripts of the design run only with this
    response's nonce; everything else must come from the portal itself."""
    nonce = secrets.token_urlsafe(16)
    page = views.html_page(get_database(), get_settings().app, user.username, code, snapshot_id, nonce,
                           client_ip(request))
    return HTMLResponse(page, headers={
        "Content-Security-Policy": (
            f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"),
        "Cache-Control": "no-store",
    })


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _account(request: Request, user: User, error: str | None = None, status_code: int = 200):
    # While the password must be changed no report can be opened, so the menu is left out.
    return render(request, "account.html", status_code=status_code, with_menu=not user.must_change_password,
                  user=user, error=error, min_length=get_settings().app.auth.local.min_password_length)


@router.get("/account")
def account_page(request: Request, user: User = Depends(signed_in_user)):
    return _account(request, user)


@router.post("/account/password")
async def change_password(request: Request, user: User = Depends(signed_in_user),
                          session: Session = Depends(db_session)):
    settings = get_settings().app
    locale = request_locale(request)
    form = await request.form()
    current = str(form.get("current_password") or "")
    new = str(form.get("new_password") or "")
    repeat = str(form.get("repeat_password") or "")
    minimum = settings.auth.local.min_password_length
    error = None
    if user.password_hash is None:
        error = translate(locale, "account.sso_only")
    elif not verify_password(user.password_hash, current):
        error = translate(locale, "account.wrong_current")
    elif new != repeat:
        error = translate(locale, "account.mismatch")
    elif new == current:  # typing the old password again is not a change
        error = translate(locale, "account.same_as_current")
    elif len(new) < minimum:
        error = translate(locale, "account.too_short", n=minimum)
    if error:
        return _account(request, user, error, status_code=400)
    db_user = session.get(User, user.user_id)
    auth.set_password(session, db_user, new, settings)  # ends every session of the user
    audit(session, db_user.username, "USER_CHANGE_OWN_PASSWORD", "user", db_user.username)
    response = RedirectResponse("/login?changed=1", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/locale/{locale}")
def set_locale(locale: str, request: Request, next: str | None = None):
    """Switches the UI language. A preference cookie only, so a plain link is enough."""
    response = RedirectResponse(safe_next(next), status_code=303)
    if locale in LOCALES:
        response.set_cookie(LOCALE_COOKIE, locale, max_age=365 * 24 * 3600, httponly=True, samesite="lax",
                            secure=get_settings().app.session.cookie_secure, path="/")
    return response


def login_redirect(request: Request) -> RedirectResponse:
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target, safe='/')}", status_code=303)
