"""Choice lists: choices ticked on a page and copied for the reports (docs/choice-lists.md).

A list - which kinds of item a report counts, say - is decided by a person, not written into a
report's SQL: its items are defined once (``import``), and the person ticks them on a page opened by
a public link that is allowed to change that list (``publiclink add|set --list``). DRS keeps the
list in its own tables and copies it, after every change, into ``target_table`` of the mirror
schema - the schema the mirrored source tables are in - where a report joins it like any table:

    item_key  text primary key   the key the report joins on
    label, group_name, sort_order
    is_checked  boolean           ticked or not
    changed_at, changed_by        the last change of that item's tick

Every change of a tick is audited (who: the CLI user, or ``public:<token hint>`` and the address).
An item is never ticked by an import that only re-describes it: its tick stays as decided.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, Table, Text, delete, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema

from drscore.audit import audit
from drscore.db.engine import Database
from drscore.db.models import ChoiceItem, ChoiceList, MirrorTable
from drscore.db.types import utcnow
from drscore.settings import AppSettings

log = logging.getLogger(__name__)

CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,99}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
MAX_ITEMS = 500


class ListError(Exception):
    """A list, an item or a change that is not valid."""


def find(session: Session, code: str, *, active: bool = True) -> ChoiceList:
    found = session.scalars(select(ChoiceList).where(ChoiceList.list_code == code)).first()
    if found is None or (active and not found.is_active):
        raise ListError(f"no {'active ' if active else ''}choice list {code}")
    return found


def list_all(session: Session) -> list[ChoiceList]:
    return list(session.scalars(select(ChoiceList).order_by(ChoiceList.list_code)))


def _item(raw: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ListError(f"item {index + 1}: an object with key and label")
    key, label = str(raw.get("key") or "").strip(), str(raw.get("label") or "").strip()
    if not KEY_RE.match(key):
        raise ListError(f"item {index + 1}: key {key!r} - letters, digits, _ . - (at most 100)")
    if not label or len(label) > 200:
        raise ListError(f"item {key}: a label of 1 to 200 characters")
    group = str(raw.get("group") or "").strip() or None
    if group and len(group) > 100:
        raise ListError(f"item {key}: a group of at most 100 characters")
    sort = raw.get("sort", index)
    if not isinstance(sort, int) or isinstance(sort, bool):
        raise ListError(f"item {key}: sort is a whole number")
    checked = raw.get("checked", False)
    if not isinstance(checked, bool):
        raise ListError(f"item {key}: checked is true or false")
    return {"key": key, "label": label, "group": group, "detail": str(raw.get("detail") or "").strip() or None,
            "sort": sort, "checked": checked}


def put(session: Session, actor: str, spec: dict[str, Any], *, reset: bool = False) -> tuple[ChoiceList, dict[str, int]]:
    """Creates a list or describes it again, from {code, title, table, description, items: [{key,
    label, group, detail, sort, checked}]}. An item already in the list keeps its tick ("checked"
    only ticks a new item) unless ``reset``; an item no longer named is removed."""
    code = str(spec.get("code") or "").strip()
    if not CODE_RE.match(code):
        raise ListError(f"code {code!r}: a letter, then letters, digits, _ . - (at most 100)")
    title = str(spec.get("title") or "").strip()
    if not title or len(title) > 200:
        raise ListError(f"{code}: a title of 1 to 200 characters")
    table = str(spec.get("table") or "").strip()
    if not TABLE_RE.match(table):
        raise ListError(f"{code}: table {table!r} - lower-case letters, digits and _ (a plain table name)")
    raw_items = spec.get("items") or []
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > MAX_ITEMS:
        raise ListError(f"{code}: 1 to {MAX_ITEMS} items")
    items = [_item(raw, i) for i, raw in enumerate(raw_items)]
    keys = [i["key"] for i in items]
    if len(set(keys)) != len(keys):
        raise ListError(f"{code}: an item key twice: " + ", ".join(sorted({k for k in keys if keys.count(k) > 1})))
    if session.scalars(select(MirrorTable.mirror_code).where(MirrorTable.target_table == table)).first():
        raise ListError(f"{code}: table {table} is a mirror's table")
    other = session.scalars(select(ChoiceList).where(ChoiceList.target_table == table,
                                                     ChoiceList.list_code != code)).first()
    if other is not None:
        raise ListError(f"{code}: table {table} is list {other.list_code}'s")
    found = session.scalars(select(ChoiceList).where(ChoiceList.list_code == code)).first()
    created = found is None
    before = None if created else export_list(found)
    lst = found or ChoiceList(list_code=code, title=title, target_table=table)
    lst.title, lst.target_table = title, table
    lst.description = str(spec.get("description") or "").strip() or None
    if spec.get("active") is not None:
        lst.is_active = bool(spec["active"])
    if created:
        session.add(lst)
    now = utcnow()
    have = {i.item_key: i for i in lst.items}
    counts = {"added": 0, "changed": 0, "removed": 0}
    for it in items:
        row = have.pop(it["key"], None)
        if row is None:
            lst.items.append(ChoiceItem(item_key=it["key"], label=it["label"], group_name=it["group"],
                                        detail=it["detail"], sort_order=it["sort"], is_checked=it["checked"],
                                        changed_by=actor[:100], changed_at=now))
            counts["added"] += 1
            continue
        new = (it["label"], it["group"], it["detail"], it["sort"])
        if (row.label, row.group_name, row.detail, row.sort_order) != new:
            row.label, row.group_name, row.detail, row.sort_order = new
            counts["changed"] += 1
        if reset and row.is_checked != it["checked"]:
            row.is_checked, row.changed_by, row.changed_at = it["checked"], actor[:100], now
            counts["changed"] += 1
    for row in have.values():
        lst.items.remove(row)
        counts["removed"] += 1
    session.flush()
    audit(session, actor, "CHOICELIST_ADD" if created else "CHOICELIST_SET", "choice_list", code,
          before=before, after=export_list(lst))
    return lst, counts


def tick(session: Session, actor: str, code: str, checked: dict[str, bool], *, where: str | None = None) -> list[str]:
    """Ticks or unticks items of an active list; returns the keys that changed. ``where``: the
    address of a public reader, kept with the audit row."""
    lst = find(session, code)
    if not isinstance(checked, dict) or len(checked) > MAX_ITEMS:
        raise ListError(f"at most {MAX_ITEMS} items, as key: true / false")
    items = {i.item_key: i for i in lst.items}
    unknown = sorted(k for k in checked if k not in items)
    if unknown:
        raise ListError(f"{code} has no item {', '.join(unknown[:10])}")
    if any(not isinstance(v, bool) for v in checked.values()):
        raise ListError("each item: true or false")
    now, changed = utcnow(), []
    for key, value in checked.items():
        row = items[key]
        if row.is_checked != value:
            row.is_checked, row.changed_by, row.changed_at = value, actor[:100], now
            changed.append(key)
    if changed:
        lst.changed_by, lst.changed_at = actor[:100], now
        after: dict[str, Any] = {k: items[k].is_checked for k in sorted(changed)}
        if where:
            after["_from"] = where
        audit(session, actor, "CHOICELIST_TICK", "choice_list", code,
              before={k: not items[k].is_checked for k in sorted(changed)}, after=after)
    session.flush()
    return sorted(changed)


def export_list(lst: ChoiceList) -> dict[str, Any]:
    """The list as ``put`` takes it, ticks included."""
    return {"code": lst.list_code, "title": lst.title, "table": lst.target_table, "description": lst.description,
            "active": lst.is_active,
            "items": [{"key": i.item_key, "label": i.label, "group": i.group_name, "detail": i.detail,
                       "sort": i.sort_order, "checked": i.is_checked} for i in lst.items]}


def state(lst: ChoiceList) -> dict[str, Any]:
    """What a page shows: the items and their ticks, and the last change."""
    return {"code": lst.list_code, "title": lst.title, "description": lst.description,
            "changed_at": lst.changed_at.isoformat() if lst.changed_at else None, "changed_by": lst.changed_by,
            "items": [{"key": i.item_key, "label": i.label, "group": i.group_name, "detail": i.detail,
                       "checked": i.is_checked,
                       "changed_at": i.changed_at.isoformat() if i.changed_at else None} for i in lst.items]}


# --------------------------------------------------------------------------------------------
# The copy the reports join


def _table(database: Database, name: str) -> Table:
    return Table(name, MetaData(schema=database.mirror_schema),
                 Column("item_key", Text, primary_key=True), Column("label", Text, nullable=False),
                 Column("group_name", Text), Column("sort_order", Integer, nullable=False),
                 Column("is_checked", Boolean, nullable=False), Column("changed_at", DateTime),
                 Column("changed_by", Text))


def publish(database: Database, settings: AppSettings, code: str) -> int:
    """Writes the list's table in the mirror schema (made if missing, readable by ``[mirror]
    read_role``) with the list as it is now; returns its rows. Called after every change."""
    from drscore.mirror.runner import _role

    with database.session() as s:
        lst = find(s, code, active=False)
        name = lst.target_table
        rows = [{"item_key": i.item_key, "label": i.label, "group_name": i.group_name, "sort_order": i.sort_order,
                 "is_checked": i.is_checked, "changed_at": i.changed_at.replace(tzinfo=None) if i.changed_at else None,
                 "changed_by": i.changed_by} for i in lst.items]
    engine, schema = database.mirror_engine, database.mirror_schema
    table = _table(database, name)
    quote = engine.dialect.identifier_preparer.quote
    with engine.begin() as conn:
        if schema and not conn.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :s"), {"s": schema}).first():
            conn.execute(CreateSchema(schema, if_not_exists=True))
        if not inspect(conn).has_table(name, schema=schema):
            table.create(conn)
            log.info("choice list %s: created table %s", code, f"{schema}.{name}" if schema else name)
        role = _role(settings) if schema else None
        if role:
            conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA {quote(schema)} TO {quote(role)}")
            conn.exec_driver_sql(f"GRANT SELECT ON {quote(schema)}.{quote(name)} TO {quote(role)}")
        conn.execute(delete(table))  # the whole list, in the same transaction: a reader sees it whole
        if rows:
            conn.execute(table.insert(), rows)
    if engine.dialect.name == "postgresql":
        # statistics at once: a report joining a new table with none was planned as loops (seconds -> 15 s)
        with engine.begin() as conn:
            conn.exec_driver_sql(f"ANALYZE {quote(schema)}.{quote(name)}" if schema else f"ANALYZE {quote(name)}")
    return len(rows)
