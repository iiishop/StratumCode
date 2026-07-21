from __future__ import annotations

from uuid import uuid4

from ..agent_runtime import start_event
from .investigating import prepare_investigation
from . import task_analysis


def handle(run):
    from .. import chat

    analyze_stage = f"task-analysis-{uuid4().hex[:8]}"
    yield start_event(analyze_stage, "stage", {
        "name": "task_analysis",
        "label": "Analyze task requirements",
        "state": "running",
        "phase": "analyzing",
        "model": "",
        "context_length": 0,
        "provider": "",
        "inherited": False,
    })
    run.analysis = task_analysis.analyze_task(
        run.message,
        run.context,
        run.workspace_dir,
        session_context=run.analyzer_session_context,
        call_model=task_analysis._runtime_call_model,
        content_text=task_analysis._runtime_content_text,
        resolve_model=task_analysis.model_settings.resolve,
    )
    run.analysis.setdefault("model", "")
    run.analysis.setdefault("provider", "")
    yield {"op": "update", "id": analyze_stage, "patch": {
        "model": run.analysis.get("model", ""),
        "provider": run.analysis.get("provider", ""),
        "state": "done",
        "phase": "analyzed",
    }}
    yield from prepare_investigation(run)
    run.transition(chat.ChatState.INVESTIGATING, "Task analysis completed.")
