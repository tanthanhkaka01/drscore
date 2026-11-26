"""The AI agent's page and API (drscore.typhon). Every route answers "not found" while the agent is off."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from drscore.db import get_database
from drscore.db.models import AiConversation, User
from drscore.errors import DRSError
from drscore.settings import get_settings
from drscore.typhon import agent
from drscore.web.i18n import request_locale
from drscore.web.deps import current_user
from drscore.web.routes.pages import safe_next
from drscore.web.templating import client_messages, render

router = APIRouter()


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=agent.MAX_QUESTION_CHARS)
    conversation_id: int | None = None
    page_path: str | None = Field(default=None, max_length=400)


def _allowed(user: User) -> None:
    settings = get_settings().app
    if not agent.availability(settings).on:
        raise DRSError("AI_OFF")
    with get_database().session() as s:
        if not agent.may_use(s, user, settings):
            raise DRSError("AI_NOT_ALLOWED")


@router.get("/ai")
def ai_page(request: Request, user: User = Depends(current_user)):
    _allowed(user)
    settings = get_settings().app
    came_from = request.query_params.get("from")
    page_data = {"name": settings.ai.name, "from": safe_next(came_from) if came_from else "/",
                 "messages": client_messages(request_locale(request), ("ai.",))}
    return render(request, "ai.html", page_data=page_data, ai_name=settings.ai.name,
                  reads_data=settings.ai.read_report_data)


@router.post("/api/ai/ask")
async def ai_ask(body: AskRequest, user: User = Depends(current_user)) -> dict:
    _allowed(user)
    page = safe_next(body.page_path) if body.page_path else None
    return await run_in_threadpool(agent.ask, get_database(), get_settings().app, user.username, body.question,
                                   body.conversation_id, page)


@router.get("/api/ai/conversations")
def ai_conversations(user: User = Depends(current_user)) -> dict:
    _allowed(user)
    with get_database().session() as s:
        rows = s.scalars(select(AiConversation).where(AiConversation.username == user.username)
                         .order_by(AiConversation.updated_at.desc()).limit(50)).all()
        return {"conversations": [{"id": c.conversation_id, "title": c.title, "updated_at": c.updated_at.isoformat()}
                                  for c in rows]}


@router.get("/api/ai/conversations/{conversation_id}")
def ai_conversation(conversation_id: int, user: User = Depends(current_user)) -> dict:
    _allowed(user)
    with get_database().session() as s:
        conv = s.get(AiConversation, conversation_id)
        if conv is None or (conv.username != user.username and not user.is_admin):
            raise DRSError("AI_CONVERSATION_NOT_FOUND")
        return {"id": conv.conversation_id, "title": conv.title, "turns": agent.conversation_view(conv.messages_json)}
