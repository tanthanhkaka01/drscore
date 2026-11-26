"""Góp ý pages (drscore.feedback): /feedback, /feedback/{id}. Plain forms, no script."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from drscore import feedback as service
from drscore.db import get_database
from drscore.db.models import FEEDBACK_KINDS, FEEDBACK_STATUSES, FEEDBACK_URGENCIES, User
from drscore.errors import DRSError
from drscore.settings import get_settings
from drscore.web.deps import current_user
from drscore.web.i18n import error_message, request_locale
from drscore.web.routes.pages import safe_next
from drscore.web.templating import render

router = APIRouter()


def _on() -> None:
    if not get_settings().app.feedback.enabled:
        raise DRSError("FEEDBACK_NOT_FOUND")


def _list_page(request: Request, user: User, *, status: str | None, mine: bool, error: str | None = None,
               form: dict | None = None, status_code: int = 200):
    settings = get_settings().app
    with get_database().session() as s:
        items = service.listing(s, user.username, status=status, mine=mine)
        rows = [{"id": f.feedback_id, "username": f.username, "kind": f.kind, "urgency": f.urgency,
                 "status": f.status, "message": f.message, "page_path": f.page_path, "created_at": f.created_at,
                 "comments": len(f.comments), "files": len(f.files)} for f in items]
    return render(request, "feedback_list.html", status_code=status_code, items=rows, status=status or "",
                  mine=mine, kinds=FEEDBACK_KINDS, urgencies=FEEDBACK_URGENCIES, statuses=FEEDBACK_STATUSES,
                  error=error, form=form or {"kind": "bug", "urgency": "normal", "message": "",
                                             "page_path": safe_next(request.query_params.get("from")) if
                                             request.query_params.get("from") else ""},
                  max_files=settings.feedback.max_files, max_file_mb=settings.feedback.max_file_mb,
                  is_admin=user.is_admin, tz=settings.tz)


@router.get("/feedback")
def feedback_list(request: Request, status: str | None = None, mine: int = 0, user: User = Depends(current_user)):
    _on()
    return _list_page(request, user, status=status, mine=bool(mine))


@router.post("/feedback")
async def feedback_create(request: Request, user: User = Depends(current_user)):
    _on()
    settings = get_settings().app
    form = await request.form()
    uploads = []
    for item in form.getlist("files"):
        if isinstance(item, UploadFile) and item.filename:
            data = await item.read(settings.feedback.max_file_mb * 1024 * 1024 + 1)
            uploads.append(service.Upload(item.filename, data))
    values = {"kind": str(form.get("kind") or "bug"), "urgency": str(form.get("urgency") or "normal"),
              "message": str(form.get("message") or ""), "page_path": str(form.get("page_path") or "")}
    try:
        feedback_id = await run_in_threadpool(
            service.create, get_database(), settings, user.username, kind=values["kind"], urgency=values["urgency"],
            message=values["message"], page_path=values["page_path"], uploads=uploads)
    except DRSError as exc:
        if exc.code != "FEEDBACK_INVALID":
            raise
        detail = "; ".join(f"{k}: {v}" for k, v in (exc.detail or {}).items())
        message = error_message(request_locale(request), exc.code) + (f" ({detail})" if detail else "")
        return _list_page(request, user, status=None, mine=False, error=message, form=values, status_code=422)
    return RedirectResponse(f"/feedback/{feedback_id}?msg=sent", status_code=303)


@router.get("/feedback/{feedback_id}")
def feedback_detail(feedback_id: int, request: Request, msg: str | None = None, user: User = Depends(current_user)):
    _on()
    settings = get_settings().app
    with get_database().session() as s:
        fb, is_admin = service.visible(s, user.username, feedback_id)
        item = {c.key: getattr(fb, c.key) for c in fb.__table__.columns}
        comments = [{"username": c.username, "kind": c.author_kind, "message": c.message, "created_at": c.created_at}
                    for c in fb.comments]
        files = [{"id": f.file_id, "name": f.file_name, "media_type": f.media_type, "size": f.size_bytes,
                  "inline": f.media_type in service.INLINE} for f in fb.files]
    if not is_admin:
        item["ai_note"] = None  # the agent's note is for the administrators
    return render(request, "feedback_detail.html", item=item, comments=comments, files=files, is_admin=is_admin,
                  is_sender=item["username"] == user.username, statuses=FEEDBACK_STATUSES, kinds=FEEDBACK_KINDS,
                  urgencies=FEEDBACK_URGENCIES, msg=msg, tz=settings.tz, ai_name=settings.ai.name)


@router.post("/feedback/{feedback_id}/comments")
async def feedback_comment(feedback_id: int, request: Request, user: User = Depends(current_user)):
    _on()
    form = await request.form()
    await run_in_threadpool(service.comment, get_database(), get_settings().app, user.username, feedback_id,
                            str(form.get("message") or ""))
    return RedirectResponse(f"/feedback/{feedback_id}?msg=commented", status_code=303)


@router.post("/feedback/{feedback_id}")
async def feedback_change(feedback_id: int, request: Request, user: User = Depends(current_user)):
    _on()
    form = await request.form()
    await run_in_threadpool(service.change, get_database(), user.username, feedback_id,
                            status=str(form.get("status") or "") or None, kind=str(form.get("kind") or "") or None,
                            urgency=str(form.get("urgency") or "") or None,
                            done_version=str(form["done_version"]) if "done_version" in form else None)
    return RedirectResponse(f"/feedback/{feedback_id}?msg=changed", status_code=303)


@router.post("/feedback/{feedback_id}/checked")
async def feedback_checked(feedback_id: int, request: Request, user: User = Depends(current_user)):
    _on()
    form = await request.form()
    await run_in_threadpool(service.set_checked, get_database(), user.username, feedback_id,
                            str(form.get("checked") or "1") == "1")
    return RedirectResponse(f"/feedback/{feedback_id}?msg=checked", status_code=303)


@router.get("/feedback/{feedback_id}/files/{file_id}")
def feedback_file(feedback_id: int, file_id: int, user: User = Depends(current_user)):
    _on()
    path, media, name = service.file_of(get_database(), get_settings().app, user.username, feedback_id, file_id)
    disposition = "inline" if media in service.INLINE else "attachment"
    return FileResponse(path, media_type=media, filename=name, content_disposition_type=disposition,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
