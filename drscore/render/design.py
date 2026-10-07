"""Report views and the design registry (spec section 13, owner decision of 2026-10-06).

Every report can be seen in up to three views, shown as tabs on the report page:

* ``grid``: the rows in a table - every report with a query has it, unless ``grid_enabled`` is
  false;
* ``html``: ``html_design_uri`` = ``file:<path>`` relative to ``designs.root``;
* ``bi``: ``bi_design_uri`` = ``superset:<embedded dashboard uuid>`` (embedded mode) or
  ``http(s)://...`` (link mode).

``report_type`` (GRID / HTML / BI) is the view the page opens on. A view that is the report's
``report_type`` is offered even when it is not designed yet, so that opening it shows
``DESIGN_NOT_SET`` (no design, no view). ``check`` runs before the source query of that view.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

View = Literal["grid", "html", "bi"]
VIEW_OF_TYPE: dict[str, View] = {"GRID": "grid", "HTML": "html", "BI": "bi"}

from jinja2 import FileSystemLoader, StrictUndefined, TemplateError, TemplateNotFound, TemplateSyntaxError
from jinja2.sandbox import ImmutableSandboxedEnvironment
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from drscore.db.models import Report, ReportRowFilter
from drscore.errors import DRSError
from drscore.settings import AppSettings

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class Design:
    kind: Literal["none", "html", "bi_link", "bi_embed"]
    uri: str | None = None
    template: str | None = None  # HTML: path inside designs.root, "/" separated
    url: str | None = None  # BI link mode
    dashboard_id: str | None = None  # BI embedded mode


# --------------------------------------------------------------------------------------------
# Sandboxed template environment for HTML designs

class RootedLoader(FileSystemLoader):
    """A loader that refuses any file outside the designs root: ``..``, absolute paths, and
    symbolic links that point out of the root."""

    def __init__(self, root: Path):
        super().__init__(str(root), encoding="utf-8", followlinks=False)
        self.root = root.resolve()

    def get_source(self, environment, template):
        if not safe_relative(template):
            raise TemplateNotFound(template)
        path = (self.root / template).resolve()
        if not path.is_relative_to(self.root):
            raise TemplateNotFound(template)
        return super().get_source(environment, template)


def safe_relative(path: str) -> bool:
    if not path or "\\" in path or "\x00" in path:
        return False
    p = PurePosixPath(path)
    return not p.is_absolute() and ".." not in p.parts and not re.match(r"^[A-Za-z]:", path)


_envs: dict[Path, ImmutableSandboxedEnvironment] = {}
_envs_lock = threading.Lock()


def environment(root: Path) -> ImmutableSandboxedEnvironment:
    """One sandboxed environment per designs root. Auto-escaping on; a template sees only the
    values it is given and the filters below - no file, database or Python access. Templates
    are reloaded when their file changes, so an edited design is used at the next view.
    Undefined values are strict: test optional ones with ``is defined``."""
    root = root.resolve()
    with _envs_lock:
        env = _envs.get(root)
        if env is None:
            from drscore.render import html_filters

            # StrictUndefined: a blocked attribute (__class__, ...) or a misspelt name is an error that
            # reaches the admin as DESIGN_INVALID with its line, not a silently empty value.
            env = ImmutableSandboxedEnvironment(loader=RootedLoader(root), autoescape=True, auto_reload=True,
                                                undefined=StrictUndefined)
            env.filters.update(html_filters.FILTERS)
            env.globals.update(html_filters.GLOBALS)
            _envs[root] = env
        return env


# --------------------------------------------------------------------------------------------
# Check

def _has_row_filter(session: Session, report_id: int) -> bool:
    return bool(session.scalar(select(func.count()).select_from(ReportRowFilter)
                               .where(ReportRowFilter.report_id == report_id)))


def available_views(report: Report) -> list[View]:
    """The tabs of a report, in display order."""
    views: list[View] = []
    if report.grid_enabled and report.query_text:
        views.append("grid")
    if (report.html_design_uri or "").strip() or report.report_type == "HTML":
        views.append("html")
    if (report.bi_design_uri or "").strip() or report.report_type == "BI":
        views.append("bi")
    return views


def default_view(report: Report) -> View | None:
    views = available_views(report)
    preferred = VIEW_OF_TYPE.get(report.report_type, "grid")
    return preferred if preferred in views else (views[0] if views else None)


def require_view(report: Report, view: str | None) -> View:
    """The view asked for (the report's default when None), if the report offers it."""
    chosen = view or default_view(report)
    if chosen not in available_views(report):
        raise DRSError("VIEW_NOT_AVAILABLE", admin_detail=f"view {chosen!r}: the report offers "
                                                          f"{', '.join(available_views(report)) or 'no view'}")
    return chosen


def check(session: Session, report: Report, settings: AppSettings, view: View) -> Design:
    """The design of one view. Raises DESIGN_NOT_SET / DESIGN_INVALID / DESIGN_NOT_FOUND (or
    BI_ENGINE_UNAVAILABLE, REPORT_MISCONFIGURED) with the stored URI and the reason as the admin
    detail."""
    if view == "grid":
        return Design("none")
    column = "html_design_uri" if view == "html" else "bi_design_uri"
    uri = (getattr(report, column) or "").strip()
    if not uri:
        raise DRSError("DESIGN_NOT_SET", admin_detail=f"{column} is empty")
    detail = f"{column} = {uri!r}: "

    if view == "html":
        if not uri.startswith("file:"):
            raise DRSError("DESIGN_INVALID", admin_detail=detail + "an HTML report needs file:<path>")
        relative = uri[5:].strip()
        if not safe_relative(relative):
            raise DRSError("DESIGN_INVALID", admin_detail=detail + "the path must stay inside designs.root")
        root = settings.designs.root_path.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise DRSError("DESIGN_INVALID", admin_detail=detail + "the path leaves designs.root (symbolic link)")
        if not path.is_file():
            raise DRSError("DESIGN_NOT_FOUND", admin_detail=detail + f"no file {path}")
        try:
            environment(root).get_template(relative)
        except TemplateSyntaxError as exc:
            raise DRSError("DESIGN_INVALID", admin_detail=detail + f"line {exc.lineno}: {exc.message}") from exc
        except TemplateNotFound as exc:
            raise DRSError("DESIGN_INVALID", admin_detail=detail + f"cannot load {exc.name}") from exc
        except (TemplateError, OSError, UnicodeDecodeError) as exc:
            raise DRSError("DESIGN_INVALID", admin_detail=detail + str(exc)) from exc
        return Design("html", uri=uri, template=relative)

    # BI
    if uri.startswith(("http://", "https://")):
        if _has_row_filter(session, report.report_id):
            raise DRSError("REPORT_MISCONFIGURED",
                           admin_detail=detail + "a report with row-filter rules must use embedded mode "
                                                 "(superset:<uuid>); DRS cannot filter a linked dashboard")
        return Design("bi_link", uri=uri, url=uri)
    if uri.startswith("superset:"):
        dashboard = uri[len("superset:"):].strip()
        if not UUID_RE.match(dashboard):
            raise DRSError("DESIGN_INVALID", admin_detail=detail + "superset: must be followed by the embedded uuid")
        if not settings.bi.superset.enabled:
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=detail + "[bi.superset] enabled = false")
        if _has_row_filter(session, report.report_id):
            # The row filter would travel as row-level security in the guest token; that part is
            # not built yet (owner, 2026-10-06), so DRS refuses rather than show unfiltered rows.
            raise DRSError("REPORT_MISCONFIGURED",
                           admin_detail=detail + "row-level security in the guest token is not built yet: "
                                                 "a report with row-filter rules cannot be embedded")
        if settings.bi.superset.check_design:
            from drscore.bi import superset

            if not superset.dashboard_exists(settings.bi.superset, dashboard):
                raise DRSError("DESIGN_NOT_FOUND", admin_detail=detail + "Superset has no embedded dashboard "
                                                                        "with this uuid")
        return Design("bi_embed", uri=uri, dashboard_id=dashboard)
    raise DRSError("DESIGN_INVALID", admin_detail=detail + "a BI report needs superset:<uuid> or an http(s) link")
