from __future__ import annotations

from uuid import uuid4

from .. import checkpoint, implementation_runner
from ..agent_runtime import start_event
from .task_contract import run_request


def handle(run):
    from .. import chat

    answer = run.answer if isinstance(run.answer, dict) else {}
    answer_changed_files = answer.get("changed_files", [])
    if run.changed_files:
        changed_files = [str(path) for path in run.changed_files]
    elif isinstance(answer_changed_files, list):
        changed_files = [str(path) for path in answer_changed_files]
    else:
        changed_files = []
    run.validation_result = None
    for event in implementation_runner.validation_stream(
        message=run_request(run),
        analysis=run.analysis,
        patch_plan=run.patch_plan or {},
        workspace_dir=run.workspace_dir,
        changed_files=changed_files,
    ):
        if event.get("op") == "start" and event.get("event") == "user_question":
            checkpoint.prepare_question(
                event["data"],
                resume_state=chat.ChatState.VALIDATING,
                analysis=run.analysis,
                investigation=run.last_investigation,
                design_plan=run.design_plan,
                patch_plan=run.patch_plan,
                implementation_result=run.implementation_result,
                validation_result=run.validation_result,
            )
            run.transition(chat.ChatState.WAITING_FOR_USER, "Validation needs user input.")
            yield event
            return
        if event.get("op") == "done" and isinstance(event.get("validation_result"), dict):
            run.validation_result = event["validation_result"]
        yield event
    if run.state == chat.ChatState.VALIDATING:
        next_state = _state_after_validation(run)
        if next_state == chat.ChatState.WAITING_FOR_USER:
            question_event = _validation_user_question(run)
            run.transition(next_state, "Validation completed and requires user input.")
            yield question_event
            return
        elif next_state in {chat.ChatState.DESIGNING, chat.ChatState.INVESTIGATING}:
            _add_validation_context(run)
        run.transition(next_state, "Validation completed.")


def _state_after_validation(run):
    from .. import chat

    verdict = str((run.validation_result or {}).get("verdict") or "inconclusive")
    if verdict == "passed":
        return chat._chat_finish_state(run)
    if verdict in {"local_repair", "redesign"}:
        return chat.ChatState.DESIGNING
    if verdict == "missing_evidence":
        return chat.ChatState.INVESTIGATING
    if verdict == "user_decision":
        return chat.ChatState.WAITING_FOR_USER
    return chat.ChatState.FAILED


def _validation_user_question(run) -> dict:
    from .. import chat

    result = run.validation_result or {}
    question = {
        "id": f"validation-{uuid4().hex[:8]}",
        "analysis_id": (run.analysis or {}).get("id", ""),
        "question": result.get("question") or result.get("summary") or "Validation needs your decision.",
        "origin_message": run_request(run),
        "reason": result.get("summary", ""),
        "why_it_matters": "Validation could not safely choose the next step without user input.",
        "options": result.get("options") or [],
        "custom_allowed": True,
    }
    checkpoint.prepare_question(
        question,
        resume_state=chat.ChatState.VALIDATING,
        analysis=run.analysis,
        investigation=run.last_investigation,
        design_plan=run.design_plan,
        patch_plan=run.patch_plan,
        implementation_result=run.implementation_result,
        validation_result=run.validation_result,
    )
    return start_event(f"{run.analysis['id']}-validation-question", "user_question", question)


def _add_validation_context(run) -> None:
    result = run.validation_result or {}
    lines = ["Validation feedback for next pass:", result.get("summary", "")]
    for issue in result.get("issues", []) if isinstance(result.get("issues"), list) else []:
        if isinstance(issue, dict):
            loc = f" ({issue.get('file')}:{issue.get('line')})" if issue.get("file") else ""
            lines.append(f"- {issue.get('severity', 'issue')}: {issue.get('summary', '')}{loc}")
    lines = [line for line in lines if line]
    run.continuation_context = run.continuation_context + lines
    run.findings = run.findings + lines
    if not isinstance(run.last_investigation, dict):
        run.last_investigation = {
            "summary": result.get("summary", ""),
            "patch_planning_context": [],
            "patch_planning_facts": [],
        }
    context = run.last_investigation.get("patch_planning_context")
    if not isinstance(context, list):
        context = []
    run.last_investigation["patch_planning_context"] = context + lines
    run.last_investigation["summary"] = " ".join(
        part for part in [run.last_investigation.get("summary", ""), result.get("summary", "")]
        if part
    )
