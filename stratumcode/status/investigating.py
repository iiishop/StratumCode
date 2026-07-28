from __future__ import annotations

from uuid import uuid4

from .. import investigator
from ..agent_runtime import start_event
from ..status.task_analysis import _analysis_requests_implementation
from .clearifying import queue_clearify
from .task_contract import _ensure_task_contract, run_request
from .task_updates import (
    _apply_task_updates,
    _beliefs_as_knowledge,
    _finalize_task_statuses,
    _investigation_continuation_findings,
    _merge_findings,
    _merge_items_by_id,
    _normalize_task_updates,
    _scoped_items,
)
from .session_memory import _session_context_lines
from .session_memory import _attach_session_relationship, _select_session_memory
from .task_updates import _seed_task_updates


def _analysis_context(analysis: dict) -> list[str]:
    """把规范化后的 task analysis 渲染成 investigation 使用的上下文行。"""
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


def prepare_investigation(run):
    run.analysis = _ensure_task_contract(run.analysis or {})
    run.analysis.setdefault("id", f"task-{uuid4().hex[:8]}")
    run.analysis.setdefault("origin_message", run.message)
    request = run_request(run)
    run.selected_session_context = _select_session_memory(request, run.analysis, run.session_context)
    _attach_session_relationship(run.analysis, run.session_context.get("tasks", []))
    seeded_tasks = _seed_task_updates(run.analysis, run.session_context.get("tasks", []))
    run.analysis["task_updates"] = _normalize_task_updates(
        run.analysis["id"],
        run.analysis.get("task_updates", []) + seeded_tasks,
        run.session_context.get("tasks", []),
    )
    yield start_event(f"task-analysis-{uuid4().hex[:8]}", "task_analysis", run.analysis)


def handle(run):
    from .. import chat

    session_lines = _session_context_lines(run.selected_session_context)
    last_investigation = None
    pending_question = None
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
    request = run_request(run)
    investigation_analysis = run.analysis
    unresolved = (run.last_investigation or {}).get("unknowns")
    if isinstance(unresolved, list) and unresolved:
        investigation_analysis = {
            **run.analysis,
            "unknowns": _merge_items_by_id(
                run.analysis.get("unknowns", []),
                unresolved,
            ),
        }
    for event in investigator.investigation_stream(
        message=request,
        analysis=investigation_analysis,
        context=run.context + session_lines + _analysis_context(investigation_analysis) + run.continuation_context,
        workspace_dir=run.workspace_dir,
        max_rounds=run.max_rounds,
        findings=run.findings,
        previous_observations=previous_observations,
        previous_knowledge=previous_knowledge,
        previous_findings=run.last_investigation,
        preserve_grounding_evidence=True,
    ):
        if event.get("event") == "task_update":
            applied = _apply_task_updates(
                run.analysis["id"],
                run.analysis.get("task_updates", []),
                event["data"].get("items", []),
                [],
            )
            event["data"]["items"] = applied["items"]
            event["data"]["changes"] = applied["changes"]
            run.analysis["task_updates"] = applied["items"]
        if event.get("op") == "start" and event.get("event") == "user_question" and event.get("data", {}).get("clearify_tool"):
            yield event
            continue
        if event.get("op") == "start" and event.get("event") == "user_question":
            pending_question = event
            continue
        if event.get("op") == "done" and isinstance(event.get("investigation"), dict):
            last_investigation = event["investigation"]
            last_investigation["task_updates"] = _normalize_task_updates(
                run.analysis["id"],
                run.analysis.get("task_updates", []) + last_investigation.get("task_updates", []),
                run.session_context.get("tasks", []),
            )
            last_investigation["task_updates"] = _finalize_task_statuses(last_investigation["task_updates"], last_investigation)
            applied = _apply_task_updates(
                run.analysis["id"],
                run.analysis.get("task_updates", []),
                last_investigation["task_updates"],
                run.session_context.get("tasks", []),
            )
            last_investigation["task_updates"] = applied["items"]
            scoped_observations = _scoped_items(
                run.analysis["id"],
                last_investigation.get("observations", []),
            )
            grounding_observations = [
                {**item, "fresh": item.get("fresh", True)}
                for item in scoped_observations
            ]
            run.investigation_grounding_observations = _merge_items_by_id(
                run.investigation_grounding_observations,
                grounding_observations,
            )
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
            last_investigation["observations"] = new_observations
            run.investigation_observations = _merge_items_by_id(run.investigation_observations, new_observations)
            run.investigation_knowledge = _merge_items_by_id(
                run.investigation_knowledge,
                _beliefs_as_knowledge(run.analysis["id"], last_investigation.get("beliefs", [])),
            )
            run.analysis["task_updates"] = last_investigation["task_updates"]
            yield start_event(f"{run.analysis['id']}-task-final", "task_update", {
                "analysis_id": run.analysis["id"],
                "items": run.analysis["task_updates"],
                "changes": applied["changes"],
            })
            if pending_question:
                run.last_investigation = last_investigation
                yield event
                data = pending_question.get("data") or {}
                queue_clearify(
                    run,
                    data.get("question") or "Which behavior should be used?",
                    reason=data.get("reason") or "Investigation requires a product decision.",
                    unknown_id=str(data.get("unknown_id") or ""),
                )
                run.transition(chat.ChatState.INVESTIGATING, "Investigation queued a clearify decision.")
                pending_question = None
                return
        yield event
    run.last_investigation = last_investigation
    if pending_question:
        data = pending_question.get("data") or {}
        queue_clearify(
            run,
            data.get("question") or "Which behavior should be used?",
            reason=data.get("reason") or "Investigation requires a product decision.",
            unknown_id=str(data.get("unknown_id") or ""),
        )
        run.transition(chat.ChatState.INVESTIGATING, "Investigation queued a clearify decision.")
        return
    next_step = ((run.last_investigation or {}).get("step_result") or {}).get("next_step")
    blocking_unknown_ids = _blocking_unknown_ids(run.last_investigation)
    has_blocked_task = _has_task_status(run.last_investigation, "blocked")
    has_unknown_task = _has_task_status(run.last_investigation, "unknown")
    if next_step == "done":
        run.transition(chat._chat_finish_state(run), "Investigation ended without an implementation path.")
    elif next_step == "failed" and blocking_unknown_ids:
        step = (run.last_investigation or {}).setdefault("step_result", {})
        step["next_step"] = "continue_investigation"
        step["target_unknown_ids"] = blocking_unknown_ids
        step["unresolved_unknown_ids"] = blocking_unknown_ids
        run.findings = _merge_findings(run.findings, _investigation_continuation_findings(run.last_investigation))
        run.transition(chat.ChatState.INVESTIGATING, "Investigation still has unresolved blocking unknowns.")
    elif next_step == "failed":
        run.transition(chat.ChatState.FAILED, "Investigation failed.")
    elif (
        run.last_investigation
        and _investigation_allows_patch(run.last_investigation)
        and _analysis_requests_implementation(run.analysis)
    ):
        run.transition(chat.ChatState.DESIGNING, "Investigation is ready for implementation planning.")
    elif next_step == "continue_investigation" or has_unknown_task or has_blocked_task:
        run.findings = _merge_findings(run.findings, _investigation_continuation_findings(run.last_investigation))
        run.transition(chat.ChatState.INVESTIGATING, "Investigation requested another pass.")
    else:
        run.transition(chat._chat_finish_state(run), "Investigation ended without an implementation path.")


