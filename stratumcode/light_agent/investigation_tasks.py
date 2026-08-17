from __future__ import annotations

from copy import deepcopy

from ..agent_runtime import start_event
from ..status.task_updates import (
    _apply_task_updates,
    _finalize_task_statuses,
    _normalize_task_updates,
)


class LightInvestigationTaskReducer:
    def __init__(self, analysis: dict) -> None:
        self.analysis = analysis

    def events_for(self, event: dict) -> list[dict]:
        if event.get("op") == "start" and event.get("event") == "task_update":
            return [self._task_update_event(event)]
        if event.get("op") == "done" and isinstance(event.get("investigation"), dict):
            final = self._final_task_event(event["investigation"])
            return [final, event] if final else [event]
        return [event]

    def _task_update_event(self, event: dict) -> dict:
        analysis_id = self._analysis_id()
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        items = data.get("items")
        if not analysis_id or not isinstance(items, list):
            return event
        applied = _apply_task_updates(
            analysis_id,
            self.analysis.get("task_updates", []),
            items,
            [],
        )
        self.analysis["task_updates"] = applied["items"]
        next_event = deepcopy(event)
        next_event["data"]["items"] = applied["items"]
        if applied["changes"]:
            next_event["data"]["changes"] = applied["changes"]
        else:
            next_event["data"].pop("changes", None)
        return next_event

    def _final_task_event(self, investigation: dict) -> dict | None:
        analysis_id = self._analysis_id()
        if not analysis_id:
            return None
        task_updates = _normalize_task_updates(
            analysis_id,
            self.analysis.get("task_updates", []) + investigation.get("task_updates", []),
            [],
        )
        task_updates = _finalize_task_statuses(task_updates, investigation)
        applied = _apply_task_updates(
            analysis_id,
            self.analysis.get("task_updates", []),
            task_updates,
            [],
        )
        self.analysis["task_updates"] = applied["items"]
        investigation["task_updates"] = applied["items"]
        data = {
            "analysis_id": analysis_id,
            "items": applied["items"],
        }
        if applied["changes"]:
            data["changes"] = applied["changes"]
        return start_event(f"{analysis_id}-light-task-final", "task_update", data)

    def _analysis_id(self) -> str:
        return str(self.analysis.get("id") or "").strip()
