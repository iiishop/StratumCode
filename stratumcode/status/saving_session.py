from __future__ import annotations

from .. import sessions
from .task_contract import run_request
from .task_updates import _beliefs_as_knowledge, _merge_items_by_id


def handle(run):
    from .. import chat

    if run.session_id and run.last_investigation:
        sessions.merge_investigation(
            run.session_id,
            run.last_investigation.get("task_updates", []),
            _merge_items_by_id(run.investigation_observations, run.last_investigation.get("observations", [])),
            investigation={
                "id": run.analysis["id"],
                "request": run_request(run),
                "summary": run.last_investigation.get("summary", ""),
            },
            knowledge=_merge_items_by_id(
                run.investigation_knowledge,
                _beliefs_as_knowledge(run.analysis["id"], run.last_investigation.get("beliefs", [])),
            ),
        )
    run.transition(chat.ChatState.COMPLETED, "Session investigation was saved.")
