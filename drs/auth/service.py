"""Local login, passwords and sessions (spec sections 9.1 and 9.3)."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from drs.auth.passwords import hash_password, needs_rehash, verify_password
from drs.db.models import Session as DbSession
from drs.db.models import User
from drs.db.types import utcnow
from drs.errors import DRSError
from drs.settings import AppSettings


class PasswordPolicyError(ValueError):
    """The new password does not meet the policy. The message says why."""


def normalise_username(username: str) -> str:
    return (username or "").strip().lower()


def find_user(session: Session, username: str) -> User | None:
    return session.scalars(
        select(User).where(func.lower(User.username) == normalise_username(username))
    ).one_or_none()


# --------------------------------------------------------------------------------------------
# Passwords

def check_password_policy(password: str, settings: AppSettings) -> None:
    minimum = settings.auth.local.min_password_length
    if len(password or "") < minimum:
        raise PasswordPolicyError(f"The password must have at least {minimum} characters.")


def set_password(session: Session, user: User, password: str, settings: AppSettings) -> None:
    """Sets a new password and ends every session of the user (spec 9.3)."""
    check_password_policy(password, settings)
    user.password_hash = hash_password(password)
    user.must_change_password = False  # a password that meets the policy replaces the known one
    user.failed_login_count = 0
    user.locked_until = None
    end_all_sessions(session, user.user_id)


def set_initial_password(user: User, password: str) -> None:
    """Gives a user a password everybody knows (the default administrator's ``admin``).

    It is not held to the policy: its length protects nothing. What protects is that the session
    of such a user opens only the account page until the password is replaced, and the
    replacement is held to the policy."""
    user.password_hash = hash_password(password)
    user.must_change_password = True


def set_active(session: Session, user: User, active: bool) -> None:
    user.is_active = active
    if not active:
        end_all_sessions(session, user.user_id)


def authenticate(session: Session, username: str, password: str, settings: AppSettings,
                 now: datetime | None = None) -> User:
    """Checks a user name and password.

    A wrong user name and a wrong password give the same error. ``max_failed_logins`` failures in a
    row lock the account for ``lockout_minutes``. The caller commits: the failure counter must be
    stored even when this raises.
    """
    now = now or utcnow()
    policy = settings.auth.local
    user = find_user(session, username)
    if user is None or user.password_hash is None:
        verify_password(None, password)  # same work as a real check
        raise DRSError("INVALID_CREDENTIALS")
    if user.locked_until is not None and user.locked_until > now:
        raise DRSError("ACCOUNT_LOCKED")
    if not verify_password(user.password_hash, password):
        user.failed_login_count += 1
        if user.failed_login_count >= policy.max_failed_logins:
            user.locked_until = now + timedelta(minutes=policy.lockout_minutes)
            user.failed_login_count = 0
            raise DRSError("ACCOUNT_LOCKED")
        raise DRSError("INVALID_CREDENTIALS")
    if not user.is_active:
        raise DRSError("ACCOUNT_DISABLED")
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


# --------------------------------------------------------------------------------------------
# Sessions

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SessionInfo:
    session_hash: str
    user_id: int
    username: str
    display_name: str
    is_admin: bool
    csrf_token: str
    auth_method: str
    must_change_password: bool = False


def create_session(session: Session, user: User, auth_method: str, settings: AppSettings,
                   client_ip: str | None = None, user_agent: str | None = None,
                   now: datetime | None = None) -> tuple[str, str]:
    """Creates a session. Returns (cookie token, CSRF token); only the token's hash is stored."""
    now = now or utcnow()
    token = secrets.token_urlsafe(32)  # 256 bits
    csrf = secrets.token_hex(32)
    session.add(DbSession(
        session_hash=token_hash(token),
        user_id=user.user_id,
        csrf_token=csrf,
        auth_method=auth_method,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session.absolute_hours),
        client_ip=(client_ip or None) and client_ip[:64],
        user_agent=(user_agent or None) and user_agent[:400],
    ))
    return token, csrf


def load_session(session: Session, token: str | None, settings: AppSettings,
                 now: datetime | None = None) -> SessionInfo | None:
    """The live session for a cookie token, or None.

    A session ends ``idle_minutes`` after its last request or ``absolute_hours`` after login,
    whichever comes first; an ended or orphaned session row is deleted.
    """
    if not token:
        return None
    now = now or utcnow()
    row = session.get(DbSession, token_hash(token))
    if row is None:
        return None
    user = session.get(User, row.user_id)
    idle_limit = row.last_seen_at + timedelta(minutes=settings.session.idle_minutes)
    if user is None or not user.is_active or now >= row.expires_at or now >= idle_limit:
        session.delete(row)
        return None
    # Write last_seen_at at most once a minute, not on every request.
    if now - row.last_seen_at >= timedelta(seconds=60):
        row.last_seen_at = now
    return SessionInfo(
        session_hash=row.session_hash,
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        is_admin=user.is_admin,
        csrf_token=row.csrf_token,
        auth_method=row.auth_method,
        must_change_password=user.must_change_password,
    )


def end_session(session: Session, token: str | None) -> None:
    if token:
        session.execute(delete(DbSession).where(DbSession.session_hash == token_hash(token)))


def end_all_sessions(session: Session, user_id: int) -> None:
    session.execute(delete(DbSession).where(DbSession.user_id == user_id))


def purge_sessions(session: Session, settings: AppSettings, now: datetime | None = None) -> int:
    now = now or utcnow()
    idle_cut = now - timedelta(minutes=settings.session.idle_minutes)
    result = session.execute(
        delete(DbSession).where((DbSession.expires_at <= now) | (DbSession.last_seen_at <= idle_cut))
    )
    return result.rowcount or 0
