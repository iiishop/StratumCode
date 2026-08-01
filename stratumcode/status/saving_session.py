from __future__ import annotations

from .. import sessions
from .memory_context import investigation_persistence_payload

SKILL_TARGET = False


def handle(run):
    from .. import chat

    if run.session_id and run.last_investigation:
        payload = investigation_persistence_payload(run)
        sessions.merge_investigation(
            run.session_id,
            payload["task_items"],
            payload["observations"],
            investigation=payload["investigation"],
            knowledge=payload["knowledge"],
        )
    run.transition(chat.ChatState.COMPLETED, "Session investigation was saved.")
