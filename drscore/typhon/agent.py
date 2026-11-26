"""The AI agent: who may ask, the limits, the call to Claude and its tool loop, and the record of every question.

Off unless ``[ai] enabled = true`` and an API key is found (``secretstore``: the environment variable named by
``[ai] api_key_env``, else the encrypted secret ``ai.api_key``). Off, the portal shows nothing of it and its
routes answer "not found". The key is used here, on the server, only: no page ever receives it.

Every question - answered, refused by a limit, or failed - is a row of ``drs_ai_usage`` with who asked, from
which page, the answer, every tool run, the tokens and what they cost; conversations are kept in
``drs_ai_conversation``, appended to, never edited (so the prompt cache stays valid).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from drscore import notify, secretstore
from drscore.db.models import AiConversation, AiUsage, Feedback, FeedbackComment, Role, User, UserRole
from drscore.errors import DRSError
from drscore.settings import AppSettings
from drscore.typhon import prompt, tools

log = logging.getLogger(__name__)

# Models served with server-side refusal fallback ("default" form): a declined request is re-run on the model
# Anthropic recommends for that refusal category, inside the same call.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_QUESTION_CHARS = 4000
HISTORY_TURNS_KEPT = 40  # a conversation longer than this many messages starts afresh

# Tests replace this with a function returning an http client that answers without the network.
http_client_factory = None
# Tests set this to False: the feedback intake then runs in the caller's thread.
background = True


@dataclass
class Availability:
    on: bool
    why: str


def api_key(settings: AppSettings) -> str | None:
    return secretstore.get("ai.api_key", settings.ai.api_key_env)


def availability(settings: AppSettings) -> Availability:
    if not settings.ai.enabled:
        return Availability(False, "[ai] enabled = false")
    if not api_key(settings):
        return Availability(False, f"no API key: set {settings.ai.api_key_env} or `python -m drscore secret set ai.api_key`")
    return Availability(True, f"model {settings.ai.model}")


def may_use(session, user: User, settings: AppSettings) -> bool:
    ai = settings.ai
    if user.is_admin or not (ai.allowed_roles or ai.allowed_users):
        return True
    if user.username.lower() in {u.lower() for u in ai.allowed_users}:
        return True
    if not ai.allowed_roles:
        return False
    wanted = {r.upper() for r in ai.allowed_roles}
    codes = session.scalars(select(Role.role_code).join(UserRole, UserRole.role_id == Role.role_id)
                            .where(UserRole.user_id == user.user_id, Role.is_active.is_(True))).all()
    return bool(wanted & {c.upper() for c in codes})


def shown_to(session, user: User | None, settings: AppSettings) -> bool:
    """Is the agent on, and may this user ask it (the page shows it only then)."""
    return bool(user and availability(settings).on and may_use(session, user, settings))


def _month_start(settings: AppSettings) -> datetime:
    local = datetime.now(settings.tz)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _day_start(settings: AppSettings) -> datetime:
    local = datetime.now(settings.tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def spent_this_month(database=None, settings: AppSettings | None = None) -> float:
    from drscore.db import get_database
    from drscore.settings import get_settings

    database = database or get_database()
    settings = settings or get_settings().app
    with database.session() as s:
        total = s.scalar(select(func.coalesce(func.sum(AiUsage.cost_usd), 0))
                         .where(AiUsage.asked_at >= _month_start(settings)))
    return float(total or 0)


def cost_of(settings: AppSettings, model: str, usage: dict[str, int]) -> float:
    price_in, price_out = settings.ai.prices.get(model) or max(settings.ai.prices.values(), default=(4.0, 20.0))
    return (usage["input"] * price_in + usage["cache_write"] * price_in * 1.25 + usage["cache_read"] * price_in * 0.1
            + usage["output"] * price_out) / 1e6


def _client(settings: AppSettings, key: str):
    import anthropic

    kwargs: dict[str, Any] = {"api_key": key, "timeout": float(settings.ai.timeout_seconds), "max_retries": 2}
    if settings.ai.base_url:
        kwargs["base_url"] = settings.ai.base_url
    if http_client_factory is not None:
        kwargs["http_client"] = http_client_factory()
    return anthropic.Anthropic(**kwargs)


def _call(client, settings: AppSettings, system: str, tool_defs: list[dict], messages: list[dict]):
    params: dict[str, Any] = {
        "model": settings.ai.model, "max_tokens": settings.ai.max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "tools": tool_defs, "messages": messages, "output_config": {"effort": settings.ai.effort},
    }
    if settings.ai.model in FALLBACK_MODELS and not settings.ai.base_url:
        return client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default")
    return client.messages.create(**params)


def _block_dict(block) -> dict:
    return block.to_dict() if hasattr(block, "to_dict") else dict(block)


@dataclass
class Turn:
    answer: str
    status: str  # ok / refused / error
    tools_run: list[dict]
    usage: dict[str, int]
    model: str
    stop: str | None = None
    final_input: dict | None = None  # the input of answer_feedback, for the intake


def run_loop(database, settings: AppSettings, username: str, system: str, tool_defs: list[dict],
             messages: list[dict], *, final_tool: str | None = None) -> Turn:
    """Calls the model until it answers (or calls final_tool), running the tools it asks for. ``messages``
    is appended to in place, so the caller keeps the conversation exactly as the model saw it."""
    import anthropic

    key = api_key(settings)
    if not key:
        raise DRSError("AI_OFF")
    client = _client(settings, key)
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    tools_run: list[dict] = []
    model = settings.ai.model
    for _ in range(settings.ai.max_tool_rounds):
        try:
            response = _call(client, settings, system, tool_defs, messages)
        except anthropic.APIConnectionError as exc:
            return Turn("", "error", tools_run, usage, model, stop=f"connection: {exc.__class__.__name__}")
        except anthropic.RateLimitError:
            return Turn("", "error", tools_run, usage, model, stop="rate limited (429)")
        except anthropic.APIStatusError as exc:
            return Turn("", "error", tools_run, usage, model, stop=f"HTTP {exc.status_code}: {str(exc.message)[:300]}")
        u = response.usage
        usage["input"] += u.input_tokens or 0
        usage["output"] += u.output_tokens or 0
        usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        model = getattr(response, "model", None) or model
        content = [_block_dict(b) for b in response.content]
        messages.append({"role": "assistant", "content": content})
        if response.stop_reason == "refusal":
            return Turn("", "refused", tools_run, usage, model, stop="refusal")
        calls = [b for b in content if b.get("type") == "tool_use"]
        text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
        if response.stop_reason != "tool_use" or not calls:
            return Turn(text, "ok", tools_run, usage, model, stop=response.stop_reason)
        results = []
        final_input = None
        for call in calls:
            args = call.get("input") or {}
            if final_tool and call["name"] == final_tool:
                final_input = args
                result, is_error = "recorded", False
            else:
                result, is_error = tools.run_tool(database, settings, username, call["name"], args)
            tools_run.append({"name": call["name"], "input": args, "result_chars": len(result), "error": is_error})
            results.append({"type": "tool_result", "tool_use_id": call["id"], "content": result,
                            **({"is_error": True} if is_error else {})})
        messages.append({"role": "user", "content": results})
        if final_input is not None:
            return Turn(text, "ok", tools_run, usage, model, stop="final_tool", final_input=final_input)
    return Turn("", "error", tools_run, usage, model, stop="too many tool rounds")


def _record(database, settings: AppSettings, *, username: str, purpose: str, question: str, turn: Turn | None,
            status: str, error: str | None, started: float, conversation_id=None, feedback_id=None,
            page_path=None, answer=None) -> int:
    usage = turn.usage if turn else {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    cost = cost_of(settings, turn.model, usage) if turn else 0.0
    with database.session() as s:
        row = AiUsage(username=username[:100], purpose=purpose, conversation_id=conversation_id,
                      feedback_id=feedback_id, page_path=(page_path or None) and page_path[:400], question=question,
                      answer=answer, tools_json=turn.tools_run if turn and turn.tools_run else None,
                      model=turn.model[:60] if turn else None, input_tokens=usage["input"],
                      output_tokens=usage["output"], cache_read_tokens=usage["cache_read"],
                      cache_write_tokens=usage["cache_write"], cost_usd=Decimal(f"{cost:.6f}"), status=status,
                      error_message=(error or None) and error[:2000], duration_ms=int((time.perf_counter() - started) * 1000))
        s.add(row)
        s.flush()
        return row.usage_id


def _check_budget(database, settings: AppSettings, before: float) -> None:
    budget = settings.ai.monthly_budget_usd
    if budget <= 0:
        return
    now = spent_this_month(database, settings)
    if before < budget <= now:
        notify.notice(settings, "ai_budget", notify.ai_budget_text(settings, now))


def _refuse_if_limited(database, settings: AppSettings, username: str) -> float:
    """The month's spend so far; raises AI_LIMIT when a limit is reached."""
    spent = spent_this_month(database, settings)
    if settings.ai.monthly_budget_usd > 0 and spent >= settings.ai.monthly_budget_usd:
        raise DRSError("AI_LIMIT", admin_detail=f"monthly budget reached: {spent:.4f} USD")
    per_day = settings.ai.questions_per_user_per_day
    if per_day:
        with database.session() as s:
            asked = s.scalar(select(func.count(AiUsage.usage_id)).where(
                AiUsage.username == username, AiUsage.purpose == "ask", AiUsage.asked_at >= _day_start(settings)))
        if (asked or 0) >= per_day:
            raise DRSError("AI_LIMIT", admin_detail=f"{per_day} questions a day")
    return spent


