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

# --------------------------------------------------------------------------------------------
# The DRS look: the colours, type and corners of the site kit the DRS portal is drawn with (kit 1.7.0,
# drscore/web/static/kit/), as Superset 6.1's Ant Design theme tokens, light and dark. The logo, the favicon
# and Manrope are files of the image (Dockerfile, drs-theme/): Superset's fontUrls token takes only Google / Adobe
# addresses, so the faces are loaded by templates/head_custom_extra.html from Superset's own /static.
APP_NAME = "DRS BI"
APP_ICON = "/static/assets/drs/logo.svg"
FAVICONS = [{"href": "/static/assets/drs/favicon.svg", "type": "image/svg+xml"}]

_KIT_TOKENS = {
    "brandAppName": APP_NAME,
    "brandLogoAlt": APP_NAME,
    "brandLogoUrl": APP_ICON,
    "brandLogoMargin": "16px 0",
    "brandLogoHref": "/",
    "brandLogoHeight": "28px",
    "brandSpinnerUrl": None,
    "brandSpinnerSvg": None,
    "fontUrls": [],
    "fontFamily": "Manrope, 'Segoe UI', Helvetica, Arial, sans-serif",
    "fontFamilyCode": "'JetBrains Mono', 'IBM Plex Mono', 'Courier New', monospace",
    "borderRadius": 10,
    "borderRadiusLG": 14,
    "borderRadiusSM": 8,
    "transitionTiming": 0.3,
    "brandIconMaxWidth": 37,
    "fontSizeXS": "8",
    "fontSizeXXL": "28",
    "fontWeightNormal": "400",
    "fontWeightLight": "300",
    "fontWeightStrong": "600",
    "fontWeightBold": "700",
}
THEME_DEFAULT = {
    "token": {
        **_KIT_TOKENS,
        "colorPrimary": "#4f46e5",  # --k-accent-light
        "colorLink": "#4f46e5",
        "colorError": "#c02626",  # --k-bad
        "colorWarning": "#b45309",  # --k-warn
        "colorSuccess": "#15803d",  # --k-ok
        "colorInfo": "#2563eb",
        "colorTextBase": "#18181b",  # --k-ink
        "colorBgBase": "#ffffff",  # --k-surface
        "colorBgLayout": "#f4f4f5",  # --k-bg-alt
        "colorBorder": "#d4d4d8",  # --k-line-strong
        "colorBorderSecondary": "#e4e4e7",  # --k-line
        "colorEditorSelection": "#e0e7ff",
    },
    "algorithm": "default",
}
THEME_DARK = {
    "token": {
        **_KIT_TOKENS,
        "colorPrimary": "#8b93ff",  # --k-accent-dark
        "colorLink": "#8b93ff",
        "colorError": "#f87171",
        "colorWarning": "#fbbf24",
        "colorSuccess": "#4ade80",
        "colorInfo": "#60a5fa",
        "colorTextBase": "#fafafa",
        "colorBgBase": "#121218",
        "colorBgLayout": "#0c0c12",
        "colorBorder": "#363640",
        "colorBorderSecondary": "#26262f",
        "colorEditorSelection": "#312e81",
    },
    "algorithm": "dark",
}

# Embedded dashboards (spec 14.3): a dashboard designed here is shown inside the DRS portal with a
# short-lived guest token issued by the DRS server. The user needs no Superset account.
FEATURE_FLAGS = {
    "EMBEDDED_SUPERSET": True,
}
GUEST_ROLE_NAME = "DRS_Embedded"  # create it in Settings > List Roles (see docs/superset-setup.md)
GUEST_TOKEN_JWT_SECRET = os.environ["SUPERSET_GUEST_TOKEN_SECRET"]
GUEST_TOKEN_JWT_ALGO = "HS256"
GUEST_TOKEN_JWT_EXP_SECONDS = 300  # = [bi.superset] guest_token_ttl_seconds of DRS

# Only the DRS portal may frame Superset dashboards. DRS_PORTAL_ORIGIN holds every address the portal
# is opened at, separated by commas or spaces - [app] base_url and [app] other_urls of DRS, for
# example "http://10.0.0.5:8090, https://portal.example.com" (no trailing /).
DRS_PORTAL_ORIGINS = [o.strip().rstrip("/") for o in
                      os.environ.get("DRS_PORTAL_ORIGIN", "http://localhost:8080").replace(",", " ").split()]
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
        "frame-ancestors": ["'self'", *DRS_PORTAL_ORIGINS],
    },
    "content_security_policy_nonce_in": ["script-src"],
    "force_https": False,  # set True (and serve over HTTPS) in production
    # Talisman marks the session cookie "Secure" unless told otherwise, and a client then does not
    # send it back over plain http: every API POST, DRS's guest-token request included, was
    # refused with "The CSRF session token is missing". Set True together with force_https.
    "session_cookie_secure": False,
    "frame_options": None,  # framing is governed by frame-ancestors above
}

# Behind a reverse proxy (Superset opened at an https address served by nginx / IIS): set
# SUPERSET_PROXY_FIX=1, so that Superset writes its own links and redirects with the address the
# browser used (X-Forwarded-Proto / -Host / -Port of the proxy). Opened directly, nothing changes.
ENABLE_PROXY_FIX = os.environ.get("SUPERSET_PROXY_FIX", "").strip().lower() in ("1", "true", "yes")
PROXY_FIX_CONFIG = {"x_for": 1, "x_proto": 1, "x_host": 1, "x_port": 1, "x_prefix": 1}


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

