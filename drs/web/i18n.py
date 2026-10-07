"""UI text in Vietnamese and English (spec section 15). No UI string is written in a template."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import Request

from drs.settings import get_settings

I18N_DIR = Path(__file__).resolve().parent / "i18n"
LOCALES = ("vi", "en")
LOCALE_COOKIE = "drs_locale"


@lru_cache(maxsize=None)
def messages(locale: str) -> dict[str, str]:
    return json.loads((I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))


def translate(locale: str, key: str, **values) -> str:
    text = messages(locale).get(key) or messages("en").get(key) or key
    return text.format(**values) if values else text


def request_locale(request: Request) -> str:
    chosen = request.cookies.get(LOCALE_COOKIE)
    return chosen if chosen in LOCALES else get_settings().app.app.default_locale


def error_message(locale: str, code: str) -> str:
    return translate(locale, f"error.{code}")
