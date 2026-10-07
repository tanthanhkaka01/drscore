"""Request dependencies: database session, current user, CSRF check (spec sections 9.3, 16)."""

from __future__ import annotations

import hmac
from typing import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from drs.auth.service import SessionInfo, load_session
from drs.db import get_database
from drs.db.models import User
from drs.errors import DRSError
from drs.logsetup import request_user
from drs.settings import get_settings

SESSION_COOKIE = "drs_session"
CSRF_HEADER = "X-CSRF-Token"
CSRF_FIELD = "csrf_token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Routes that change state but cannot have a session yet.
CSRF_EXEMPT_PREFIXES = ("/login", "/auth/sso/")


def db_session(request: Request) -> Iterator[Session]:
    """One DRS database session per request; committed when the route succeeds."""
    with get_database().session() as session:
        request.state.db = session
        yield session


def current_session(request: Request, session: Session = Depends(db_session)) -> SessionInfo | None:
    if not hasattr(request.state, "auth"):
        info = load_session(session, request.cookies.get(SESSION_COOKIE), get_settings().app)
        # Release the write lock of a last_seen_at update now, not at the end of the request.
        session.commit()
        request.state.auth = info
        request.state.is_admin = bool(info and info.is_admin)
        if info:
            request_user.set(info.username)
    return request.state.auth


async def csrf_guard(request: Request, session: Session = Depends(db_session)) -> None:
    """Applied to every route. A state-changing request must carry the session's CSRF token in
    the X-CSRF-Token header or the csrf_token form field."""
    if request.method not in UNSAFE_METHODS or request.url.path.startswith(CSRF_EXEMPT_PREFIXES):
        return
    info = current_session(request, session)
    if info is None:
        raise DRSError("AUTH_REQUIRED")
    sent = request.headers.get(CSRF_HEADER)
    if not sent and request.headers.get("content-type", "").startswith(
            ("application/x-www-form-urlencoded", "multipart/form-data")):
        sent = (await request.form()).get(CSRF_FIELD)
    if not sent or not hmac.compare_digest(str(sent), info.csrf_token):
        raise DRSError("CSRF_FAILED")


def signed_in_user(request: Request, session: Session = Depends(db_session)) -> User:
    """The user of the session, whatever the state of their password. Only the account page and
    the password change take this one; every other route takes ``current_user``."""
    info = current_session(request, session)
    if info is None:
        raise DRSError("AUTH_REQUIRED")
    user = session.get(User, info.user_id)
    if user is None or not user.is_active:
        raise DRSError("AUTH_REQUIRED")
    return user


def current_user(user: User = Depends(signed_in_user)) -> User:
    # A password everybody knows opens nothing but the page where it is replaced.
    if user.must_change_password:
        raise DRSError("PASSWORD_CHANGE_REQUIRED")
    return user


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
