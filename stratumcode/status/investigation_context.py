from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from ..agent_runtime import start_event
from .investigation_contracts import InvestigationRunInput, MemorySnapshot
from .session_memory import _attach_session_relationship, _select_session_memory
from .task_contract import _ensure_task_contract, run_request
from .task_updates import _normalize_task_updates, _seed_task_updates


class InvestigationContextBuilder:
    def prepare(self, run):
        run.analysis = _ensure_task_contract(run.analysis or {})
        run.analysis.setdefault("id", f"task-{uuid4().hex[:8]}")
        run.analysis.setdefault("origin_message", run.message)
        request = run_request(run)
        run.selected_session_context = _select_session_memory(
            request,
            run.analysis,
            run.session_context,
        )
        _attach_session_relationship(run.analysis, run.session_context.get("tasks", []))
        seeded_tasks = _seed_task_updates(run.analysis, run.session_context.get("tasks", []))
        run.analysis["task_updates"] = _normalize_task_updates(
            run.analysis["id"],
            run.analysis.get("task_updates", []) + seeded_tasks,
            run.session_context.get("tasks", []),
        )
        yield start_event(f"task-analysis-{uuid4().hex[:8]}", "task_analysis", deepcopy(run.analysis))

    def build(self, run, memory: MemorySnapshot) -> InvestigationRunInput:
        request = run_request(run)
        investigation_analysis = {
            **run.analysis,
            "unknowns": _open_analysis_unknowns(run.analysis, run.last_investigation),
        }
        unresolved = (run.last_investigation or {}).get("unknowns")
        if isinstance(unresolved, list) and unresolved:
            investigation_analysis = {
                **investigation_analysis,
                "unknowns": _merge_items_by_id(
                    investigation_analysis.get("unknowns", []),
                    unresolved,
                ),
            }
        return InvestigationRunInput(
            request=request,
            analysis=investigation_analysis,
            context=(
                run.context
                + memory.context_lines
                + _analysis_context(investigation_analysis)
                + run.continuation_context
            ),
            previous_observations=memory.previous_observations,
            previous_knowledge=memory.previous_knowledge,
            previous_findings=memory.previous_findings,
        )


def _analysis_context(analysis: dict) -> list[str]:
    """Render normalized task analysis into investigation context lines."""
    analysis = _ensure_task_contract(analysis)
    lines = [f"Task intent ({analysis['intent']['type']}): {analysis['intent']['summary']}"]
    lines.extend(
        f"Acceptance criterion {item['id']}: {item['text']}"
        for item in analysis.get("acceptance_criteria", [])
    )
    behavior = analysis.get("behavior_contract", {})
    for key, label in (
        ("inputs", "Behavior input"),
        ("outputs", "Behavior output"),
        ("success_behaviors", "Success behavior"),
        ("failure_behaviors", "Failure behavior"),
        ("boundaries", "Boundary"),
    ):
        lines.extend(f"{label}: {item}" for item in behavior.get(key, []))
    lines.extend(f"Constraint: {item}" for item in analysis["constraints"])
    scope = analysis.get("scope", {})
    lines.extend(f"In scope: {item}" for item in scope.get("in", []))
    lines.extend(f"Out of scope: {item}" for item in scope.get("out", []))
    lines.extend(f"Undecided scope: {item}" for item in scope.get("undecided", []))
    lines.extend(
        f"Assumption to verify ({item['certainty']}): {item['text']}"
        for item in analysis["hypotheses"]
    )
    for clue in analysis["clues"]:
        parts = [clue["kind"], clue["value"]]
        if clue.get("path"):
            parts.append(f"path={clue['path']}")
        if clue.get("line"):
            parts.append(f"line={clue['line']}")
        if clue.get("symbol"):
            parts.append(f"symbol={clue['symbol']}")
        lines.append("Clue to verify: " + " ".join(str(part) for part in parts if part))
    lines.extend(
        "Initial unknown {id} [{type}, {strategy}, blocking={blocking}]: {question}".format(
            id=item.get("id", ""),
            type=item.get("type", ""),
            strategy=item.get("resolution_strategy", ""),
            blocking=bool(item.get("blocking")),
            question=item.get("question", ""),
        )
        for item in analysis["unknowns"]
    )
    return lines


def _open_analysis_unknowns(analysis: dict, investigation: dict | None) -> list[dict]:
    resolved = {
        str(item.get("unknown_id") or "").strip()
        for item in (investigation or {}).get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") in {"resolved", "deferred"}
        and str(item.get("unknown_id") or "").strip()
    }
    if not resolved:
        return analysis.get("unknowns", [])
    return [
        item for item in analysis.get("unknowns", [])
        if isinstance(item, dict)
        and not any(_same_unknown_id(item.get("id"), known_id) for known_id in resolved)
    ]


def _same_unknown_id(left: str | None, right: str | None) -> bool:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text or not right_text:
        return False
    return left_text == right_text or left_text.rsplit(":", 1)[-1] == right_text.rsplit(":", 1)[-1]


def _merge_items_by_id(old: list[dict], new: list[dict]) -> list[dict]:
    from .task_updates import _merge_items_by_id as merge_items

    return merge_items(old, new)
