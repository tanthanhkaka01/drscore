"""XLSX, CSV and TXT files from a snapshot (spec section 14.1).

Values come from the snapshot JSON (spec 11.4): numbers as numbers or decimal strings, dates as
``YYYY-MM-DD``, date-times as ``YYYY-MM-DDTHH:MM:SS``, times as ``HH:MM:SS``, NULL as None.
"""

from __future__ import annotations

import csv
import io
import json
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator, Sequence

import xlsxwriter

from drs.errors import DRSError
from drs.render.grid import GridColumn
from drs.settings import ExportSection

XLSX_MAX_DATA_ROWS = 1_048_575
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class ExportMeta:
    report_code: str
    report_name: str
    params: dict[str, Any]
    data_time: str  # in app.timezone, for people
    exported_by: str
    exported_at: str  # in app.timezone, for people


def headers(columns: Sequence[GridColumn], cfg: ExportSection) -> list[str]:
    return [c.label if cfg.header_source == "label" else c.name for c in columns]


# --------------------------------------------------------------------------------------------
# Plain text values (CSV, TXT)

def plain(value: Any, column_type: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(Decimal(repr(value)), "f")
    if isinstance(value, int):
        return str(value)
    if column_type == "datetime" and isinstance(value, str):
        return value.replace("T", " ")
    return str(value)


def guard(text: str) -> str:
    """Spreadsheet formula injection: a text cell that starts like a formula gets a quote."""
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def csv_chunks(columns: Sequence[GridColumn], rows: Sequence[Sequence[Any]], cfg: ExportSection) -> Iterator[bytes]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=cfg.csv_delimiter, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")

    def flush() -> bytes:
        data = buffer.getvalue().encode("utf-8")
        buffer.seek(0)
        buffer.truncate()
        return data

    writer.writerow(headers(columns, cfg))
    first = flush()
    yield (b"\xef\xbb\xbf" + first) if cfg.csv_bom else first
    for n, row in enumerate(rows, 1):
        cells = []
        for c, v in zip(columns, row):
            text = plain(v, c.type)
            if cfg.formula_guard and c.type == "text" and isinstance(v, str):
                text = guard(text)
            cells.append(text)
        writer.writerow(cells)
        if n % 2000 == 0:
            yield flush()
    yield flush()


def txt_cell(text: str, cfg: ExportSection) -> str:
    """No quoting: line breaks and tabs become a space, the delimiter its replacement, so every
    line has exactly (columns - 1) delimiters."""
    for ch in ("\r\n", "\r", "\n", "\t"):
        text = text.replace(ch, " ")
    return text.replace(cfg.txt_delimiter, cfg.txt_delimiter_replacement)


def txt_chunks(columns: Sequence[GridColumn], rows: Sequence[Sequence[Any]], cfg: ExportSection) -> Iterator[bytes]:
    eol = "\r\n" if cfg.txt_line_ending == "crlf" else "\n"
    lines = [cfg.txt_delimiter.join(txt_cell(h, cfg) for h in headers(columns, cfg))]
    for n, row in enumerate(rows, 1):
        lines.append(cfg.txt_delimiter.join(txt_cell(plain(v, c.type), cfg) for c, v in zip(columns, row)))
        if n % 2000 == 0:
            yield (eol.join(lines) + eol).encode("utf-8")
            lines = []
    if lines:
        yield (eol.join(lines) + eol).encode("utf-8")


# --------------------------------------------------------------------------------------------
# XLSX

def number_format(fmt: str) -> str | None:
    if fmt == "integer":
        return "#,##0"
    if fmt.startswith("number:"):
        n = int(fmt.split(":")[1])
        return "#,##0" + ("." + "0" * n if n else "")
    if fmt.startswith("percent:"):
        n = int(fmt.split(":")[1])
        return "0" + ("." + "0" * n if n else "") + "%"
    return {"date": "dd/mm/yyyy", "datetime": "dd/mm/yyyy hh:mm", "time": "hh:mm"}.get(fmt)


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


def _beyond_double(value: Any) -> bool:
    """An integer kept as a string because a double cannot hold it exactly: written as text."""
    return isinstance(value, str) and value.lstrip("-").isdigit() and abs(int(value)) > 2 ** 53


def _to_datetime(value: Any, column_type: str) -> datetime | None:
    try:
        if column_type == "date":
            return datetime.combine(date.fromisoformat(str(value)[:10]), time())
        if column_type == "datetime":
            return datetime.fromisoformat(str(value))
        if column_type == "time":
            return datetime.combine(date(1899, 12, 31), time.fromisoformat(str(value)))
    except ValueError:
        return None
    return None


def write_xlsx(columns: Sequence[GridColumn], rows: Sequence[Sequence[Any]], meta: ExportMeta,
               cfg: ExportSection) -> Path:
    """Writes the workbook to a temporary file (constant memory) and returns its path; the caller
    deletes it after sending."""
    if len(rows) > XLSX_MAX_DATA_ROWS:
        raise DRSError("EXPORT_TOO_LARGE")
    handle = tempfile.NamedTemporaryFile(prefix="drs_export_", suffix=".xlsx", delete=False)
    handle.close()
    path = Path(handle.name)
    workbook = xlsxwriter.Workbook(str(path), {"constant_memory": True, "strings_to_formulas": False,
                                               "strings_to_numbers": False, "strings_to_urls": False})
    try:
        bold = workbook.add_format({"bold": True})
        formats = {}
        for c in columns:
            nf = number_format(c.format)
            formats[c.index] = workbook.add_format({"num_format": nf}) if nf else None

        sheet = workbook.add_worksheet(meta.report_code[:31])
        for col, c in enumerate(columns):
            width = (c.width / 7.0) if c.width else max(10, min(50, len(c.label) + 4))
            sheet.set_column(col, col, width)
        sheet.write_row(0, 0, headers(columns, cfg), bold)
        sheet.freeze_panes(1, 0)
        sheet.autofilter(0, 0, max(len(rows), 1), max(len(columns) - 1, 0))

        for r, row in enumerate(rows, 1):
            for col, (c, v) in enumerate(zip(columns, row)):
                if v is None:
                    continue
                fmt = formats[c.index]
                if c.type == "bool" or isinstance(v, bool):
                    sheet.write_boolean(r, col, bool(v))
                elif c.type in ("int", "decimal", "float") and not _beyond_double(v) \
                        and (number := _to_number(v)) is not None:
                    sheet.write_number(r, col, number, fmt)
                elif c.type in ("date", "datetime", "time") and (dt := _to_datetime(v, c.type)) is not None:
                    sheet.write_datetime(r, col, dt, fmt)
                else:
                    sheet.write_string(r, col, str(v))  # never interpreted as a formula

        info = workbook.add_worksheet("Info")
        info.set_column(0, 0, 18)
        info.set_column(1, 1, 60)
        for i, (k, v) in enumerate([
            ("Report", f"{meta.report_name} ({meta.report_code})"),
            ("Parameters", json.dumps(meta.params, ensure_ascii=False)),
            ("Data as of", meta.data_time),
            ("Exported by", meta.exported_by),
            ("Exported at", meta.exported_at),
            ("Row count", len(rows)),
        ]):
            info.write_string(i, 0, k, bold)
            if isinstance(v, int):
                info.write_number(i, 1, v)
            else:
                info.write_string(i, 1, v)
    finally:
        workbook.close()
    return path
