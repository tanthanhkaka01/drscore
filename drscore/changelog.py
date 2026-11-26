"""What changed, version by version, as each account may read it (``drscore/CHANGELOG.md``, ``CHANGELOG.vi.md``).

A version is ``## 1.7.0 — 2026-11-09`` followed by ``- `` lines, each with ``  - `` details. A line or a detail
marked ``[admin]`` is for administrators, ``[designer]`` for report designers and administrators; no mark is for
everyone. A version with nothing for an account still shows, with a line that says little. ``**bold**`` and
backticks are the only formatting. The page reads it through ``/changes`` and ``/api/changelog``; the files are
not served as they are.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from markupsafe import Markup, escape

HERE = Path(__file__).resolve().parent
FILES = {"en": HERE / "CHANGELOG.md", "vi": HERE / "CHANGELOG.vi.md"}
AUDIENCES = ("everyone", "designer", "admin")
LEVEL = {"everyone": 0, "designer": 1, "admin": 2}
GENERAL = {"en": "System improvements and small fixes.", "vi": "Cải thiện hệ thống và sửa lỗi nhỏ."}
SHOWN = 15
HEADING = re.compile(r"^## (\d+\.\d+\.\d+) [—-] (\d{4}-\d{2}-\d{2})\s*$")
MARK = re.compile(r"^\[(admin|designer)\]\s*")


@lru_cache(maxsize=1)
def version() -> str:
    """The running version: pyproject.toml beside the package in a source checkout (an editable install's
    metadata can lag behind it), else the installed package's metadata."""
    pyproject = HERE.parent / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            if data.get("project", {}).get("name") == "drscore":
                return data["project"]["version"]
        except (OSError, ValueError, KeyError):
            pass
    from importlib.metadata import PackageNotFoundError, version as installed

    try:
        return installed("drscore")
    except PackageNotFoundError:
        return "0.0.0"


@dataclass
class Line:
    audience: str
    text: str
    details: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Release:
    version: str
    date: str
    lines: list[Line]


def _marked(text: str) -> tuple[str, str]:
    m = MARK.match(text)
    return (m.group(1), text[m.end():]) if m else ("everyone", text)


def parse(text: str) -> list[Release]:
    releases: list[Release] = []
    for raw in text.splitlines():
        heading = HEADING.match(raw)
        if heading:
            releases.append(Release(heading.group(1), heading.group(2), []))
        elif not releases:
            continue
        elif raw.startswith("- "):
            releases[-1].lines.append(Line(*_marked(raw[2:].strip())))
        elif raw.startswith("  - ") and releases[-1].lines:
            releases[-1].lines[-1].details.append(_marked(raw[4:].strip()))
    return releases


@lru_cache(maxsize=4)
def releases(locale: str) -> tuple[Release, ...]:
    path = FILES.get(locale, FILES["en"])
    return tuple(parse(path.read_text(encoding="utf-8")))


def render_inline(text: str) -> Markup:
    """**bold** and `code`, everything else escaped."""
    out = str(escape(text))
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return Markup(out)


def audience_of(is_admin: bool, is_designer: bool) -> str:
    return "admin" if is_admin else "designer" if is_designer else "everyone"


def for_reader(locale: str, audience: str, since: str | None = None, limit: int = SHOWN) -> list[dict]:
    """The last versions (newer than ``since`` when given), each change as this reader may read it."""
    level = LEVEL[audience]
    out = []
    for release in releases(locale)[:limit]:
        if since and _key(release.version) <= _key(since):
            break
        changes = []
        for line in release.lines:
            if LEVEL[line.audience] > level:
                continue
            details = [render_inline(t) for a, t in line.details if LEVEL[a] <= level]
            changes.append({"text": render_inline(line.text), "details": details})
        if not changes:
            changes = [{"text": Markup(escape(GENERAL.get(locale, GENERAL["en"]))), "details": []}]
        out.append({"version": release.version, "date": release.date, "changes": changes})
    return out


def _key(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return (0,)
