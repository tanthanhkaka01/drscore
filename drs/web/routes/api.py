"""JSON API (spec section 16)."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from drs.authz.permissions import menu
from drs.db import get_database
from drs.db.models import User
from drs.settings import get_settings
from drs.web import views
from drs.web.deps import client_ip, current_session, current_user, db_session
from drs.web.i18n import request_locale
from drs.web.schemas import MeResponse, MenuResponse, ReportDefinition, RunRequest, RunResponse

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict:
    """No session needed, no secrets."""
    return {"status": "ok", "database": get_database().dialect}


@router.get("/api/me", response_model=MeResponse)
def me(request: Request, user: User = Depends(current_user), session: Session = Depends(db_session)):
    info = current_session(request, session)
    return MeResponse(username=user.username, display_name=user.display_name, is_admin=user.is_admin,
                      locale=request_locale(request), csrf_token=info.csrf_token)


@router.get("/api/menu", response_model=MenuResponse)
def api_menu(user: User = Depends(current_user), session: Session = Depends(db_session)):
    return MenuResponse(groups=menu(session, user))


@router.get("/api/reports/{code}", response_model=ReportDefinition)
def report_definition(code: str, user: User = Depends(current_user)):
    return views.definition(get_database(), get_settings().app, user.username, code)


@router.post("/api/reports/{code}/run", response_model=RunResponse, response_model_exclude_none=True)
def report_run(code: str, body: RunRequest, request: Request, user: User = Depends(current_user)):
    return views.run(get_database(), get_settings().app, user.username, code, body.params, body.refresh,
                     client_ip(request), view=body.view)


@router.get("/api/reports/{code}/grid", response_model=RunResponse, response_model_exclude_none=True)
def report_grid(code: str, request: Request, snapshot_id: int = Query(...), user: User = Depends(current_user)):
    """The grid of a snapshot already shown in another tab: no new run."""
    return views.grid_of_snapshot(get_database(), get_settings().app, user.username, code, snapshot_id,
                                  client_ip(request))


@router.post("/api/reports/{code}/bi-token")
def report_bi_token(code: str, request: Request, user: User = Depends(current_user)) -> dict:
    """Guest token for the embedded dashboard (asked by the Superset embedded SDK)."""
    return {"token": views.bi_token(get_database(), get_settings().app, user.username, code, client_ip(request))}


@router.get("/api/reports/{code}/export")
def report_export(code: str, request: Request, format: str = Query(...), snapshot_id: int = Query(...),
                  user: User = Depends(current_user)):
    file = views.export(get_database(), get_settings().app, user.username, code, format.lower(), snapshot_id,
                        client_ip(request))
    headers = {"Content-Disposition": f'attachment; filename="{file.filename}"', "Cache-Control": "no-store"}
    if file.path is not None:
        return FileResponse(file.path, media_type=file.media_type, headers=headers,
                            background=BackgroundTask(os.unlink, file.path))
    return StreamingResponse(file.chunks, media_type=file.media_type, headers=headers)
