"""Notices to a Telegram group and a Zalo group (docs/feedback-typhon-notices.md).

A channel sends only when both are set: its group (``[notify.telegram] chat_id``) and its bot token
(the environment variable ``bot_token_env``, else the encrypted secret ``telegram.bot_token``).
Until then nothing is sent and nothing fails. What a notice is about is one of
``settings.NOTICE_EVENTS``; ``[notify] events`` chooses which are sent.

A notice never holds up or breaks the request that caused it: it is sent from a background thread,
after the request's own work, and every attempt - sent or not - is a row of ``drs_notice_log``.
The bot token is never written there nor in the application log.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

import httpx

from drscore import secretstore
from drscore.settings import AppSettings

log = logging.getLogger(__name__)

# The longest text each service takes in one message.
TEXT_LIMIT = {"telegram": 4000, "zalo": 2000}

# Tests replace these: a transport that answers without the network, and sending in the caller's thread.
transport: httpx.BaseTransport | None = None
background = True


@dataclass
class Channel:
    name: str
    chat_id: str
    api_url: str
    ready: bool
    why: str
    token: str | None = field(default=None, repr=False)


def channels(settings: AppSettings, only_ready: bool = True) -> list[Channel]:
    out = []
    for name, section, secret in (("telegram", settings.notify.telegram, "telegram.bot_token"),
                                  ("zalo", settings.notify.zalo, "zalo.bot_token")):
        chat = section.chat_id.strip()
        token = secretstore.get(secret, section.bot_token_env) if chat else None
        if not chat:
            why = f"no group: [notify.{name}] chat_id is empty"
        elif not token:
            why = f"no bot token: set {section.bot_token_env} or `python -m drscore secret set {secret}`"
        else:
            why = f"group {chat}"
        channel = Channel(name, chat, section.api_url.rstrip("/"), bool(chat and token), why, token)
        if channel.ready or not only_ready:
            out.append(channel)
    return out


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def send_now(settings: AppSettings, channel: Channel, event: str, text: str) -> tuple[bool, str | None]:
    """Sends one message to one channel, in this thread; records it. (sent, error)."""
    body = _clip(settings.notify.prefix + text, TEXT_LIMIT[channel.name])
    payload: dict = {"chat_id": channel.chat_id, "text": body}
    if channel.name == "telegram":
        payload["disable_web_page_preview"] = True
    ok, error = False, None
    try:
        with httpx.Client(timeout=settings.notify.timeout_seconds, transport=transport) as client:
            answer = client.post(f"{channel.api_url}/bot{channel.token}/sendMessage", json=payload)
        try:
            data = answer.json()
        except ValueError:
            data = {}
        ok = bool(isinstance(data, dict) and data.get("ok"))
        if not ok:
            error = f"HTTP {answer.status_code} {data.get('error_code', '') if isinstance(data, dict) else ''} " \
                    f"{data.get('description', '') if isinstance(data, dict) else ''}".strip()
    except Exception as exc:  # network, timeout: recorded, never raised
        error = f"{exc.__class__.__name__}: {exc}"
    if error and channel.token:
        error = error.replace(channel.token, "<token>")
    _record(channel, event, body, ok, error)
    if not ok:
        log.warning("notice %s to %s not sent: %s", event, channel.name, error)
    return ok, error


def _record(channel: Channel, event: str, text: str, ok: bool, error: str | None) -> None:
    from drscore.db import get_database
    from drscore.db.models import NoticeLog

    try:
        with get_database().session() as s:
            s.add(NoticeLog(channel=channel.name, chat_id=channel.chat_id[:100], event=event[:40], text_sent=text,
                            is_ok=ok, error_message=(error or None) and error[:1000]))
    except Exception:
        log.exception("could not write the notice log")


def notice(settings: AppSettings, event: str, text: str) -> int:
    """Sends a notice of this event to every ready channel, in the background. How many channels it goes to."""
    if event not in settings.notify.events:
        return 0
    ready = channels(settings)
    if not ready:
        return 0

    def run():
        for channel in ready:
            send_now(settings, channel, event, text)

    if background:
        threading.Thread(target=run, name=f"notice-{event}", daemon=True).start()
    else:
        run()
    return len(ready)


# --------------------------------------------------------------------------------------------
# What each notice says (pure: tested without sending)

KIND = {"bug": "Báo lỗi / Bug", "idea": "Góp ý / Idea", "question": "Câu hỏi / Question"}
URGENCY = {"high": "Gấp / Urgent", "normal": "Bình thường / Normal", "low": "Nhỏ / Minor"}


def _flat(text: str | None, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return _clip(flat, limit)


def feedback_text(settings: AppSettings, feedback, event: str, *, by: str | None = None, message: str | None = None,
                  user_name: str | None = None) -> str:
    head = {
        "feedback_new": f"📝 Góp ý #{feedback.feedback_id} mới",
        "feedback_comment": f"💬 Góp ý #{feedback.feedback_id}: người gửi viết thêm",
        "feedback_reopened": f"🔁 Góp ý #{feedback.feedback_id} được mở lại",
    }[event]
    who = f"{user_name} ({feedback.username})" if user_name and user_name != feedback.username else feedback.username
    lines = [f"{settings.app.name} · {head}",
             f"{who} · {KIND.get(feedback.kind, feedback.kind)} · {URGENCY.get(feedback.urgency, feedback.urgency)}"
             + (f" · {feedback.page_path}" if feedback.page_path else "")
             + (f" · {feedback.app_version}" if feedback.app_version else ""),
             ""]
    if event == "feedback_new":
        lines.append(_flat(feedback.message, 900))
        if feedback.ai_note:
            lines += ["", f"{settings.ai.name}: {_flat(feedback.ai_note, 900)}"]
    else:
        lines.append(f"{by or feedback.username}: {_flat(message, 900)}")
    lines += ["", f"{settings.app.base_url}/feedback/{feedback.feedback_id}"]
    return "\n".join(lines)


def design_submitted_text(settings: AppSettings, draft_id: int, report_code: str | None, report_name: str | None,
                          author: str) -> str:
    what = f"{report_code} - {report_name}" if report_code and report_name else (report_code or report_name or "?")
    return "\n".join([f"{settings.app.name} · 🧩 Báo cáo chờ duyệt / Report waiting for approval",
                      f"{what} · {author}", "", f"{settings.app.base_url}/design/drafts/{draft_id}"])


def ai_budget_text(settings: AppSettings, spent: float) -> str:
    return (f"{settings.app.name} · ⚠️ {settings.ai.name}: hết ngân sách tháng / monthly budget reached "
            f"({spent:.2f} / {settings.ai.monthly_budget_usd:.2f} USD). {settings.ai.name} trả lời lại từ tháng sau "
            f"hoặc khi [ai] monthly_budget_usd được tăng.")