def _investigation_allows_patch(investigation: dict) -> bool:
    if _has_task_status(investigation, "blocked"):
        return False
    if _has_task_status(investigation, "unknown"):
        return False
    raw_step = investigation.get("step_result")
    step: dict = raw_step if isinstance(raw_step, dict) else {}
    return bool(investigation.get("ready_for_patch_planning") or step.get("next_step") == "write_code")


def _has_open_tasks(investigation: dict) -> bool:
    return _has_task_status(investigation, "blocked") or _has_task_status(investigation, "unknown")


def _has_task_status(investigation: dict | None, status: str) -> bool:
    if not investigation:
        return False
    for item in investigation.get("task_updates", []) if isinstance(investigation.get("task_updates"), list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "hypothesis":
            continue
        if item.get("status") == status:
            return True
    return False


def _blocking_unknown_ids(investigation: dict | None) -> list[str]:
    if not investigation or not isinstance(investigation.get("unknowns"), list):
        return []
    return [
        str(item.get("id") or "").strip()
        for item in investigation["unknowns"]
        if isinstance(item, dict)
        and item.get("blocking")
        and str(item.get("id") or "").strip()
    ]


def _fallback_question(run, request: str) -> dict:
    question = next(
        (
            str(item.get("text") or item.get("question") or "").strip()
            for item in (run.last_investigation or {}).get("task_updates", [])
            if isinstance(item, dict) and item.get("status") in {"blocked", "unknown"}
        ),
        "",
    ) or "Please clarify the next decision."
    return {
        "id": f"question-{uuid4().hex[:8]}",
        "analysis_id": run.analysis["id"],
        "question": question,
        "origin_message": request,
        "reason": (run.last_investigation or {}).get("summary", ""),
        "why_it_matters": "The investigation needs your answer before it can continue.",
    }
