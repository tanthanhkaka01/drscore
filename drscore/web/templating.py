"""Server-rendered pages on the Tabler layout (spec sections 15, R18)."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from drscore import changelog
from drscore.authz.design import design_rights
from drscore.authz.permissions import menu
from drscore.db import get_database
from drscore.db.models import ReportDraft, User
from drscore.render.html_filters import static as versioned_static
from drscore.settings import get_settings
from drscore.web import navigation
from drscore.web.i18n import LOCALES, messages, request_locale, translate

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))  # auto-escaping on


def public_cell(value, column_type: str) -> str:
    """A grid value on a public link's page (no script draws that table): numbers grouped, empty for null."""
    if value is None:
        return ""
    if column_type in ("int", "decimal", "float") and not isinstance(value, bool):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        return f"{number:,.0f}" if column_type == "int" or number.is_integer() else f"{number:,.2f}"
    return str(value)


templates.env.filters["public_cell"] = public_cell


def static_url(path: str) -> str:
    return versioned_static(path)  # one rule for the portal's pages and for HTML designs


def shell_context(request: Request, locale: str | None = None, *, with_menu: bool = True,
                  overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the frame of a page needs (templates/_shell.html): the user, the menu, the versions, the words.

    The portal's pages and the admin pages (drscore/admin/web.py) take it from here, so both draw the same menu.
    ``overrides`` are values a page set itself (``feedback_on``, ``can_design``...); ``active_group`` and
    ``active_report`` in it mark the report being read.
    """
    overrides = overrides or {}
    locale = locale or request_locale(request)
    auth = getattr(request.state, "auth", None)
    groups: list = []
    is_admin = False
    values: dict[str, Any] = {"can_design": False, "pending_drafts": 0, "new_feedback": 0, "ai_shown": False}
    settings = get_settings().app
    if auth is not None and with_menu:
        from drscore import feedback
        from drscore.typhon import agent

        with get_database().session() as s:
            user = s.scalars(select(User).where(User.user_id == auth.user_id)).one_or_none()
            if user is not None:
                is_admin = bool(user.is_admin)
                groups = menu(s, user)
                values["can_design"] = design_rights(s, user).is_designer
                if user.is_admin:
                    values["pending_drafts"] = s.scalar(
                        select(func.count(ReportDraft.draft_id)).where(ReportDraft.status == "PENDING")
                    ) or 0
                    if settings.feedback.enabled:
                        values["new_feedback"] = feedback.new_count(s)
                values["ai_shown"] = agent.shown_to(s, user, settings)
    values["feedback_on"] = settings.feedback.enabled and auth is not None
    values["ai_name"] = settings.ai.name
    for key in values:
        if key in overrides:
            values[key] = overrides[key]
    tr = partial(translate, locale)
    values.update(
        locale=locale,
        locales=LOCALES,
        t=tr,
        static=static_url,
        auth=auth,
        menu=groups,
        group_colors={g["code"]: navigation.group_color(i) for i, g in enumerate(groups)},
        app_name=settings.app.name,
        app_version=changelog.version(),
        path=request.url.path,
        sso_enabled=settings.auth.sso.enabled,
    )
    values["nav"] = navigation.build(
        tr, request.url.path, groups=groups, active_group=overrides.get("active_group"),
        active_report=overrides.get("active_report"), is_admin=is_admin, can_design=values["can_design"],
        ai_shown=values["ai_shown"], ai_name=values["ai_name"], feedback_on=values["feedback_on"],
        pending_drafts=values["pending_drafts"], new_feedback=values["new_feedback"])
    return values


def render(request: Request, name: str, status_code: int = 200, *, with_menu: bool = True,
           locale: str | None = None, **context: Any):
    shell = shell_context(request, locale, with_menu=with_menu, overrides=context)
    context.update(shell, shell_ctx=shell)
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def client_messages(locale: str, prefixes: tuple[str, ...]) -> dict[str, str]:
    """The UI strings a page's JavaScript needs, so the script holds no text of its own."""
    return {k: v for k, v in messages(locale).items() if k.startswith(prefixes)}
