from __future__ import annotations

from copy import deepcopy

from .. import investigator
from ..agent_runtime import start_event
from .investigation_contracts import (
    InvestigationRunInput,
    InvestigationStreamResult,
    MemoryWriteSet,
)
from .task_updates import (
    _apply_task_updates,
    _beliefs_as_knowledge,
    _finalize_task_statuses,
    _normalize_task_updates,
    _scoped_items,
)


class InvestigationEventConsumer:
    def consume(self, run, input_data: InvestigationRunInput):
        result = InvestigationStreamResult()
        for event in investigator.investigation_stream(
            message=input_data.request,
            analysis=input_data.analysis,
            context=input_data.context,
            workspace_dir=run.workspace_dir,
            max_rounds=run.max_rounds,
            findings=run.findings,
            previous_observations=input_data.previous_observations,
            previous_knowledge=input_data.previous_knowledge,
            previous_findings=input_data.previous_findings,
            preserve_grounding_evidence=True,
        ):
            if event.get("event") == "task_update":
                self._apply_task_update_event(run, event)
            if event.get("op") == "start" and event.get("event") == "user_question" and event.get("data", {}).get("clearify_tool"):
                yield event
                continue
            if event.get("op") == "start" and event.get("event") == "user_question":
                result.pending_question = event
                continue
            if (
                event.get("op") == "start"
                and event.get("event") == "output"
                and str(event.get("id") or "").endswith("-output")
            ):
                result.pending_output = event
                continue
            if event.get("op") == "done" and isinstance(event.get("investigation"), dict):
                result.investigation = event["investigation"]
                result.memory_delta = self._finalize_investigation(run, result.investigation)
                yield self._task_final_event(run, result.memory_delta)
            yield event
        return result

    def _apply_task_update_event(self, run, event: dict) -> None:
        applied = _apply_task_updates(
            run.analysis["id"],
            run.analysis.get("task_updates", []),
            event["data"].get("items", []),
            [],
        )
        event["data"]["items"] = applied["items"]
        if applied["changes"]:
            event["data"]["changes"] = applied["changes"]
        else:
            event["data"].pop("changes", None)
        run.analysis["task_updates"] = applied["items"]

    def _finalize_investigation(self, run, investigation: dict) -> MemoryWriteSet:
        investigation["task_updates"] = _normalize_task_updates(
            run.analysis["id"],
            run.analysis.get("task_updates", []) + investigation.get("task_updates", []),
            run.session_context.get("tasks", []),
        )
        investigation["task_updates"] = _finalize_task_statuses(
            investigation["task_updates"],
            investigation,
        )
        applied = _apply_task_updates(
            run.analysis["id"],
            run.analysis.get("task_updates", []),
            investigation["task_updates"],
            run.session_context.get("tasks", []),
        )
        investigation["task_updates"] = applied["items"]
        scoped_observations = _scoped_items(
            run.analysis["id"],
            investigation.get("observations", []),
        )
        grounding_observations = [
            {**item, "fresh": item.get("fresh", True)}
            for item in scoped_observations
        ]
        new_observations = [
            {
                **{
                    key: value
                    for key, value in item.items()
                    if key != "_grounding_evidence"
                },
                "fresh": item.get("fresh", True),
            }
            for item in scoped_observations
        ]
        investigation["observations"] = new_observations
        run.analysis["task_updates"] = investigation["task_updates"]
        return MemoryWriteSet(
            observations=new_observations,
            grounding_observations=grounding_observations,
            knowledge=_beliefs_as_knowledge(run.analysis["id"], investigation.get("beliefs", [])),
            task_updates=investigation["task_updates"],
            task_update_changes=applied["changes"],
            investigation_summary={
                "id": run.analysis["id"],
                "summary": investigation.get("summary", ""),
            },
        )

    def _task_final_event(self, run, delta: MemoryWriteSet) -> dict:
        data = {
            "analysis_id": run.analysis["id"],
            "items": delta.task_updates,
        }
        if delta.task_update_changes:
            data["changes"] = delta.task_update_changes
        return start_event(f"{run.analysis['id']}-task-final", "task_update", deepcopy(data))
