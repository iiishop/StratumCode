from __future__ import annotations

from .investigation_contracts import MemorySnapshot, MemoryWriteSet
from .session_memory import _session_context_lines
from .task_contract import run_request
from .task_updates import _beliefs_as_knowledge, _merge_items_by_id


class LegacyRunMemoryPort:
    """Compatibility adapter until a shared memory system is introduced."""

    def current_snapshot(self, run, stage: str) -> MemorySnapshot:
        if stage != "investigation":
            return MemorySnapshot()
        previous_observations = _merge_items_by_id(
            _merge_items_by_id(
                run.selected_session_context.get("observations", []),
                run.investigation_observations,
            ),
            run.investigation_grounding_observations,
        )
        previous_knowledge = _merge_items_by_id(
            run.selected_session_context.get("knowledge", []),
            run.investigation_knowledge,
        )
        return MemorySnapshot(
            selected_context=run.selected_session_context,
            context_lines=_session_context_lines(run.selected_session_context),
            previous_observations=previous_observations,
            previous_knowledge=previous_knowledge,
            previous_findings=run.last_investigation,
        )

    def record_stage_delta(self, run, stage: str, delta: MemoryWriteSet) -> None:
        if stage != "investigation":
            return
        run.investigation_grounding_observations = _merge_items_by_id(
            run.investigation_grounding_observations,
            delta.grounding_observations,
        )
        run.investigation_observations = _merge_items_by_id(
            run.investigation_observations,
            delta.observations,
        )
        run.investigation_knowledge = _merge_items_by_id(
            run.investigation_knowledge,
            delta.knowledge,
        )


def investigation_persistence_payload(run) -> dict:
    """Build the legacy sessions.merge_investigation payload."""
    investigation = run.last_investigation or {}
    analysis_id = (run.analysis or {}).get("id", "")
    return {
        "task_items": investigation.get("task_updates", []),
        "observations": _merge_items_by_id(
            run.investigation_observations,
            investigation.get("observations", []),
        ),
        "investigation": {
            "id": analysis_id,
            "request": run_request(run),
            "summary": investigation.get("summary", ""),
        },
        "knowledge": _merge_items_by_id(
            run.investigation_knowledge,
            _beliefs_as_knowledge(analysis_id, investigation.get("beliefs", [])),
        ),
    }
