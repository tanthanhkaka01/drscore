"""GRID reports: the columns a user sees and exports (spec section 14.1)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from drs.db.models import ReportColumn
from drs.errors import DRSError

FORMAT_RE = re.compile(r"^(text|integer|date|datetime|time|number:\d|percent:\d)$")
# Without a display_format a number is shown as the query returned it (owner decision 2026-10-07:
# the report writer decides what a cell shows). An integer used to get thousands separators here,
# so an id 14969547 read 14,969,547. A date has no text of its own and keeps the portal's format.
DEFAULT_FORMATS = {"date": "date", "datetime": "datetime", "time": "time"}
NUMERIC_TYPES = ("int", "decimal", "float")


@dataclass(frozen=True)
class GridColumn:
    index: int  # position in the snapshot rows
    name: str
    label: str
    type: str
    format: str
    width: int | None
    align: str
    sortable: bool
    filterable: bool
    frozen: bool
    footer: str | None
    visible: bool
    exported: bool

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "type": self.type, "format": self.format,
                "width": self.width, "align": self.align, "sortable": self.sortable,
                "filterable": self.filterable, "frozen": self.frozen, "footer": self.footer}


def _format(fmt: str | None, column_type: str, field: str) -> str:
    if not fmt:
        return DEFAULT_FORMATS.get(column_type, "text")
    if not FORMAT_RE.match(fmt):
        raise DRSError("REPORT_MISCONFIGURED", admin_detail=f"column {field}: unknown display_format {fmt!r}")
    return fmt


def grid_columns(session: Session, report_id: int, snapshot_columns: Sequence[dict[str, str]]) -> list[GridColumn]:
    """The configured columns in ``sort_order``; with no column rows, every result column as it
    comes. A configured column the result does not contain is ``REPORT_MISCONFIGURED``."""
    positions = {c["name"].casefold(): i for i, c in enumerate(snapshot_columns)}
    rows = list(session.scalars(select(ReportColumn).where(ReportColumn.report_id == report_id)
                                .order_by(ReportColumn.sort_order, ReportColumn.column_id)))
    if not rows:
        return [
            GridColumn(i, c["name"], c["name"], c["type"], _format(None, c["type"], c["name"]), None,
                       "right" if c["type"] in NUMERIC_TYPES else "left", True, True, False, None, True, True)
            for i, c in enumerate(snapshot_columns)
        ]
    result = []
    for r in rows:
        index = positions.get(r.field_name.casefold())
        if index is None:
            raise DRSError("REPORT_MISCONFIGURED",
                           admin_detail=f"column {r.field_name!r} is not in the result of the report")
        column_type = r.data_type or snapshot_columns[index]["type"]
        result.append(GridColumn(
            index=index, name=snapshot_columns[index]["name"], label=r.label, type=column_type,
            format=_format(r.display_format, column_type, r.field_name), width=r.width_px,
            align=r.align or ("right" if column_type in NUMERIC_TYPES else "left"),
            sortable=r.is_sortable, filterable=r.is_filterable, frozen=r.is_frozen,
            footer=r.footer_aggregate, visible=r.is_visible, exported=r.is_exported,
        ))
    return result


def project(rows: Sequence[Sequence[Any]], columns: Sequence[GridColumn]) -> list[list[Any]]:
    """Only these columns, in this order. Hidden columns never leave the server."""
    return [[row[c.index] for c in columns] for row in rows]
