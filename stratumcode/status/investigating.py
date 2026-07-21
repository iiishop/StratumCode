from __future__ import annotations

from uuid import uuid4

from .. import investigator
from ..agent_runtime import start_event
from ..status.task_analysis import IMPLEMENTATION_INTENT_TYPES, _message_requests_implementation
from . import clearify
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
from .session_memory import _attach_session_relationship, _select_session_memory
from .task_contract import _ensure_task_contract
from .task_updates import _seed_task_updates
from .user_context import _answered_task_update, _continuation_context, _prior_findings


def prepare_investigation(run):
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
                run.last_investigation = last_investigation
                yield event
                yield clearify.ask_event(
                    run,
                    pending_question,
                    resume_state=chat.ChatState.INVESTIGATING,
                    analysis=run.analysis,
                    investigation=last_investigation,
                )
                pending_question = None
                return
        yield event
    run.last_investigation = last_investigation
    if pending_question:
        yield clearify.ask_event(
            run,
            pending_question,
            resume_state=chat.ChatState.INVESTIGATING,
            analysis=run.analysis,
            investigation=run.last_investigation,
        )
        return
    next_step = ((run.last_investigation or {}).get("step_result") or {}).get("next_step")
    has_blocked_task = _has_task_status(run.last_investigation, "blocked")
    has_unknown_task = _has_task_status(run.last_investigation, "unknown")
    if next_step == "done":
        run.transition(chat._chat_finish_state(run), "Investigation ended without an implementation path.")
    elif next_step == "ask_user" or has_blocked_task:
        yield clearify.ask(
            run,
            _fallback_question(run, request),
            resume_state=chat.ChatState.INVESTIGATING,
            analysis=run.analysis,
            investigation=run.last_investigation,
        )
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
        if item.get("status") == status:
            return True
    return False


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
