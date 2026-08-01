from __future__ import annotations

from uuid import uuid4

from ..agent_runtime import start_event


def user_question_event(
    run,
    *,
    question: str,
    reason: str = "",
    unknown_id: str = "",
    checkpoint_phase: str,
    resume_state: str,
    extra: dict | None = None,
) -> dict:
    data = {
        "id": str(unknown_id or "").strip() or f"question-{uuid4().hex[:8]}",
        "analysis_id": (run.analysis or {}).get("id", ""),
        "question": question,
        "origin_message": run.message,
        "reason": reason,
        "checkpoint_phase": checkpoint_phase,
        "resume_state": resume_state,
    }
    data.update(extra or {})
    return start_event(data["id"], "user_question", data)


def prepared_user_question_event(
    event: dict,
    *,
    checkpoint_phase: str,
    resume_state: str,
    extra: dict | None = None,
) -> dict:
    prepared = dict(event)
    data = dict(prepared.get("data") or {})
    data.setdefault("checkpoint_phase", checkpoint_phase)
    data.setdefault("resume_state", resume_state)
    data.update(extra or {})
    prepared["data"] = data
    return prepared