def ask(database, settings: AppSettings, username: str, question: str, conversation_id: int | None = None,
        page_path: str | None = None) -> dict:
    """One question from a signed-in user. Returns {answer, conversation_id, usage_id}."""
    started = time.perf_counter()
    question = (question or "").strip()
    if not availability(settings).on:
        raise DRSError("AI_OFF")
    if not question or len(question) > MAX_QUESTION_CHARS:
        raise DRSError("PARAM_INVALID", detail={"message": f"1 to {MAX_QUESTION_CHARS} characters"})
    with database.session() as s:
        user = s.scalars(select(User).where(User.username == username)).one()
        if not may_use(s, user, settings):
            raise DRSError("AI_NOT_ALLOWED")
        history: list[dict] = []
        if conversation_id:
            conv = s.get(AiConversation, conversation_id)
            if conv is None or conv.username != username:
                raise DRSError("AI_CONVERSATION_NOT_FOUND")
            history = list(conv.messages_json or [])
            if len(history) > HISTORY_TURNS_KEPT:
                conversation_id, history = None, []
    try:
        before = _refuse_if_limited(database, settings, username)
    except DRSError as exc:
        _record(database, settings, username=username, purpose="ask", question=question, turn=None, status="refused",
                error=exc.admin_detail, started=started, conversation_id=conversation_id, page_path=page_path)
        raise
    where = f"[The user is {username}, on the page {page_path or '/'}.]\n" if page_path is not None else ""
    messages = history + [{"role": "user", "content": [{"type": "text", "text": where + question}]}]
    turn = run_loop(database, settings, username, prompt.system_prompt(settings), tools.tools_for(settings), messages)
    if turn.status == "ok" and not turn.answer:
        turn.status, turn.stop = "error", f"no text in the answer ({turn.stop})"
    with database.session() as s:
        if conversation_id:
            conv = s.get(AiConversation, conversation_id)
            conv.messages_json = messages
        else:
            conv = AiConversation(username=username, title=question[:200], messages_json=messages)
            s.add(conv)
        s.flush()
        conversation_id = conv.conversation_id
    usage_id = _record(database, settings, username=username, purpose="ask", question=question, turn=turn,
                       status=turn.status, error=None if turn.status == "ok" else turn.stop, started=started,
                       conversation_id=conversation_id, page_path=page_path, answer=turn.answer or None)
    _check_budget(database, settings, before)
    if turn.status == "error":
        log.warning("%s could not answer usage %s: %s", settings.ai.name, usage_id, turn.stop)
        raise DRSError("AI_UNAVAILABLE", admin_detail=turn.stop)
    return {"answer": turn.answer if turn.status == "ok" else None, "refused": turn.status == "refused",
            "conversation_id": conversation_id, "usage_id": usage_id}


