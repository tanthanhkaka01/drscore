"""HTML reports: render a design with one user's rows (spec section 14.2)."""

from __future__ import annotations

from typing import Any

from jinja2 import TemplateError
from jinja2.sandbox import SecurityError
from sqlalchemy.orm import Session

from drscore.authz.rowfilter import filter_rows
from drscore.db.models import Report, User
from drscore.db.types import utcnow
from drscore.errors import DRSError
from drscore.render.design import Design, environment
from drscore.render.grid import grid_columns
from drscore.settings import AppSettings


def render(session: Session, settings: AppSettings, report: Report, user: User, snapshot, design: Design,
           csp_nonce: str) -> str:
    """The design rendered in the sandbox, with the rows of the snapshot this user may see."""
    names = [c["name"] for c in snapshot.columns_json]
    rows = filter_rows(session, user, report.report_id, names, snapshot.rows_json)
    labels = {c.name: c.label for c in grid_columns(session, report.report_id, snapshot.columns_json)}
    tz = settings.tz
    context: dict[str, Any] = {
        "report": {"code": report.report_code, "name": report.report_name, "description": report.description},
        "params": dict(snapshot.params_json or {}),
        "columns": [{"name": c["name"], "label": labels.get(c["name"], c["name"]), "type": c["type"]}
                    for c in snapshot.columns_json],
        "rows": [dict(zip(names, row)) for row in rows],
        "row_count": len(rows),
        "snapshot": {
            "created_at": snapshot.created_at.astimezone(tz).strftime("%Y-%m-%dT%H:%M:%S"),
            "expires_at": snapshot.expires_at.astimezone(tz).strftime("%Y-%m-%dT%H:%M:%S"),
            "is_stale": snapshot.expires_at <= utcnow(),
        },
        "user": {"username": user.username, "display_name": user.display_name},
        "now": utcnow().astimezone(tz).strftime("%Y-%m-%dT%H:%M:%S"),
        "csp_nonce": csp_nonce,
    }
    try:
        template = environment(settings.designs.root_path).get_template(design.template)
        return template.render(context)
    except SecurityError as exc:
        raise DRSError("DESIGN_INVALID", admin_detail=f"{design.uri}: not allowed in a design: {exc}") from exc
    except TemplateError as exc:
        line = getattr(exc, "lineno", None)
        where = f" line {line}" if line else ""
        raise DRSError("DESIGN_INVALID", admin_detail=f"{design.uri}{where}: {exc}") from exc
    except Exception as exc:  # anything else a design does wrong (overflow, bad types, ...)
        raise DRSError("DESIGN_INVALID", admin_detail=f"{design.uri}: {exc.__class__.__name__}: {exc}") from exc
