"""Apache Superset configuration for DRS (spec section 14.3, milestone 6).

Loaded through SUPERSET_CONFIG_PATH. Values come from docker/.env. Check the setting names against
the documentation of the installed Superset version (6.1.0 here).
"""

import os

from flask import flash, jsonify, redirect, request, session
from flask_login import current_user
from sqlalchemy.engine import URL
from werkzeug.security import check_password_hash

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

# Superset's own metadata (users, dashboards, charts).
#
# Without SUPERSET_DB_PASSWORD: Superset's default, a SQLite file in its home folder
# (/app/superset_home - keep that folder on a volume). Nothing to install.
#
# With it: PostgreSQL. By default the database "superset" of the postgres service of
# docker/compose.yml; SUPERSET_DB_HOST / _PORT / _NAME / _USER point it at another server, and
# SUPERSET_DB_SCHEMA keeps it in one schema of a database it shares (the DRS database): every table
# of Superset is then created in that schema and nowhere else.
if os.environ.get("SUPERSET_DB_PASSWORD"):
    _schema = os.environ.get("SUPERSET_DB_SCHEMA") or ""
    SQLALCHEMY_DATABASE_URI = URL.create(
        "postgresql+psycopg2",
        username=os.environ.get("SUPERSET_DB_USER") or "superset",
        password=os.environ["SUPERSET_DB_PASSWORD"],  # escaped here: "#" or "@" in it is no problem
        host=os.environ.get("SUPERSET_DB_HOST") or "postgres",
        port=int(os.environ.get("SUPERSET_DB_PORT") or 5432),
        database=os.environ.get("SUPERSET_DB_NAME") or "superset",
        query={"options": f"-csearch_path={_schema}"} if _schema else {},
    ).render_as_string(hide_password=False)

# The datasets of a DRS that runs on SQLite are one SQLite file (database.toml, [sqlite] bi_path),
# mounted read-only at the path DRS_BI_SQLITE names. Superset refuses SQLite connections unless
# told otherwise; only its administrators create connections.
if os.environ.get("DRS_BI_SQLITE"):
    PREVENT_UNSAFE_DB_CONNECTIONS = False

# Embedded dashboards (spec 14.3): a dashboard designed here is shown inside the DRS portal with a
# short-lived guest token issued by the DRS server. The user needs no Superset account.
FEATURE_FLAGS = {
    "EMBEDDED_SUPERSET": True,
}
GUEST_ROLE_NAME = "DRS_Embedded"  # create it in Settings > List Roles (see docs/superset-setup.md)
GUEST_TOKEN_JWT_SECRET = os.environ["SUPERSET_GUEST_TOKEN_SECRET"]
GUEST_TOKEN_JWT_ALGO = "HS256"
GUEST_TOKEN_JWT_EXP_SECONDS = 300  # = [bi.superset] guest_token_ttl_seconds of DRS

# Only the DRS portal may frame Superset dashboards.
DRS_PORTAL_ORIGIN = os.environ.get("DRS_PORTAL_ORIGIN", "http://localhost:8080")
TALISMAN_ENABLED = True
TALISMAN_CONFIG = {
    "content_security_policy": {
        "base-uri": ["'self'"],
        "default-src": ["'self'"],
        "img-src": ["'self'", "blob:", "data:"],
        "worker-src": ["'self'", "blob:"],
        "connect-src": ["'self'"],
        "object-src": "'none'",
        "style-src": ["'self'", "'unsafe-inline'"],
        "script-src": ["'self'", "'strict-dynamic'"],
        "frame-ancestors": ["'self'", DRS_PORTAL_ORIGIN],
    },
    "content_security_policy_nonce_in": ["script-src"],
    "force_https": False,  # set True (and serve over HTTPS) in production
    # Talisman marks the session cookie "Secure" unless told otherwise, and a client then does not
    # send it back over plain http: every API POST, DRS's guest-token request included, was
    # refused with "The CSRF session token is missing". Set True together with force_https.
    "session_cookie_secure": False,
    "frame_options": None,  # framing is governed by frame-ancestors above
}

# Behind a reverse proxy, uncomment:
# ENABLE_PROXY_FIX = True


# --------------------------------------------------------------------------------------------
# First sign-in (owner decision 2026-10-07, as in DRS): the Superset administrator starts as
# admin / admin and must choose another password before doing anything else.
#
# A user whose password is still the initial one (SUPERSET_ADMIN_PASSWORD, "admin" by default) is
# sent to Superset's own "Reset Password" page; every other page answers with that page, every API
# call with 403, until the password is changed. Nothing is stored: as soon as the password is no
# longer the initial one, the user works normally. Guest users of embedded dashboards (DRS viewers)
# and users without a local password (LDAP / OAuth) are never concerned.

INITIAL_PASSWORD = os.environ.get("SUPERSET_ADMIN_PASSWORD") or "admin"
PASSWORD_CHANGE_URL = "/resetmypassword/form"
_ALLOWED_WHILE_DEFAULT = ("/resetmypassword/", "/static/", "/logout", "/login", "/health", "/healthcheck",
                          "/lang/", "/favicon.ico")


def _must_change_password():
    if not getattr(current_user, "is_authenticated", False) or getattr(current_user, "is_guest_user", False):
        return None
    user_id = getattr(current_user, "id", None)
    if session.get("drs_password_ok") == user_id:  # checked once per session: a hash check is slow
        return None
    stored = getattr(current_user, "password", None) or ""
    if not stored or not check_password_hash(stored, INITIAL_PASSWORD):
        session["drs_password_ok"] = user_id
        return None
    if request.path.startswith(_ALLOWED_WHILE_DEFAULT):
        return None
    message = "This is the first sign-in: choose a new password before you continue."
    if request.path.startswith("/api/"):
        return jsonify(message=message), 403
    flash(message, "warning")
    return redirect(PASSWORD_CHANGE_URL)


def FLASK_APP_MUTATOR(app):  # noqa: N802 - the name Superset looks for
    app.before_request(_must_change_password)

