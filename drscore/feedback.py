"""Góp ý: feedback any signed-in user sends from the portal; administrators read and handle it
(docs/feedback-typhon-notices.md).

A feedback has a kind (bug, idea, question), an urgency, the page it is about, the sender's message, files
(screenshots, a spreadsheet, ...) and a thread of answers. Its sender sees their own; an administrator sees
all, answers, and sets the status (new, seen, doing, done, won't fix). The sender may write again - which
reopens one that was closed - and tick a fixed one as checked.

A new feedback goes to the AI agent first when it is on (``[ai] feedback_intake``): its reply goes in the
thread and its note to the administrators. Then the administrators' Telegram / Zalo group is told.
"""

from __future__ import annotations

import logging
import secrets as pysecrets
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from drscore import notify
from drscore.db.models import (
    FEEDBACK_KINDS,
    FEEDBACK_STATUSES,
    FEEDBACK_URGENCIES,
    Feedback,
    FeedbackComment,
    FeedbackFile,
    User,
)
from drscore.db.types import utcnow
from drscore.errors import DRSError
from drscore.settings import AppSettings

log = logging.getLogger(__name__)

MAX_MESSAGE = 8000
CLOSED = ("done", "wontfix")
EXTENSION = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
             "application/pdf": "pdf", "text/plain": "txt", "text/csv": "csv",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx"}
INLINE = {"image/png", "image/jpeg", "image/webp", "image/gif"}  # shown in the page; the rest is downloaded


def app_version() -> str | None:
    from drscore import changelog

    return changelog.version()


def sniff(data: bytes, name: str) -> str | None:
    """What a file really is, from its first bytes (never from the name or type the browser sends)."""
    def starts(*sig: int) -> bool:
        return data[: len(sig)] == bytes(sig)

    lower = name.lower()
    if starts(0x89, 0x50, 0x4E, 0x47):
        return "image/png"
    if starts(0xFF, 0xD8, 0xFF):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if starts(0x50, 0x4B, 0x03, 0x04) and lower.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if starts(0x50, 0x4B, 0x03, 0x04) and lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if lower.endswith((".txt", ".csv", ".log")) and b"\x00" not in data[:8192]:
        return "text/csv" if lower.endswith(".csv") else "text/plain"
    return None


@dataclass
class Upload:
    name: str
    data: bytes


def _user(session, username: str) -> User:
    return session.scalars(select(User).where(User.username == username)).one()


def _check_choice(value: str, allowed: tuple[str, ...], field: str) -> str:
    if value not in allowed:
        raise DRSError("FEEDBACK_INVALID", detail={field: f"one of {', '.join(allowed)}"})
    return value


def create(database, settings: AppSettings, username: str, *, kind: str, urgency: str, message: str,
           page_path: str | None, uploads: list[Upload]) -> int:
    cfg = settings.feedback
    message = (message or "").strip()
    if not message or len(message) > MAX_MESSAGE:
        raise DRSError("FEEDBACK_INVALID", detail={"message": f"1 to {MAX_MESSAGE} characters"})
    _check_choice(kind, FEEDBACK_KINDS, "kind")
    _check_choice(urgency, FEEDBACK_URGENCIES, "urgency")
    uploads = [u for u in uploads if u.data]
    if len(uploads) > cfg.max_files:
        raise DRSError("FEEDBACK_INVALID", detail={"files": f"at most {cfg.max_files}"})
    typed = []
    for u in uploads:
        if len(u.data) > cfg.max_file_mb * 1024 * 1024:
            raise DRSError("FEEDBACK_INVALID", detail={"files": f"{u.name}: at most {cfg.max_file_mb} MB"})
        media = sniff(u.data, u.name)
        if media is None:
            raise DRSError("FEEDBACK_INVALID", detail={"files": f"{u.name}: an image, PDF, XLSX, DOCX, CSV or TXT file"})
        typed.append((u, media))
    page = (page_path or "").strip()[:400] or None
    if page and (not page.startswith("/") or page.startswith("//")):
        page = None
    with database.session() as s:
        fb = Feedback(username=username, kind=kind, urgency=urgency, message=message, page_path=page,
                      app_version=app_version())
        s.add(fb)
        s.flush()
        folder = cfg.files_path / str(fb.feedback_id)
        written: list[Path] = []
        try:
            for u, media in typed:
                folder.mkdir(parents=True, exist_ok=True)
                stored = f"{pysecrets.token_hex(8)}.{EXTENSION[media]}"
                (folder / stored).write_bytes(u.data)
                written.append(folder / stored)
                s.add(FeedbackFile(feedback_id=fb.feedback_id, file_name=Path(u.name).name[:200] or stored,
                                   media_type=media, size_bytes=len(u.data), stored_name=stored))
            s.flush()
        except Exception:
            for path in written:
                path.unlink(missing_ok=True)
            raise
        feedback_id = fb.feedback_id
    after_create(database, settings, feedback_id)
    return feedback_id


