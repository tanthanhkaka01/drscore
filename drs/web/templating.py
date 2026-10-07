"""Server-rendered pages on the Tabler layout (spec sections 15, R18)."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from drs.authz.permissions import menu
from drs.db import get_database
from drs.db.models import User
from drs.render.html_filters import static as versioned_static
from drs.settings import get_settings
from drs.web.i18n import LOCALES, messages, request_locale, translate

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))  # auto-escaping on


def static_url(path: str) -> str:
    return versioned_static(path)  # one rule for the portal's pages and for HTML designs


def render(request: Request, name: str, status_code: int = 200, *, with_menu: bool = True, **context: Any):
    locale = request_locale(request)
    auth = getattr(request.state, "auth", None)
    groups: list[dict] = []
    if auth is not None and with_menu:
        with get_database().session() as s:
            user = s.scalars(select(User).where(User.user_id == auth.user_id)).one_or_none()
            if user is not None:
                groups = menu(s, user)
    context.update(
        locale=locale,
        locales=LOCALES,
        t=partial(translate, locale),
        static=static_url,
        auth=auth,
        menu=groups,
        app_name=get_settings().app.app.name,
        path=request.url.path,
        sso_enabled=get_settings().app.auth.sso.enabled,
    )
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def client_messages(locale: str, prefixes: tuple[str, ...]) -> dict[str, str]:
    """The UI strings a page's JavaScript needs, so the script holds no text of its own."""
    return {k: v for k, v in messages(locale).items() if k.startswith(prefixes)}
