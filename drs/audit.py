"""Audit and access logs (spec sections 8.2 and 19)."""

from __future__ import annotations

import getpass
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from drs.db.models import AccessLog, AuditLog

log = logging.getLogger(__name__)


SECRET_FIELDS = {"password_hash", "secret_ref"}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def row_snapshot(obj: Any) -> dict[str, Any]:
    """The columns of a row as JSON values, for before / after in the audit log; secrets masked."""
    data = {}
    for attr in sa_inspect(obj).mapper.column_attrs:
        value = getattr(obj, attr.key, None)
        data[attr.key] = ("***" if value else None) if attr.key in SECRET_FIELDS else _jsonable(value)
    return data


def cli_actor() -> str:
    try:
        return f"cli:{getpass.getuser()}"[:100]
    except Exception:  # no login name available (service account, container)
        return "cli"


def audit(session: Session, actor: str, action: str, object_type: str, object_key: str,
          before: Any = None, after: Any = None) -> None:
    """Records one change made through DRS, in the caller's transaction."""
    session.add(AuditLog(actor=actor, action=action, object_type=object_type,
                         object_key=str(object_key)[:200], before_json=before, after_json=after))


def log_access(username: str, action: str, status: str, **fields: Any) -> None:
    """Writes one row of drs_access_log in its own transaction, so it is kept even when the
    request itself fails. A failure to log is reported in the application log, never raised.

    Call it after the request's own transaction has committed or rolled back: on SQLite a second
    writer waits for the first one's lock."""
    from drs.db import get_database
    from drs.logsetup import request_client, request_id

    ip, agent = request_client.get()
    fields.setdefault("client_ip", ip)
    if fields["client_ip"] is None:
        fields["client_ip"] = ip
    fields.setdefault("user_agent", agent)
    fields["user_agent"] = (fields["user_agent"] or "")[:400] or None
    rid = request_id.get()
    fields.setdefault("request_id", None if rid == "-" else rid)
    if fields.get("error_message"):
        fields["error_message"] = str(fields["error_message"])[:2000]
    try:
        with get_database().session() as session:
            session.add(AccessLog(username=(username or "-")[:100], action=action, status=status, **fields))
    except Exception:
        log.exception("could not write the access log")
