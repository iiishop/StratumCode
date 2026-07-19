from __future__ import annotations

from uuid import uuid4

from ..agent_runtime import start_event
from .session_memory import _attach_session_relationship, _select_session_memory
from .task_contract import _ensure_task_contract, run_request
from .task_updates import _normalize_task_updates, _seed_task_updates
from .user_context import _answered_task_update, _continuation_context, _prior_findings


def handle(run):
    from .. import chat

    run.analysis = _ensure_task_contract(run.analysis or {})
    run.analysis.setdefault("id", f"task-{uuid4().hex[:8]}")
    run.analysis.setdefault("origin_message", run.message)
    request = run_request(run)
    run.selected_session_context = _select_session_memory(request, run.analysis, run.session_context)
    _attach_session_relationship(run.analysis, run.session_context.get("tasks", []))
    seeded_tasks = _seed_task_updates(run.analysis, run.session_context.get("tasks", []))
    run.answered_task = _answered_task_update(run.analysis["id"], run.answer)
    run.analysis["task_updates"] = _normalize_task_updates(
        run.analysis["id"],
        run.analysis.get("task_updates", []) + seeded_tasks + ([run.answered_task] if run.answered_task else []),
        run.session_context.get("tasks", []),
    )
    if not run.answer:
        yield start_event(f"task-analysis-{uuid4().hex[:8]}", "task_analysis", run.analysis)
    run.findings = _prior_findings(run.prior_analysis, run.answer)
    run.continuation_context = _continuation_context(run.answer, run.selected_session_context)
    run.transition(chat.ChatState.INVESTIGATING, "Task contract and continuation context are ready.")
