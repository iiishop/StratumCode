from __future__ import annotations

from uuid import uuid4

from .. import checkpoint
from ..agent_runtime import start_event


DEFAULT_OPTIONS = [
    {
        "id": "best_judgment",
        "label": "Use best judgment",
        "value": "Use the model's best engineering judgment.",
        "recommended": True,
    },
    {
        "id": "continue_investigation",
        "label": "Continue investigation",
        "value": "Look for more project evidence before deciding.",
    },
    {
        "id": "minimal_change",
        "label": "Minimal change",
        "value": "Choose the smallest change that satisfies the request.",
    },
]


def ask(run, question: dict, *, resume_state, event_id: str = "", **context) -> dict:
    checkpoint.prepare_question(question, resume_state=resume_state, **context)
    _normalize_question(question)
    run.awaiting_user = True
    return start_event(event_id or f"clearify-{uuid4().hex[:8]}", "user_question", question)


def ask_event(run, event: dict, *, resume_state, **context) -> dict:
    checkpoint.prepare_question(event["data"], resume_state=resume_state, **context)
    _normalize_question(event["data"])
    run.awaiting_user = True
    return event


def _normalize_question(question: dict) -> None:
    question["options"] = _three_options(question.get("options"))
    question["custom_allowed"] = True


def _three_options(raw_options) -> list[dict]:
    options = []
    for index, raw in enumerate(raw_options if isinstance(raw_options, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or raw.get("value") or f"Option {index}").strip()
        value = str(raw.get("value") or raw.get("description") or label).strip()
        if not label and not value:
            continue
        option = dict(raw)
        option["id"] = str(option.get("id") or f"option_{index}").strip()
        option["label"] = label or value
        option["value"] = value or label
        options.append(option)
        if len(options) == 3:
            break
    used = {option["id"] for option in options}
    for option in DEFAULT_OPTIONS:
        if len(options) == 3:
            break
        if option["id"] not in used:
            options.append(dict(option))
    return options
