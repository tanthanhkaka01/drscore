"""Administration of users, roles, attributes and grants.

Used by the CLI now and by the admin pages later (milestone 8). Every change is audited. Functions
raise ``AdminError`` with a message for the administrator when the request cannot be done.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from drscore.audit import audit
from drscore.auth import service as auth
from drscore.authz.permissions import accessible_reports
from drscore.db.models import (
    Datasource,
    GrantDatasource,
    GrantGroup,
    GrantReport,
    Report,
    ReportGroup,
    Role,
    User,
    UserAttribute,
    UserRole,
)
from drscore.settings import AppSettings

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,99}$")
ROLE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,49}$")
# User name, and initial password, of the administrator a database without one is given.
DEFAULT_ADMIN = "admin"


class AdminError(Exception):
    pass


def _user_json(u: User) -> dict[str, Any]:
    return {"username": u.username, "display_name": u.display_name, "email": u.email,
            "sso_subject": u.sso_subject, "is_admin": u.is_admin, "is_active": u.is_active,
            "must_change_password": u.must_change_password}


def get_user(session: Session, username: str) -> User:
    user = auth.find_user(session, username)
    if user is None:
        raise AdminError(f"No user {username!r}.")
    return user


def get_role(session: Session, code: str) -> Role:
    role = session.scalars(select(Role).where(Role.role_code == code.upper())).one_or_none()
    if role is None:
        raise AdminError(f"No role {code!r}.")
    return role


# --------------------------------------------------------------------------------------------
# Users

def add_user(session: Session, actor: str, settings: AppSettings, username: str, display_name: str,
             password: str | None = None, email: str | None = None, sso_subject: str | None = None,
             is_admin: bool = False) -> User:
    username = auth.normalise_username(username)
    if not USERNAME_RE.match(username):
        raise AdminError("A user name is 1 to 100 characters: letters, digits, '.', '_', '@', '-'.")
    if auth.find_user(session, username) is not None:
        raise AdminError(f"User {username!r} already exists.")
    if sso_subject and session.scalars(select(User).where(User.sso_subject == sso_subject)).first():
        raise AdminError(f"SSO subject {sso_subject!r} is already used by another user.")
    user = User(username=username, display_name=display_name or username, email=email or None,
                sso_subject=sso_subject or None, is_admin=is_admin)
    session.add(user)
    session.flush()
    if password is not None:
        try:
            auth.set_password(session, user, password, settings)
        except auth.PasswordPolicyError as exc:
            raise AdminError(str(exc)) from exc
    audit(session, actor, "USER_ADD", "user", username, after=_user_json(user))
    return user


def ensure_default_admin(session: Session, actor: str, settings: AppSettings) -> User | None:
    """Gives a DRS database that has no administrator the administrator ``admin`` / ``admin``, who
    must choose another password at the first sign-in (owner decision 2026-10-07).

    Returns the new user, or None when nothing was done: an administrator exists (a disabled one
    counts, so disabling the last administrator never brings the known password back), the name
    ``admin`` is taken by someone else, or local sign-in is switched off."""
    if not settings.auth.local.enabled:
        return None
    if session.scalars(select(User.user_id).where(User.is_admin).limit(1)).first() is not None:
        return None
    if auth.find_user(session, DEFAULT_ADMIN) is not None:
        return None
    user = User(username=DEFAULT_ADMIN, display_name="Administrator", is_admin=True)
    session.add(user)
    session.flush()
    auth.set_initial_password(user, DEFAULT_ADMIN)
    audit(session, actor, "USER_ADD", "user", user.username, after=_user_json(user))
    return user


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.username)))


def set_user_active(session: Session, actor: str, username: str, active: bool) -> User:
    user = get_user(session, username)
    before = _user_json(user)
    auth.set_active(session, user, active)
    audit(session, actor, "USER_ENABLE" if active else "USER_DISABLE", "user", user.username,
          before=before, after=_user_json(user))
    return user


def set_user_password(session: Session, actor: str, settings: AppSettings, username: str, password: str) -> User:
    user = get_user(session, username)
    try:
        auth.set_password(session, user, password, settings)
    except auth.PasswordPolicyError as exc:
        raise AdminError(str(exc)) from exc
    audit(session, actor, "USER_SET_PASSWORD", "user", user.username)  # never the password or hash
    return user


def set_user_admin(session: Session, actor: str, username: str, is_admin: bool) -> User:
    user = get_user(session, username)
    before = _user_json(user)
    user.is_admin = is_admin
    audit(session, actor, "USER_SET_ADMIN", "user", user.username, before=before, after=_user_json(user))
    return user


# --------------------------------------------------------------------------------------------
# Attributes

def attr_set(session: Session, actor: str, username: str, name: str, values: list[str]) -> list[str]:
    """Replaces every value of one attribute of a user."""
    user = get_user(session, username)
    values = sorted({v.strip() for v in values if v.strip()})
    if not values:
        raise AdminError("Give at least one value. Use 'attr remove' to delete the attribute.")
    before = attr_list(session, username).get(name, [])
    session.execute(delete(UserAttribute).where(UserAttribute.user_id == user.user_id,
                                                UserAttribute.attr_name == name))
    for v in values:
        session.add(UserAttribute(user_id=user.user_id, attr_name=name, attr_value=v))
    audit(session, actor, "USER_ATTR_SET", "user_attribute", f"{user.username}.{name}",
          before=before, after=values)
    return values


def attr_remove(session: Session, actor: str, username: str, name: str, value: str | None = None) -> int:
    user = get_user(session, username)
    stmt = delete(UserAttribute).where(UserAttribute.user_id == user.user_id, UserAttribute.attr_name == name)
    if value is not None:
        stmt = stmt.where(UserAttribute.attr_value == value)
    count = session.execute(stmt).rowcount or 0
    if count == 0:
        raise AdminError("Nothing to remove.")
    audit(session, actor, "USER_ATTR_REMOVE", "user_attribute", f"{user.username}.{name}",
          before=value or "*all*")
    return count


def attr_list(session: Session, username: str) -> dict[str, list[str]]:
    user = get_user(session, username)
    values: dict[str, list[str]] = {}
    for a in session.scalars(select(UserAttribute).where(UserAttribute.user_id == user.user_id)
                             .order_by(UserAttribute.attr_name, UserAttribute.attr_value)):
        values.setdefault(a.attr_name, []).append(a.attr_value)
    return values


# --------------------------------------------------------------------------------------------
# Roles

def add_role(session: Session, actor: str, code: str, name: str) -> Role:
    code = code.upper()
    if not ROLE_CODE_RE.match(code):
        raise AdminError("A role code is upper-case letters, digits and '_', starting with a letter.")
    if session.scalars(select(Role).where(Role.role_code == code)).first():
        raise AdminError(f"Role {code!r} already exists.")
    role = Role(role_code=code, role_name=name or code)
    session.add(role)
    audit(session, actor, "ROLE_ADD", "role", code, after={"role_name": role.role_name})
    return role


def list_roles(session: Session) -> list[tuple[Role, list[str]]]:
    result = []
    for role in session.scalars(select(Role).order_by(Role.role_code)):
        members = list(session.scalars(
            select(User.username).join(UserRole, UserRole.user_id == User.user_id)
            .where(UserRole.role_id == role.role_id).order_by(User.username)))
        result.append((role, members))
    return result


def role_member(session: Session, actor: str, code: str, username: str, add: bool) -> None:
    role = get_role(session, code)
    user = get_user(session, username)
    existing = session.get(UserRole, (user.user_id, role.role_id))
    if add:
        if existing is not None:
            raise AdminError(f"{user.username} is already a member of {role.role_code}.")
        session.add(UserRole(user_id=user.user_id, role_id=role.role_id))
    else:
        if existing is None:
            raise AdminError(f"{user.username} is not a member of {role.role_code}.")
        session.delete(existing)
    audit(session, actor, "ROLE_MEMBER_ADD" if add else "ROLE_MEMBER_REMOVE", "role", role.role_code,
          after={"user": user.username})


# --------------------------------------------------------------------------------------------
# Grants

def _target(session: Session, kind: str, code: str):
    if kind == "group":
        obj = session.scalars(select(ReportGroup).where(ReportGroup.group_code == code)).one_or_none()
        if obj is None:
            raise AdminError(f"No report group {code!r}.")
        return GrantGroup, GrantGroup.group_id, obj.group_id
    if kind == "report":
        obj = session.scalars(select(Report).where(Report.report_code == code)).one_or_none()
        if obj is None:
            raise AdminError(f"No report {code!r}.")
        return GrantReport, GrantReport.report_id, obj.report_id
    if kind == "datasource":
        obj = session.scalars(select(Datasource).where(Datasource.datasource_code == code)).one_or_none()
        if obj is None:
            raise AdminError(f"No datasource {code!r}.")
        return GrantDatasource, GrantDatasource.datasource_id, obj.datasource_id
    raise AdminError(f"Unknown grant kind {kind!r}.")


def _principal(session: Session, username: str | None, role_code: str | None) -> tuple[int | None, int | None, str]:
    if bool(username) == bool(role_code):
        raise AdminError("Give exactly one of --user or --role.")
    if username:
        user = get_user(session, username)
        return user.user_id, None, f"user {user.username}"
    role = get_role(session, role_code)
    return None, role.role_id, f"role {role.role_code}"


def grant(session: Session, actor: str, kind: str, code: str, username: str | None = None,
          role_code: str | None = None, can_export: bool | None = None, can_refresh: bool | None = None,
          can_design: bool | None = None, can_publish: bool | None = None):
    """Creates a grant, or updates the flags of the existing one for the same principal."""
    if can_design is not None and kind != "group":
        raise AdminError("The --design / --no-design option applies only to group grants.")
    if can_publish is not None and kind != "group":
        raise AdminError("The --publish / --no-publish option applies only to group grants.")
    if (can_export is not None or can_refresh is not None) and kind == "datasource":
        raise AdminError("The --export and --refresh options do not apply to datasource grants.")
    model, object_col, object_id = _target(session, kind, code)
    user_id, role_id, who = _principal(session, username, role_code)
    existing = session.scalars(select(model).where(
        object_col == object_id,
        model.user_id == user_id if user_id is not None else model.role_id == role_id,
    )).one_or_none()
    before = None
    if kind == "datasource":
        if existing is None:
            existing = model(user_id=user_id, role_id=role_id, granted_by=actor)
            setattr(existing, object_col.key, object_id)
            session.add(existing)
        else:
            before = {"granted_by": existing.granted_by}
            existing.granted_by = actor
        audit(session, actor, "GRANT_DATASOURCE", "grant_datasource", f"{code} -> {who}", before=before,
              after={"granted_by": existing.granted_by})
        return existing

    if existing is None:
        kwargs = {
            "user_id": user_id, "role_id": role_id, "granted_by": actor,
            "can_export": True if can_export is None else can_export,
            "can_refresh": False if can_refresh is None else can_refresh,
        }
        if kind == "group":
            kwargs["can_design"] = False if can_design is None else can_design
            kwargs["can_publish"] = False if can_publish is None else can_publish
        existing = model(**kwargs)
        setattr(existing, object_col.key, object_id)
        session.add(existing)
    else:
        before = {"can_export": existing.can_export, "can_refresh": existing.can_refresh}
        if kind == "group":
            before["can_design"] = existing.can_design
            before["can_publish"] = existing.can_publish
        if can_export is not None:
            existing.can_export = can_export
        if can_refresh is not None:
            existing.can_refresh = can_refresh
        if can_design is not None and kind == "group":
            existing.can_design = can_design
        if can_publish is not None and kind == "group":
            existing.can_publish = can_publish
        existing.granted_by = actor
    after = {"can_export": existing.can_export, "can_refresh": existing.can_refresh}
    if kind == "group":
        after["can_design"] = existing.can_design
        after["can_publish"] = existing.can_publish
    audit(session, actor, f"GRANT_{kind.upper()}", f"grant_{kind}", f"{code} -> {who}", before=before,
          after=after)
    return existing


def revoke(session: Session, actor: str, kind: str, code: str, username: str | None = None,
           role_code: str | None = None) -> None:
    model, object_col, object_id = _target(session, kind, code)
    user_id, role_id, who = _principal(session, username, role_code)
    count = session.execute(delete(model).where(
        object_col == object_id,
        model.user_id == user_id if user_id is not None else model.role_id == role_id,
    )).rowcount or 0
    if count == 0:
        raise AdminError(f"There is no grant on {kind} {code} for {who}.")
    audit(session, actor, f"REVOKE_{kind.upper()}", f"grant_{kind}", f"{code} -> {who}")


def grant_show(session: Session, username: str) -> list[dict[str, Any]]:
    """The reports this user can view, and through which grant."""
    user = get_user(session, username)
    return [
        {"group": r.group.group_code, "report": r.report_code, "type": r.report_type,
         "restricted": r.is_restricted, "can_export": a.can_export, "can_refresh": a.can_refresh,
         "via": a.via}
        for r, a in accessible_reports(session, user)
    ]
