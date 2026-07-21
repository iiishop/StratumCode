from __future__ import annotations

from uuid import uuid4

from . import sessions
from .status.session_memory import _select_session_memory, _session_context
from .status.task_contract import _ensure_task_contract, request_from_analysis, run_request
from .status.task_updates import _normalize_task_updates
from .status.user_context import _answer_context, _answered_task_update, _continuation_context, _design_plan_with_answer, _prior_findings


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
    return state if state in transitions else None


# -- restore_context 的阶段函数 ------------------------------------------------

_FIELD_MAP: list[tuple[str, type, str | None]] = [
    ("analysis",             dict, None),
    ("investigation",        dict, "last_investigation"),
    ("design_plan",          dict, None),
    ("patch_plan",           dict, None),
    ("implementation_result", dict, None),
    ("validation_result",    dict, None),
]


def _hydrate_fields(run, answer: dict) -> None:
    """将 answer 中的已验证字段赋值到 run 上。"""
    for source_key, expected_type, target_attr in _FIELD_MAP:
        value = answer.get(source_key)
        if isinstance(value, expected_type):
            setattr(run, target_attr or source_key, value)


def _hydrate_changed_files(run, answer: dict) -> None:
    """填充 changed_files：优先 answer 中的列表，fallback 到 implementation_result。"""
    if isinstance(answer.get("changed_files"), list):
        run.changed_files = [str(path) for path in answer["changed_files"]]
    elif run.implementation_result and isinstance(run.implementation_result.get("changed_files"), list):
        run.changed_files = [str(path) for path in run.implementation_result["changed_files"]]


def _normalize_analysis(run, answer: dict) -> None:
    """保证 analysis 非空、补齐 id/origin_message，并合并 answer 附带的上下文。"""
    if run.analysis is None:
        run.analysis = {}
    run.analysis = _ensure_task_contract(run.analysis)
    run.analysis.setdefault("id", str(answer.get("analysis_id") or f"task-{uuid4().hex[:8]}"))
    run.analysis.setdefault("origin_message", request_from_analysis(run.analysis, run.message))
    answer_context = _answer_context(answer)
    if answer_context:
        run.context = run.context + answer_context


def _enrich_session(run, answer: dict) -> None:
    """加载 session 状态，计算记忆筛选、任务更新、findings 和 continuation_context。"""
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


def restore_context(run, answer: dict) -> None:
    """从 answer 恢复 run 的完整上下文：字段赋值 → 文件列表 → analysis 规范化 → session 富化。"""
    _hydrate_fields(run, answer)
    _hydrate_changed_files(run, answer)
    _normalize_analysis(run, answer)
    _enrich_session(run, answer)


def resume_from_answer(run, answer: dict, chat_state, transitions) -> None:
    resume_state = answer_resume_state(answer, chat_state, transitions)
    restore_context(run, answer)
    if resume_state:
        run.state = resume_state
    else:
        run.state = chat_state.SAVING_SESSION if run.session_id and run.last_investigation else chat_state.COMPLETED
    if run.state == chat_state.PATCH_PLANNING:
        run.design_plan = _design_plan_with_answer(run.design_plan or {}, answer)
    run.answer = None
