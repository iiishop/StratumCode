from __future__ import annotations

from uuid import uuid4

from .. import memory_system, sessions
from ..agent_runtime import start_event
from .memory_context import investigation_persistence_payload

SKILL_TARGET = False


def handle(run):
    from .. import chat

    memory_result = {}
    if run.session_id and run.last_investigation:
        payload = investigation_persistence_payload(run)
        sessions.merge_investigation(
            run.session_id,
            payload["task_items"],
            payload["observations"],
            investigation=payload["investigation"],
            knowledge=payload["knowledge"],
        )
        delta = memory_system.delta_from_events(
            workspace_dir=run.workspace_dir,
            session_id=run.session_id,
            turn_id=f"turn-{uuid4().hex[:12]}",
            events=[{
                "op": "done",
                "investigation": run.last_investigation,
                "validation_result": run.validation_result or {},
                "implementation": run.implementation_result or {},
            }],
            assistant_output="",
        )
        memory_result = memory_system.record_delta(run.workspace_dir, delta)
    if memory_result.get("records") or memory_result.get("refs"):
        yield start_event(f"memory-write-{uuid4().hex[:8]}", "memory_write", {
            "status": "accepted",
            "summary": (
                f"Recorded {len(memory_result.get('records', []))} memory record(s) "
                f"and {len(memory_result.get('refs', []))} reference(s)."
            ),
            "records": memory_result.get("records", [])[:8],
            "refs": memory_result.get("refs", [])[:8],
        })
    run.transition(chat.ChatState.COMPLETED, "Session investigation was saved.")
