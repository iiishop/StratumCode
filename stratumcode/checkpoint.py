from __future__ import annotations

from uuid import uuid4

from . import sessions
from .status.session_memory import _select_session_memory, _session_context
from .status.task_contract import _ensure_task_contract, request_from_analysis, run_request
from .status.task_updates import _normalize_task_updates
from .status.user_context import _answer_context, _answered_task_update, _continuation_context, _prior_findings


def prepare_question(
    question: dict,
    *,
    resume_state,
    analysis: dict | None = None,
    investigation: dict | None = None,
    design_plan: dict | None = None,
    patch_plan: dict | None = None,
    implementation_result: dict | None = None,
    validation_result: dict | None = None,
) -> dict:
    checkpoint_id = str(question.get("checkpoint_id") or question.get("id") or f"checkpoint-{uuid4().hex[:8]}")
    question.update({
        "checkpoint_id": checkpoint_id,
        "resume_state": resume_state.value,
        "question_id": question.get("question_id") or question.get("id") or checkpoint_id,
        "analysis": analysis or {},
        "investigation": investigation or {},
        "design_plan": design_plan or question.get("design_plan") or {},
        "patch_plan": patch_plan or question.get("patch_plan") or {},
        "implementation_result": implementation_result or question.get("implementation_result") or {},
        "validation_result": validation_result or question.get("validation_result") or {},
        "answer_status": "waiting",
    })
    return question


def answer_resume_state(answer: dict, chat_state, transitions) -> object | None:
    raw = str(answer.get("resume_state") or "").strip()
    if not raw:
        raw = {
            "design_checkpoint": chat_state.PATCH_PLANNING.value,
            "implementation_checkpoint": chat_state.IMPLEMENTING.value,
            "validation_checkpoint": chat_state.VALIDATING.value,
            "investigation_provider_checkpoint": chat_state.INVESTIGATING.value,
        }.get(str(answer.get("checkpoint_phase") or ""), "")
    try:
        state = chat_state(raw)
    except ValueError:
        return None
    return state if state in transitions[chat_state.WAITING_FOR_USER] else None


def restore_context(run, answer: dict) -> None:
    if isinstance(answer.get("analysis"), dict):
        run.analysis = answer["analysis"]
    if isinstance(answer.get("investigation"), dict):
        run.last_investigation = answer["investigation"]
    if isinstance(answer.get("design_plan"), dict):
        run.design_plan = answer["design_plan"]
    if isinstance(answer.get("patch_plan"), dict):
        run.patch_plan = answer["patch_plan"]
    if isinstance(answer.get("implementation_result"), dict):
        run.implementation_result = answer["implementation_result"]
    if isinstance(answer.get("validation_result"), dict):
        run.validation_result = answer["validation_result"]
    if isinstance(answer.get("changed_files"), list):
        run.changed_files = [str(path) for path in answer["changed_files"]]
    elif run.implementation_result and isinstance(run.implementation_result.get("changed_files"), list):
        run.changed_files = [str(path) for path in run.implementation_result["changed_files"]]
    if run.analysis is None:
        run.analysis = {}
    run.analysis = _ensure_task_contract(run.analysis)
    run.analysis.setdefault("id", str(answer.get("analysis_id") or f"task-{uuid4().hex[:8]}"))
    run.analysis.setdefault("origin_message", request_from_analysis(run.analysis, run.message))
    answer_context = _answer_context(answer)
    if answer_context:
        run.context = run.context + answer_context
    try:
        state = sessions.get(run.session_id)["state"] if run.session_id else {}
    except ValueError:
        state = {}
    run.session_context = _session_context(state)
    run.selected_session_context = _select_session_memory(run_request(run), run.analysis, run.session_context)
    run.answered_task = _answered_task_update(run.analysis["id"], answer)
    if run.answered_task:
        run.analysis["task_updates"] = _normalize_task_updates(
            run.analysis["id"],
            run.analysis.get("task_updates", []) + [run.answered_task],
            run.session_context.get("tasks", []),
        )
    run.findings = _prior_findings(run.analysis, answer)
    run.continuation_context = _continuation_context(answer, run.selected_session_context)
