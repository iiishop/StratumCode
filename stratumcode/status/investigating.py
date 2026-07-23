from __future__ import annotations

from uuid import uuid4

from .. import investigator
from ..agent_runtime import start_event
from ..status.task_analysis import IMPLEMENTATION_INTENT_TYPES, _message_requests_implementation
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
        run.selected_session_context.get("observations", []),
        run.investigation_observations,
    )
    previous_knowledge = _merge_items_by_id(
        run.selected_session_context.get("knowledge", []),
        run.investigation_knowledge,
    )
    request = run_request(run)
    for event in investigator.investigation_stream(
        message=request,
        analysis=run.analysis,
        context=run.context + session_lines + _analysis_context(run.analysis) + run.continuation_context,
        workspace_dir=run.workspace_dir,
        max_rounds=run.max_rounds,
        findings=run.findings,
        previous_observations=previous_observations,
        previous_knowledge=previous_knowledge,
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
            last_investigation["observations"] = _scoped_items(run.analysis["id"], last_investigation.get("observations", []))
            new_observations = [{**item, "fresh": item.get("fresh", True)} for item in last_investigation["observations"]]
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
                yield _unsupported_user_question(pending_question)
                run.transition(chat.ChatState.FAILED, "Investigation emitted a legacy user question.")
                pending_question = None
                return
        yield event
    run.last_investigation = last_investigation
    if pending_question:
        yield _unsupported_user_question(pending_question)
        run.transition(chat.ChatState.FAILED, "Investigation emitted a legacy user question.")
        return
    next_step = ((run.last_investigation or {}).get("step_result") or {}).get("next_step")
    has_blocked_task = _has_task_status(run.last_investigation, "blocked")
    has_unknown_task = _has_task_status(run.last_investigation, "unknown")
    if next_step == "done":
        run.transition(chat._chat_finish_state(run), "Investigation ended without an implementation path.")
    elif next_step == "ask_user" or has_blocked_task:
        yield start_event(f"{run.analysis['id']}-unsupported-question", "output", {
            "content": "Investigation requested legacy checkpoint. Use the clearify tool instead.",
            "streaming": False,
        })
        run.transition(chat.ChatState.FAILED, "Legacy checkpoint is disabled.")
    elif run.last_investigation and _investigation_allows_patch(run.last_investigation) and _wants_implementation(run.analysis, request):
        run.transition(chat.ChatState.DESIGNING, "Investigation is ready for implementation planning.")
    elif next_step == "continue_investigation" or has_unknown_task:
        run.findings = _merge_findings(run.findings, _investigation_continuation_findings(run.last_investigation))
        run.transition(chat.ChatState.INVESTIGATING, "Investigation requested another pass.")
    elif next_step == "failed":
        run.transition(chat.ChatState.FAILED, "Investigation failed.")
    else:
        run.transition(chat._chat_finish_state(run), "Investigation ended without an implementation path.")


def _wants_implementation(analysis: dict, message: str = "") -> bool:
    return (
        (analysis.get("intent") or {}).get("type") in IMPLEMENTATION_INTENT_TYPES
        or _message_requests_implementation(message)
    )


def _investigation_allows_patch(investigation: dict) -> bool:
    if _has_task_status(investigation, "blocked"):
        return False
    if _has_task_status(investigation, "unknown") and not (
        investigation.get("runtime_recovered") and (
            investigation.get("patch_planning_facts") or investigation.get("patch_planning_context")
        )
    ):
        return False
    step = investigation.get("step_result") if isinstance(investigation.get("step_result"), dict) else {}
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


def _unsupported_user_question(event: dict) -> dict:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    question = str(data.get("question") or "A legacy user question was requested.").strip()
    return start_event(f"{event.get('id', 'legacy-question')}-unsupported", "output", {
        "content": f"Legacy checkpoint is disabled: {question}",
        "streaming": False,
    })


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
