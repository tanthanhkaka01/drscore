"""Application log: ``runtime/logs/drs.log``, rotated daily (spec section 19)."""

from __future__ import annotations

import contextvars
import logging
import logging.handlers
from pathlib import Path

from drs.settings import PROJECT_ROOT

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
request_user: contextvars.ContextVar[str] = contextvars.ContextVar("request_user", default="-")
# Who is asking, for the access log: set per request by the web application, empty in the CLI.
request_client: contextvars.ContextVar[tuple[str | None, str | None]] = contextvars.ContextVar(
    "request_client", default=(None, None))  # (client ip, user agent)

FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] [%(user)s] %(name)s: %(message)s"


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        if not hasattr(record, "user"):  # a caller may pass it through extra={"user": ...}
            record.user = request_user.get()
        return True


_configured = False


def setup_logging(log_dir: Path | None = None, *, console: bool = True) -> None:
    global _configured
    if _configured:
        return
    log_dir = log_dir or PROJECT_ROOT / "runtime" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(FORMAT)
    context = _ContextFilter()

    handlers: list[logging.Handler] = [
        logging.handlers.TimedRotatingFileHandler(
            log_dir / "drs.log", when="midnight", backupCount=30, encoding="utf-8"
        )
    ]
    if console:
        handlers.append(logging.StreamHandler())
    root = logging.getLogger("drs")
    root.setLevel(logging.INFO)
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(context)
        root.addHandler(handler)
    _configured = True
