from __future__ import annotations

from .. import checkpoint, investigator
from ..agent_runtime import start_event
from ..status.task_analysis import IMPLEMENTATION_INTENT_TYPES, _message_requests_implementation
from .task_contract import _analysis_context, run_request
from .task_updates import (
    _beliefs_as_knowledge,
    _finalize_task_statuses,
    _investigation_continuation_findings,
    _merge_findings,
    _merge_items_by_id,
    _normalize_task_updates,
    _scoped_items,
)
from .session_memory import _session_context_lines


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
            event["data"]["items"] = _normalize_task_updates(
                run.analysis["id"],
                run.analysis.get("task_updates", []) + event["data"].get("items", []) + ([run.answered_task] if run.answered_task else []),
                [],
            )
            run.analysis["task_updates"] = event["data"]["items"]
        if event.get("op") == "start" and event.get("event") == "user_question":
            pending_question = event
            continue
        if event.get("op") == "done" and isinstance(event.get("investigation"), dict):
            last_investigation = event["investigation"]
            last_investigation["task_updates"] = _normalize_task_updates(
                run.analysis["id"],
                run.analysis.get("task_updates", []) + last_investigation.get("task_updates", []) + ([run.answered_task] if run.answered_task else []),
                run.session_context.get("tasks", []),
            )
            last_investigation["task_updates"] = _finalize_task_statuses(last_investigation["task_updates"], last_investigation)
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
            })
            if pending_question:
                checkpoint.prepare_question(
                    pending_question["data"],
                    resume_state=chat.ChatState.INVESTIGATING,
                    analysis=run.analysis,
                    investigation=last_investigation,
                )
                yield pending_question
                pending_question = None
        yield event
    run.last_investigation = last_investigation
    if pending_question:
        checkpoint.prepare_question(
            pending_question["data"],
            resume_state=chat.ChatState.INVESTIGATING,
            analysis=run.analysis,
            investigation=run.last_investigation,
        )
        yield pending_question
    next_step = ((run.last_investigation or {}).get("step_result") or {}).get("next_step")
    has_blocked_task = _has_task_status(run.last_investigation, "blocked")
    has_unknown_task = _has_task_status(run.last_investigation, "unknown")
    if pending_question:
        run.transition(chat.ChatState.WAITING_FOR_USER, "Investigation needs user input.")
    elif next_step == "done":
        run.transition(chat._chat_finish_state(run), "Investigation ended without an implementation path.")
    elif next_step == "ask_user" or has_blocked_task:
        run.transition(chat.ChatState.WAITING_FOR_USER, "Investigation needs user input.")
    elif next_step == "continue_investigation" or has_unknown_task:
        run.findings = _merge_findings(run.findings, _investigation_continuation_findings(run.last_investigation))
        run.transition(chat.ChatState.INVESTIGATING, "Investigation requested another pass.")
    elif next_step == "failed":
        run.transition(chat.ChatState.FAILED, "Investigation failed.")
    elif run.last_investigation and _investigation_allows_patch(run.last_investigation) and _wants_implementation(run.analysis, request):
        run.transition(chat.ChatState.DESIGNING, "Investigation is ready for implementation planning.")
    else:
        run.transition(chat._chat_finish_state(run), "Investigation ended without an implementation path.")


def _wants_implementation(analysis: dict, message: str = "") -> bool:
    return (
        (analysis.get("intent") or {}).get("type") in IMPLEMENTATION_INTENT_TYPES
        or _message_requests_implementation(message)
    )


def _investigation_allows_patch(investigation: dict) -> bool:
    if _has_open_tasks(investigation):
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
        if item.get("status") == status:
            return True
    return False
