"""Checks made by ``serve`` before it accepts requests, so a mistake in the set-up is reported once,
clearly, at start-up instead of as an error page later.

Fatal problems raise ``ConfigError`` (the server does not start); the others are returned as
warnings and printed. With ``[app] auto_migrate = true`` (the default) an empty or older DRS
database is created / migrated here: on PostgreSQL the schema and every table appear on the first
start. A database without an administrator is given the default one (``admin`` / ``admin``, to be
changed at the first sign-in), so a new installation can be signed in to at once.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from drscore.db.engine import Database
from drscore.settings import ConfigError, Settings, resolve_path


def _connect(database: Database) -> None:
    try:
        with database.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        reason = str(getattr(exc, "orig", exc)).strip().splitlines()[0]
        hint = ""
        if database.dialect == "postgresql":
            pg = database.settings.postgresql
            hint = (f" Check [postgresql] in database.toml (host {pg.host}:{pg.port}, database {pg.database}, "
                    f"user {pg.user}) and the password in the environment variable {pg.password_env}.")
        raise ConfigError(f"Cannot connect to the DRS database ({database.settings.describe()}): {reason}.{hint}") \
            from exc


def _migrate(database: Database, settings: Settings) -> list[str]:
    from drscore.db.migrate import current_revision, head_revision, upgrade

    before, head = current_revision(database), head_revision()
    if before == head:
        return []
    if not settings.app.app.auto_migrate:
        raise ConfigError(f"The DRS database is at revision {before or '(empty)'}, this version needs {head}: "
                          f"run `python -m drscore db upgrade` (or set [app] auto_migrate = true).")
    try:
        upgrade(database)
    except SQLAlchemyError as exc:
        reason = str(getattr(exc, "orig", exc)).strip().splitlines()[0]
        raise ConfigError(f"Creating / migrating the DRS database failed: {reason}. On PostgreSQL the DRS login "
                          f"must own the database or the schema (see docs/deployment.md).") from exc
    except Exception as exc:  # an unknown revision: the database is newer than this code
        raise ConfigError(f"Cannot migrate the DRS database from revision {before}: {exc}. "
                          f"Is this an older DRS version than the one that created the database?") from exc
    return [f"DRS database {'created' if before is None else 'migrated'}: revision {before or '(empty)'} -> {head}."]


def _default_admin(database: Database, settings: Settings) -> list[str]:
    from drscore.admin.service import DEFAULT_ADMIN, ensure_default_admin

    try:
        with database.session() as s:
            created = ensure_default_admin(s, "startup", settings.app)
    except IntegrityError:  # another server starting at the same moment has just created it
        return []
    if created is None:
        return []
    return [f"NOTE: the DRS database had no administrator: user \"{DEFAULT_ADMIN}\" was created with the "
            f"password \"{DEFAULT_ADMIN}\". The first sign-in must change it."]


def _warnings(database: Database, settings: Settings) -> list[str]:
    from drscore.db.models import User

    app = settings.app
    out = []
    key_env = app.app.secret_key_env
    key = os.environ.get(key_env)
    if not key:
        out.append(f"WARNING: {key_env} is not set. Datasource passwords cannot be stored or read until it is "
                   f"(`python -m drscore secret new-key`).")
    else:
        try:
            Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ConfigError(f"The environment variable {key_env} does not hold a valid key "
                              f"(`python -m drscore secret new-key` prints one).") from exc
    https = app.app.base_url.lower().startswith("https://")
    if https and not app.session.cookie_secure:
        out.append("WARNING: base_url is https but [session] cookie_secure = false: set it to true.")
    if app.session.cookie_secure and not https:
        out.append("WARNING: [session] cookie_secure = true: the browser sends the session cookie over https only, "
                   "so signing in over plain http will not work.")
    if not resolve_path(app.designs.root).is_dir():
        out.append(f"WARNING: the designs folder {resolve_path(app.designs.root)} does not exist ([designs] root).")
    with database.session() as s:
        admins = s.scalar(select(func.count()).select_from(User).where(User.is_admin, User.is_active))
    if not admins:
        out.append("NOTE: there is no administrator yet. Create one with "
                   "`python -m drscore user add admin --display-name \"Administrator\" --admin`.")
    return out


def preflight(database: Database, settings: Settings) -> list[str]:
    """Connects, creates / migrates the DRS database when allowed, gives it its first administrator,
    and returns the messages to print."""
    _connect(database)
    messages = _migrate(database, settings)
    messages += _default_admin(database, settings)
    return messages + _warnings(database, settings)
