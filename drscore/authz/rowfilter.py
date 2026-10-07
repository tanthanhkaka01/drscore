"""Row filter per user (spec section 10.3, requirement R17).

For a non-admin user a result row is kept when every rule of the report passes. A rule
(``column_name``, ``attr_name``) passes when the user has the value ``*`` for the attribute, or the
row's value in the column, as text, equals one of the user's values (trimmed, case-insensitive).
A user with no value for a rule's attribute sees no rows. Admins are not filtered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from drscore.db.models import ReportRowFilter, User, UserAttribute
from drscore.errors import DRSError

ALL_VALUES = "*"


@dataclass(frozen=True)
class Rule:
    column_index: int
    allowed: frozenset[str] | None  # None = every value ("*")


def _key(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value).strip().casefold()


def user_attributes(session: Session, user: User) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for a in session.scalars(select(UserAttribute).where(UserAttribute.user_id == user.user_id)):
        values.setdefault(a.attr_name, []).append(a.attr_value)
    return values


def report_rules(session: Session, report_id: int) -> list[ReportRowFilter]:
    return list(session.scalars(
        select(ReportRowFilter).where(ReportRowFilter.report_id == report_id)
        .order_by(ReportRowFilter.row_filter_id)
    ))


def compile_rules(rules: Iterable[ReportRowFilter], column_names: Sequence[str],
                  attributes: dict[str, list[str]]) -> list[Rule]:
    """Resolves each rule to a column position. A rule on a column the result does not contain is
    ``REPORT_MISCONFIGURED`` - the filter never falls back to showing everything."""
    positions = {name.casefold(): i for i, name in enumerate(column_names)}
    compiled = []
    for rule in rules:
        index = positions.get(rule.column_name.casefold())
        if index is None:
            raise DRSError("REPORT_MISCONFIGURED",
                           admin_detail=f"row filter column {rule.column_name!r} is not in the result")
        values = attributes.get(rule.attr_name, [])
        if any(v.strip() == ALL_VALUES for v in values):
            compiled.append(Rule(index, None))
        else:
            compiled.append(Rule(index, frozenset(_key(v) for v in values)))
    return compiled


def apply(rows: Sequence[Sequence[Any]], rules: list[Rule]) -> list[Sequence[Any]]:
    active = [r for r in rules if r.allowed is not None]
    if not active:
        return list(rows)
    return [row for row in rows if all(_key(row[r.column_index]) in r.allowed for r in active)]


def filter_rows(session: Session, user: User, report_id: int, column_names: Sequence[str],
                rows: Sequence[Sequence[Any]]) -> list[Sequence[Any]]:
    """The rows of a result this user may see."""
    rules = report_rules(session, report_id)
    if not rules:
        return list(rows)
    # The columns are checked for admins too, so a broken rule is found by whoever runs it first.
    compiled = compile_rules(rules, column_names, {} if user.is_admin else user_attributes(session, user))
    if user.is_admin:
        return list(rows)
    return apply(rows, compiled)
