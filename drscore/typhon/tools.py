"""The tools the agent may call. Each runs as the user who asked, through the same functions as the portal's
pages, so every right and row filter applies; none changes anything."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from drscore.errors import DRSError
from drscore.settings import AppSettings

MAX_RESULT_CHARS = 60_000


def _strict(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": properties, "required": required,
                             "additionalProperties": False}}


LIST_REPORTS = _strict("list_reports", "The report groups and reports this user may open, with their codes.", {}, [])
DESCRIBE_REPORT = _strict(
    "describe_report", "One report the user may open: its parameters (name, label, type, required, default, "
    "options) and its views (grid, html, bi).", {"code": {"type": "string", "description": "The report code"}},
    ["code"])
RUN_REPORT = _strict(
    "run_report", "Runs a report for the user, with the user's rights and row filters, and returns its columns "
    "and first rows. Parameters left out take their default.",
    {"code": {"type": "string"},
     "params": {"type": "array", "description": "Parameter values",
                "items": {"type": "object", "additionalProperties": False, "required": ["name", "values"],
                          "properties": {"name": {"type": "string"},
                                         "values": {"type": "array", "items": {"type": "string"},
                                                    "description": "One value, or several for a multi-select"}}}}},
    ["code", "params"])
MY_FEEDBACK = _strict("my_feedback", "The feedback this user sent most recently, with its status.", {}, [])
ANSWER_FEEDBACK = _strict(
    "answer_feedback", "Gives your reply to the user and your note for the administrators about the feedback.",
    {"reply": {"type": "string"}, "note": {"type": "string"}}, ["reply", "note"])


def tools_for(settings: AppSettings, intake: bool = False) -> list[dict]:
    tools = [LIST_REPORTS, DESCRIBE_REPORT]
    if settings.ai.read_report_data:
        tools.append(RUN_REPORT)
    tools.append(ANSWER_FEEDBACK if intake else MY_FEEDBACK)
    return tools


def _clip(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + " …(cut)"


def run_tool(database, settings: AppSettings, username: str, name: str, args: dict) -> tuple[str, bool]:
    """(result text, is_error)."""
    from drscore.authz.permissions import menu
    from drscore.db.models import Feedback, User
    from drscore.web import views

    try:
        if name == "list_reports":
            with database.session() as s:
                user = s.scalars(select(User).where(User.username == username)).one()
                return _clip(menu(s, user)), False
        if name == "describe_report":
            d = views.definition(database, settings, username, str(args.get("code", "")))
            data = d.model_dump(mode="json")
            return _clip({"report": data.get("report"), "params": data.get("params"), "views": data.get("views"),
                          "default_view": data.get("default_view"), "can_export": data.get("can_export")}), False
        if name == "run_report" and settings.ai.read_report_data:
            params: dict[str, Any] = {}
            for p in args.get("params") or []:
                values = [str(v) for v in p.get("values") or []]
                if p.get("name"):
                    params[str(p["name"])] = values[0] if len(values) == 1 else values
            from drscore.reports.service import run_row_cap

            cap = settings.ai.max_report_rows
            token = run_row_cap.set(cap)
            try:
                answer = views.run(database, settings, username, str(args.get("code", "")), params, False, None,
                                   view="grid")
            except DRSError as exc:
                if exc.code == "RESULT_TOO_LARGE":
                    return (f"RESULT_TOO_LARGE: this report gives more than {cap} rows for these parameters. "
                            f"Run it for a narrower choice (a shorter date range, one company, one employee...), "
                            f"or tell the user to open the report and export it."), True
                raise
            finally:
                run_row_cap.reset(token)
            limit = settings.ai.max_rows_to_model
            rows = (answer.rows or [])[:limit]
            return _clip({"columns": [c.model_dump(mode="json", include={"name", "label", "type"})
                                      for c in answer.columns or []],
                          "rows": rows, "row_count": answer.row_count, "rows_shown": len(rows),
                          "params_used": answer.params}), False
        if name == "my_feedback":
            with database.session() as s:
                rows = s.scalars(select(Feedback).where(Feedback.username == username)
                                 .order_by(Feedback.feedback_id.desc()).limit(20)).all()
                return _clip([{"id": f.feedback_id, "status": f.status, "kind": f.kind,
                               "sent": f.created_at.isoformat(timespec="minutes"), "message": f.message[:300]}
                              for f in rows]), False
        return f"unknown tool {name}", True
    except DRSError as exc:
        return f"{exc.code}: {exc.message}", True
    except Exception as exc:  # reported to the model, which tells the user; the server log has the rest
        return f"the tool failed: {exc.__class__.__name__}", True
