from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from ..agent_runtime import start_event
from ..status import task_analysis
from ..status.task_updates import _normalize_task_updates, _seed_task_updates


def light_task_analysis(message: str, context: list[str]) -> dict:
    analysis = task_analysis._minimal_task_analysis(message, context)
    analysis.setdefault("id", f"light-task-{uuid4().hex[:8]}")
    analysis.setdefault("origin_message", message)
    analysis.setdefault("source", "light_agent")
    analysis["task_updates"] = _normalize_task_updates(
        analysis["id"],
        _seed_task_updates(analysis),
        [],
    )
    return analysis


def light_task_events(analysis: dict) -> list[dict]:
    analysis_id = str(analysis["id"])
    return [
        start_event(f"{analysis_id}-analysis", "task_analysis", deepcopy(analysis)),
        start_event(f"{analysis_id}-task-seed", "task_update", {
            "analysis_id": analysis_id,
            "items": deepcopy(analysis.get("task_updates", [])),
        }),
    ]