def conversation_view(messages: list[dict]) -> list[dict]:
    """The turns of a conversation as the chat shows them: the user's questions and the agent's answers."""
    out = []
    for m in messages or []:
        texts = [b.get("text", "") for b in m.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(t for t in texts if t).strip()
        if not text:
            continue
        if m["role"] == "user" and text.startswith("[The user is "):
            text = text.split("]\n", 1)[-1]
        out.append({"role": m["role"], "text": text})
    return out


# --------------------------------------------------------------------------------------------
# A new feedback, read first by the agent

def intake(database, settings: AppSettings, feedback_id: int) -> None:
    """Typhon reads a new feedback: its reply goes in the thread, its note to the administrators. Then the
    administrators are told (Telegram / Zalo), with the note. Never raises; without the agent the notice goes
    out at once."""
    def run():
        from drscore import feedback as feedback_mod

        started = time.perf_counter()
        turn = None
        status, error = "error", None
        sender = "-"
        try:
            with database.session() as s:
                fb = s.get(Feedback, feedback_id)
                text = (f"Feedback #{fb.feedback_id} from {fb.username}\nkind: {fb.kind}, urgency: {fb.urgency}\n"
                        f"page: {fb.page_path or '?'}\nfiles attached: {len(fb.files)}\n\n{fb.message}")
                sender = fb.username
            before = spent_this_month(database, settings)
            if settings.ai.monthly_budget_usd > 0 and before >= settings.ai.monthly_budget_usd:
                raise DRSError("AI_LIMIT", admin_detail="monthly budget reached")
            messages = [{"role": "user", "content": [{"type": "text", "text": text}]}]
            turn = run_loop(database, settings, sender, prompt.intake_prompt(settings),
                            tools.tools_for(settings, intake=True), messages, final_tool="answer_feedback")
            status, error = turn.status, None if turn.status == "ok" else turn.stop
            final = turn.final_input or ({"reply": turn.answer, "note": ""} if turn.answer else None)
            if turn.status == "ok" and final:
                with database.session() as s:
                    fb = s.get(Feedback, feedback_id)
                    reply = str(final.get("reply") or "").strip()
                    if reply:
                        s.add(FeedbackComment(feedback_id=feedback_id, username=settings.ai.name[:100],
                                              author_kind="ai", message=reply[:8000]))
                    fb.ai_note = str(final.get("note") or "").strip()[:8000] or None
            elif turn.status == "ok":
                status, error = "error", "no answer_feedback call"
            _check_budget(database, settings, before)
        except DRSError as exc:
            status, error = ("refused" if exc.code == "AI_LIMIT" else "error"), exc.admin_detail or exc.code
        except Exception as exc:  # the feedback is kept whatever happens here
            log.exception("feedback intake %s failed", feedback_id)
            error = exc.__class__.__name__
        try:
            _record(database, settings, username=sender, purpose="feedback", question=f"feedback #{feedback_id}",
                    turn=turn, status=status, error=error, started=started, feedback_id=feedback_id,
                    answer=(turn.final_input or {}).get("reply") if turn and turn.final_input else None)
        except Exception:
            log.exception("could not record the feedback intake")
        feedback_mod.tell_new(database, settings, feedback_id)

    if background:
        threading.Thread(target=run, name=f"feedback-intake-{feedback_id}", daemon=True).start()
    else:
        run()
