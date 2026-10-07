"""Apache Superset configuration for DRS (spec section 14.3, milestone 6).

Loaded through SUPERSET_CONFIG_PATH. Values come from docker/.env. Check the setting names against
the documentation of the installed Superset version (6.1.0 here).
"""

import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

# Superset's own metadata (users, dashboards, charts) in its own database.
SQLALCHEMY_DATABASE_URI = (
    f"postgresql+psycopg2://superset:{os.environ['SUPERSET_DB_PASSWORD']}@postgres:5432/superset"
)

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
