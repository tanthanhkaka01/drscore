"""Filters and globals an HTML design may use (spec section 14.2)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"


def fmt_number(value: Any, decimals: int = 0) -> str:
    """12345.678 -> "12,346" (decimals 0) or "12,345.68" (decimals 2). Empty for NULL."""
    if value is None or value == "":
        return ""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    decimals = max(0, min(int(decimals), 10))
    return f"{number:,.{decimals}f}"


def fmt_date(value: Any) -> str:
    """"2026-12-31" (or a date-time) -> "31/12/2026"."""
    text = "" if value is None else str(value)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return f"{text[8:10]}/{text[5:7]}/{text[0:4]}"
    return text


def fmt_datetime(value: Any) -> str:
    """"2026-12-31T17:45:00" -> "31/12/2026 17:45"."""
    text = "" if value is None else str(value)
    if len(text) >= 16 and text[10] in "T ":
        return f"{fmt_date(text)} {text[11:16]}"
    return fmt_date(text)


def static(path: str) -> str:
    """URL of a file served by the portal, for example static('vendor/echarts/echarts.min.js').

    The file's modification time rides along as ``?v=``. Static files carry no cache header, so a
    browser keeps one for hours without asking again: after ``report.js`` was fixed, the page kept
    running the old script and showing its error. With the version in the address a changed file
    is a new address, and an unchanged one stays cached."""
    clean = str(path).lstrip("/")
    try:
        version = int((STATIC_DIR / clean).stat().st_mtime)
    except OSError:  # not a file of the portal: the address is returned as asked, and answers 404
        return "/static/" + clean
    return f"/static/{clean}?v={version}"


FILTERS = {"fmt_number": fmt_number, "fmt_date": fmt_date, "fmt_datetime": fmt_datetime}
GLOBALS = {"static": static}
