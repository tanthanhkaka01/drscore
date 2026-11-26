"""FastAPI application factory."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from drscore.db import get_database
from drscore.errors import DRSError
from drscore.logsetup import request_client, request_id, request_user, setup_logging
from drscore.settings import get_settings
from drscore.siteurl import SiteSchemeMiddleware, current_origin
from drscore.web.deps import csrf_guard
from drscore.web.i18n import error_message, request_locale
from drscore.web.routes import api, auth, changes, design, feedback, pages, public, system, typhon
from drscore.web.templating import render

log = logging.getLogger("drscore.web")

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
    # Everything is served by DRS itself (vendored libraries, no CDN).
    "Content-Security-Policy": pages.SECURITY_HEADERS_CSP,
}


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def frames_self_only(csp: str | None) -> bool:
    """True unless the page's own CSP lets other sites frame it (``frame-ancestors`` beyond 'self').
    X-Frame-Options knows no list of sites, and a browser that reads it would refuse them all."""
    for directive in (csp or "").split(";"):
        name, _, value = directive.strip().partition(" ")
        if name.lower() == "frame-ancestors":
            return value.strip() in ("'self'", "'none'")
    return True


def same_origin(request: Request) -> bool:
    """CSRF defence of the admin pages (sqladmin forms carry no token): a state-changing request
    must come from a page of this site, as told by Origin, or Referer when Origin is missing."""
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return False
    host = request.headers.get("host", "")
    rest = source.split("://", 1)[-1]
    return rest.split("/", 1)[0] == host


def admin_secret(settings) -> str:
    """Signs sqladmin's small session cookie (flash messages only). Derived from DRS_SECRET_KEY so
    every worker agrees; a random one when the key is not set (flash messages then work per worker)."""
    key = os.environ.get(settings.app.app.secret_key_env)
    if not key:
        return secrets.token_urlsafe(32)
    return hashlib.sha256(b"drs-admin-flash:" + key.encode()).hexdigest()


def is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/") or request.url.path == "/healthz"


def error_response(request: Request, exc: DRSError):
    is_admin = bool(getattr(request.state, "is_admin", False))
    locale = request_locale(request)
    wants_json = (is_api(request) or request.headers.get("content-type", "").startswith("application/json")
                  or "application/json" in request.headers.get("accept", ""))
    if wants_json:
        return JSONResponse(exc.to_json(is_admin=is_admin, message=error_message(locale, exc.code)),
                            status_code=exc.http_status)
    if exc.code == "AUTH_REQUIRED" and request.method == "GET":
        return pages.login_redirect(request)
    if exc.code == "PASSWORD_CHANGE_REQUIRED" and request.method == "GET":
        return RedirectResponse("/account", status_code=303)
    return render(request, "error.html", status_code=exc.http_status, code=exc.code,
                  message=error_message(locale, exc.code),
                  detail=exc.detail if exc.code == "PARAM_INVALID" else None,
                  admin_detail=exc.admin_detail if is_admin else None)


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging()
    database = get_database()
    log.info("Starting %s on %s", settings.app.app.name, settings.database.describe())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        scheduler = None
        if settings.app.mirror.enabled:  # copies of source tables, refreshed in the background
            from drscore.mirror.scheduler import MirrorScheduler

            scheduler = MirrorScheduler(database, settings.app)
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.stop()

    app = FastAPI(title=settings.app.app.name, docs_url=None, redoc_url=None, openapi_url=None,
                  dependencies=[Depends(csrf_guard)], lifespan=lifespan)
    app.state.database = database

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id.set(uuid.uuid4().hex[:12])
        request_user.set("-")
        request_client.set((request.client.host if request.client else None, request.headers.get("user-agent")))
        # The address the browser used (the scheme is https behind a trusted proxy that says so).
        current_origin.set(f"{request.url.scheme}://{request.url.netloc}")
        started = time.perf_counter()
        path = request.url.path
        if (path.startswith("/admin/") and (request.method in UNSAFE_METHODS or "/action/" in path)
                and not same_origin(request)):
            log.warning("Refused cross-site %s %s", request.method, request.url.path)
            response = PlainTextResponse("403 - cross-site request refused", status_code=403)
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            if name == "X-Frame-Options" and not frames_self_only(response.headers.get("content-security-policy")):
                continue  # a page that lets other sites frame it (a public link): its CSP says which
            response.headers.setdefault(name, value)
        # The session is loaded in a worker thread, so the user comes from request.state.
        auth_info = getattr(request.state, "auth", None)
        log.info("%s %s -> %s in %d ms", request.method, request.url.path, response.status_code,
                 (time.perf_counter() - started) * 1000,
                 extra={"user": auth_info.username if auth_info else "-"})
        return response

    @app.exception_handler(DRSError)
    async def drs_error(request: Request, exc: DRSError):
        if exc.http_status >= 500:
            log.warning("%s: %s", exc.code, exc.admin_detail or exc.detail or exc.message)
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        detail = {".".join(str(p) for p in e["loc"][1:]) or "body": e["msg"] for e in exc.errors()}
        return error_response(request, DRSError("PARAM_INVALID", detail=detail))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(auth.router)
    app.include_router(api.router)
    app.include_router(design.router)
    app.include_router(pages.router)
    app.include_router(public.router)
    app.include_router(feedback.router)
    app.include_router(typhon.router)
    app.include_router(changes.router)
    app.include_router(system.router)

    from drscore.admin.web import mount as mount_admin
    mount_admin(app, database, settings.app, admin_secret(settings))
    # Every request a user makes, with its outcome (drs_activity_log); outside the request context, so a
    # request refused there (a cross-site admin form) is logged too.
    from drscore.activity import ActivityMiddleware

    app.add_middleware(ActivityMiddleware, settings=settings)
    # Outermost: the scheme is set before anything reads the request's address.
    app.add_middleware(SiteSchemeMiddleware, settings=settings)
    return app
