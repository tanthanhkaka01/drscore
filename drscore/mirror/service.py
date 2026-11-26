"""Mirror definitions in the DRS database: add, change, list, import and export."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from drscore.audit import audit, row_snapshot
from drscore.db.models import Datasource, MirrorTable
from drscore.mirror.spec import MirrorError, MirrorSpec


def _datasource(session: Session, code: str) -> Datasource:
    ds = session.scalars(select(Datasource).where(Datasource.datasource_code == code)).first()
    if ds is None:
        raise MirrorError(f"no datasource {code}")
    return ds


def get(session: Session, code: str) -> MirrorTable:
    m = session.scalars(select(MirrorTable).where(MirrorTable.mirror_code == code)).first()
    if m is None:
        raise MirrorError(f"no mirror {code}")
    return m


def _check_target(session: Session, spec: MirrorSpec, datasource_id: int, mirror_id: int | None) -> None:
    """Mirrors that share a target table share its key; one datasource writes a target once."""
    for other in session.scalars(select(MirrorTable).where(MirrorTable.target_table == spec.target)):
        if other.mirror_id == mirror_id:
            continue
        if [k.lower() for k in (other.key_columns_json or [])] != [k.lower() for k in (spec.key or [])]:
            raise MirrorError(f"mirror {spec.code}: target {spec.target} is also written by {other.mirror_code} "
                              f"with the key {other.key_columns_json or []}; use the same key")
        if other.datasource_id == datasource_id:
            raise MirrorError(f"mirror {spec.code}: {other.mirror_code} already copies datasource "
                              f"{other.datasource.datasource_code} into {spec.target}")


def _apply(m: MirrorTable, spec: MirrorSpec, ds: Datasource) -> None:
    changed_source = (m.mirror_id is not None and (
        m.datasource_id != ds.datasource_id or m.source_object != spec.source or m.watermark_column != spec.watermark
        or m.where_sql != spec.where or m.target_table != spec.target or m.columns_json != spec.columns
        or m.key_columns_json != spec.key))
    m.mirror_code = spec.code
    m.datasource_id = ds.datasource_id
    m.source_object = spec.source
    m.columns_json = spec.columns
    m.where_sql = spec.where
    m.target_table = spec.target
    m.key_columns_json = spec.key
    m.mode = spec.mode
    m.watermark_column = spec.watermark
    m.lookback_seconds = spec.lookback_seconds
    m.interval_minutes = spec.interval_minutes
    m.full_refresh_hours = spec.full_refresh_hours
    m.run_hours = spec.run_hours
    m.full_hours = spec.full_hours
    m.read_uncommitted = spec.read_uncommitted
    m.is_active = spec.active
    if changed_source:  # what was read before is not what is read now (a new column too): a full copy
        m.watermark_value = None


def save(session: Session, actor: str, spec: MirrorSpec) -> tuple[MirrorTable, bool]:
    """Adds the mirror, or replaces the definition of the one with this code. (row, created)."""
    ds = _datasource(session, spec.datasource)
    m = session.scalars(select(MirrorTable).where(MirrorTable.mirror_code == spec.code)).first()
    _check_target(session, spec, ds.datasource_id, m.mirror_id if m else None)
    created = m is None
    before = None if created else row_snapshot(m)
    if created:
        m = MirrorTable()
    _apply(m, spec, ds)
    session.add(m)
    session.flush()
    audit(session, actor, "MIRROR_ADD" if created else "MIRROR_SET", "mirror", spec.code,
          before=before, after=row_snapshot(m))
    return m, created


def spec_from_row(m: MirrorTable) -> dict[str, Any]:
    """The definition of a mirror as an import file holds it."""
    out: dict[str, Any] = {
        "code": m.mirror_code, "datasource": m.datasource.datasource_code, "source": m.source_object,
        "target": m.target_table, "columns": m.columns_json, "where": m.where_sql, "key": m.key_columns_json,
        "mode": m.mode, "watermark": m.watermark_column, "lookback_seconds": m.lookback_seconds,
        "interval_minutes": m.interval_minutes, "full_refresh_hours": m.full_refresh_hours,
        "run_hours": m.run_hours, "full_hours": m.full_hours, "read_uncommitted": m.read_uncommitted, "active": m.is_active,
    }
    return {k: v for k, v in out.items() if v not in (None, [], False) or k == "active"}


def status_of(m: MirrorTable) -> dict[str, Any]:
    return {
        "status": m.last_status, "started_at": m.last_started_at, "finished_at": m.last_finished_at,
        "last_full_at": m.last_full_at, "rows": m.last_rows, "duration_ms": m.last_duration_ms,
        "watermark": m.watermark_value, "error": m.last_error, "running_on": m.lock_owner,
    }


def reset_watermark(session: Session, actor: str, code: str) -> MirrorTable:
    m = get(session, code)
    before = row_snapshot(m)
    m.watermark_value = None
    audit(session, actor, "MIRROR_SET", "mirror", code, before=before, after=row_snapshot(m))
    return m


def list_all(session: Session) -> list[MirrorTable]:
    return list(session.scalars(select(MirrorTable).order_by(MirrorTable.target_table, MirrorTable.mirror_code)))
