"""JSON report cache (spec section 12, requirement R05, owner decisions of 2026-10-06).

* One table, ``drs_report_snapshot``, holds the cached results of every report.
* When a user opens a report the retention is checked: a snapshot that is still valid is served;
  an expired one is moved to ``drs_report_snapshot_history`` (one table for every report) and the
  data is read again. Cleaning moves snapshots, it never deletes them.
* Reading the data again happens under a lock row in ``drs_report_lock``, so identical requests
  run the source once; the others wait and are served the same snapshot.
"""

from __future__ import annotations

import hashlib
import os
import socket
import threading
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import delete, func, insert, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from drscore.db.engine import Database
from drscore.db.models import ReportLock, ReportSnapshot, ReportSnapshotHistory
from drscore.db.types import UTCDateTime, utcnow

COPIED_COLUMNS = ("snapshot_id", "report_id", "report_code", "cache_key", "params_json", "columns_json",
                  "rows_json", "row_count", "byte_size", "source_duration_ms", "created_at", "expires_at",
                  "created_by")
ARCHIVE_CHUNK = 200


def cache_key(datasource_code: str, query_text: str, params_canonical_json: str) -> str:
    query_hash = hashlib.sha256(query_text.encode("utf-8")).hexdigest()
    material = f"{datasource_code}\n{query_hash}\n{params_canonical_json}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def lock_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"[:100]


# --------------------------------------------------------------------------------------------
# Lookups

def find_valid(session: Session, report_id: int, key: str, now: datetime) -> ReportSnapshot | None:
    """The newest snapshot still inside its retention."""
    return session.scalars(
        select(ReportSnapshot)
        .where(ReportSnapshot.report_id == report_id, ReportSnapshot.cache_key == key,
               ReportSnapshot.expires_at > now)
        .order_by(ReportSnapshot.created_at.desc(), ReportSnapshot.snapshot_id.desc()).limit(1)
    ).first()


def newest_id(session: Session, report_id: int, key: str) -> int:
    return session.scalar(select(func.max(ReportSnapshot.snapshot_id)).where(
        ReportSnapshot.report_id == report_id, ReportSnapshot.cache_key == key)) or 0


def newer_than(session: Session, report_id: int, key: str, snapshot_id: int) -> ReportSnapshot | None:
    """A snapshot saved by another request after ``snapshot_id``, valid or not (TTL 0 included)."""
    return session.scalars(
        select(ReportSnapshot)
        .where(ReportSnapshot.report_id == report_id, ReportSnapshot.cache_key == key,
               ReportSnapshot.snapshot_id > snapshot_id)
        .order_by(ReportSnapshot.snapshot_id.desc()).limit(1)
    ).first()


def newest_kept(session: Session, report_id: int, key: str) -> ReportSnapshot | ReportSnapshotHistory | None:
    """The newest snapshot for this key, live or in the history (serve_stale_on_error)."""
    live = session.scalars(
        select(ReportSnapshot).where(ReportSnapshot.report_id == report_id, ReportSnapshot.cache_key == key)
        .order_by(ReportSnapshot.snapshot_id.desc()).limit(1)).first()
    if live is not None:
        return live
    return session.scalars(
        select(ReportSnapshotHistory)
        .where(ReportSnapshotHistory.report_id == report_id, ReportSnapshotHistory.cache_key == key)
        .order_by(ReportSnapshotHistory.snapshot_id.desc()).limit(1)).first()


def by_id(session: Session, report_id: int, snapshot_id: int) -> ReportSnapshot | ReportSnapshotHistory | None:
    """A snapshot of this report by id, from the cache or from the history."""
    for model in (ReportSnapshot, ReportSnapshotHistory):
        snap = session.get(model, snapshot_id)
        if snap is not None and snap.report_id == report_id:
            return snap
    return None


# --------------------------------------------------------------------------------------------
# Moving to the history

def archive_ids(session: Session, ids: Iterable[int], reason: str, now: datetime | None = None) -> int:
    """Moves these snapshots from the cache to the history: copy, then delete, in the caller's
    transaction, so a snapshot is never lost and never in both tables after commit."""
    now = now or utcnow()
    ids = list(ids)
    moved = 0
    for start in range(0, len(ids), ARCHIVE_CHUNK):
        chunk = ids[start:start + ARCHIVE_CHUNK]
        source = select(
            *[getattr(ReportSnapshot, c) for c in COPIED_COLUMNS],
            literal(now, UTCDateTime()).label("archived_at"),
            literal(reason).label("archive_reason"),
        ).where(ReportSnapshot.snapshot_id.in_(chunk))
        session.execute(insert(ReportSnapshotHistory).from_select(
            [*COPIED_COLUMNS, "archived_at", "archive_reason"], source))
        moved += session.execute(delete(ReportSnapshot).where(ReportSnapshot.snapshot_id.in_(chunk))).rowcount or 0
    return moved


def archive_key(session: Session, report_id: int, key: str, now: datetime, *, only_expired: bool) -> int:
    """Moves the snapshots of one (report, parameters) to the history: the expired ones, or all of
    them when the user forces a refresh."""
    stmt = select(ReportSnapshot.snapshot_id).where(ReportSnapshot.report_id == report_id,
                                                    ReportSnapshot.cache_key == key)
    if only_expired:
        stmt = stmt.where(ReportSnapshot.expires_at <= now)
    return archive_ids(session, list(session.scalars(stmt)), "EXPIRED", now)


def archive_expired(session: Session, now: datetime | None = None, report_id: int | None = None) -> int:
    """``cache purge``: moves every expired snapshot (of every report, or one) to the history."""
    now = now or utcnow()
    stmt = select(ReportSnapshot.snapshot_id).where(ReportSnapshot.expires_at <= now)
    if report_id is not None:
        stmt = stmt.where(ReportSnapshot.report_id == report_id)
    return archive_ids(session, list(session.scalars(stmt)), "EXPIRED", now)


def archive_report(session: Session, report_id: int, now: datetime | None = None) -> int:
    """``cache clear``: moves every snapshot of one report to the history."""
    ids = list(session.scalars(select(ReportSnapshot.snapshot_id).where(ReportSnapshot.report_id == report_id)))
    return archive_ids(session, ids, "CLEARED", now)


# --------------------------------------------------------------------------------------------
# Lock

def try_lock(database: Database, report_id: int, key: str, owner: str, stale_seconds: int,
             now: datetime | None = None) -> bool:
    """Takes the lock of one (report, parameters). A lock older than ``stale_seconds`` belongs to
    a request that died and is taken over. Committed at once, so other processes see it."""
    now = now or utcnow()
    with database.session() as s:
        s.execute(delete(ReportLock).where(ReportLock.report_id == report_id, ReportLock.cache_key == key,
                                           ReportLock.locked_at < now - timedelta(seconds=stale_seconds)))
    try:
        with database.session() as s:
            s.add(ReportLock(report_id=report_id, cache_key=key, locked_by=owner, locked_at=now))
        return True
    except IntegrityError:
        return False


def release_lock(database: Database, report_id: int, key: str, owner: str) -> None:
    with database.session() as s:
        s.execute(delete(ReportLock).where(ReportLock.report_id == report_id, ReportLock.cache_key == key,
                                           ReportLock.locked_by == owner))


def purge_stale_locks(session: Session, stale_seconds: int, now: datetime | None = None) -> int:
    now = now or utcnow()
    return session.execute(delete(ReportLock).where(
        ReportLock.locked_at < now - timedelta(seconds=stale_seconds))).rowcount or 0