def after_create(database, settings: AppSettings, feedback_id: int) -> None:
    """The agent reads it first when it is on; then (or at once) the administrators are told."""
    from drscore.typhon import agent

    try:
        if settings.ai.feedback_intake and agent.availability(settings).on:
            agent.intake(database, settings, feedback_id)
            return
    except Exception:
        log.exception("could not start the feedback intake of %s", feedback_id)
    tell_new(database, settings, feedback_id)


def _display_name(session, username: str) -> str | None:
    return session.scalar(select(User.display_name).where(User.username == username))


def tell_new(database, settings: AppSettings, feedback_id: int) -> None:
    try:
        with database.session() as s:
            fb = s.get(Feedback, feedback_id)
            text = notify.feedback_text(settings, fb, "feedback_new", user_name=_display_name(s, fb.username))
        notify.notice(settings, "feedback_new", text)
    except Exception:
        log.exception("could not tell about feedback %s", feedback_id)


def visible(session, username: str, feedback_id: int) -> tuple[Feedback, bool]:
    """(the feedback, the user is an administrator); its sender or an administrator only."""
    user = _user(session, username)
    fb = session.get(Feedback, feedback_id)
    if fb is None or (fb.username != username and not user.is_admin):
        raise DRSError("FEEDBACK_NOT_FOUND")
    return fb, user.is_admin


def listing(session, username: str, *, status: str | None = None, mine: bool = False, limit: int = 200) -> list[Feedback]:
    user = _user(session, username)
    q = select(Feedback).order_by(Feedback.feedback_id.desc()).limit(limit)
    if mine or not user.is_admin:
        q = q.where(Feedback.username == username)
    if status == "open":
        q = q.where(Feedback.status.not_in(CLOSED))
    elif status in FEEDBACK_STATUSES:
        q = q.where(Feedback.status == status)
    return list(session.scalars(q))


def new_count(session) -> int:
    return session.scalar(select(func.count(Feedback.feedback_id)).where(Feedback.status == "new")) or 0


def comment(database, settings: AppSettings, username: str, feedback_id: int, message: str) -> None:
    message = (message or "").strip()
    if not message or len(message) > MAX_MESSAGE:
        raise DRSError("FEEDBACK_INVALID", detail={"message": f"1 to {MAX_MESSAGE} characters"})
    event = None
    with database.session() as s:
        fb, is_admin = visible(s, username, feedback_id)
        by_sender = fb.username == username
        s.add(FeedbackComment(feedback_id=feedback_id, username=username,
                              author_kind="user" if by_sender else "admin", message=message))
        if by_sender:
            event = "feedback_reopened" if fb.status in CLOSED else "feedback_comment"
            if fb.status in CLOSED:
                fb.status, fb.checked_at = "new", None
        elif fb.status == "new":
            fb.status = "seen"
        if not by_sender:
            fb.handled_by, fb.handled_at = username, utcnow()
        fb.updated_at = utcnow()
        text = notify.feedback_text(settings, fb, event, by=username, message=message,
                                    user_name=_display_name(s, fb.username)) if event else None
    if event:
        notify.notice(settings, event, text)


def change(database, username: str, feedback_id: int, *, status: str | None = None, kind: str | None = None,
           urgency: str | None = None, done_version: str | None = None) -> None:
    with database.session() as s:
        fb, is_admin = visible(s, username, feedback_id)
        if not is_admin:
            raise DRSError("FEEDBACK_FORBIDDEN")
        if status:
            fb.status = _check_choice(status, FEEDBACK_STATUSES, "status")
        if kind:
            fb.kind = _check_choice(kind, FEEDBACK_KINDS, "kind")
        if urgency:
            fb.urgency = _check_choice(urgency, FEEDBACK_URGENCIES, "urgency")
        if done_version is not None:
            fb.done_version = done_version.strip()[:20] or None
        fb.handled_by, fb.handled_at = username, utcnow()


def set_checked(database, username: str, feedback_id: int, checked: bool) -> None:
    with database.session() as s:
        fb, _ = visible(s, username, feedback_id)
        if fb.username != username:
            raise DRSError("FEEDBACK_FORBIDDEN")
        if fb.status != "done":
            raise DRSError("FEEDBACK_INVALID", detail={"status": "only a feedback marked done can be checked"})
        fb.checked_at = utcnow() if checked else None


def file_of(database, settings: AppSettings, username: str, feedback_id: int, file_id: int) -> tuple[Path, str, str]:
    """(path on disk, media type, name) of one file of a feedback the user may see."""
    with database.session() as s:
        visible(s, username, feedback_id)
        f = s.get(FeedbackFile, file_id)
        if f is None or f.feedback_id != feedback_id:
            raise DRSError("FEEDBACK_NOT_FOUND")
        path = settings.feedback.files_path / str(feedback_id) / f.stored_name
        if not path.is_file():
            raise DRSError("FEEDBACK_NOT_FOUND", admin_detail=f"file missing on disk: {path.name}")
        return path, f.media_type, f.file_name
