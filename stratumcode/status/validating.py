from __future__ import annotations

from uuid import uuid4

from .. import implementation_runner
from . import clearify
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
            yield clearify.ask_event(
                run,
                event,
                resume_state=chat.ChatState.VALIDATING,
                analysis=run.analysis,
                investigation=run.last_investigation,
                design_plan=run.design_plan,
                patch_plan=run.patch_plan,
                implementation_result=run.implementation_result,
                validation_result=run.validation_result,
            )
            return
        if event.get("op") == "done" and isinstance(event.get("validation_result"), dict):
            run.validation_result = event["validation_result"]
        yield event
    if run.state == chat.ChatState.VALIDATING:
        next_state = _state_after_validation(run)
        if next_state == "clearify":
            yield _validation_user_question(run)
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
        return "clearify"
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
    return clearify.ask(
        run,
        question,
        resume_state=chat.ChatState.VALIDATING,
        analysis=run.analysis,
        investigation=run.last_investigation,
        design_plan=run.design_plan,
        patch_plan=run.patch_plan,
        implementation_result=run.implementation_result,
        validation_result=run.validation_result,
        event_id=f"{run.analysis['id']}-validation-question",
    )


def _add_validation_context(run) -> None:
    result = run.validation_result or {}
    lines = ["Validation feedback for next pass:", result.get("summary", "")]
    for issue in result.get("issues", []) if isinstance(result.get("issues"), list) else []:
        if isinstance(issue, dict):
            loc = f" ({issue.get('file')}:{issue.get('line')})" if issue.get("file") else ""
            lines.append(f"- {issue.get('severity', 'issue')}: {issue.get('summary', '')}{loc}")
    lines = [line for line in lines if line]
    repair_facts = _validation_repair_facts(result)
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
    run.last_investigation["patch_planning_context"] = context + lines + repair_facts
    facts = run.last_investigation.get("patch_planning_facts")
    if not isinstance(facts, list):
        facts = []
    run.last_investigation["patch_planning_facts"] = facts + repair_facts
    structured = run.last_investigation.get("structured_findings")
    if not isinstance(structured, dict):
        structured = {}
    structured["validation_repair_candidates"] = _validation_repair_candidates(result)
    run.last_investigation["structured_findings"] = structured
    run.last_investigation["summary"] = " ".join(
        part for part in [run.last_investigation.get("summary", ""), result.get("summary", "")]
        if part
    )


def _validation_repair_facts(result: dict) -> list[str]:
    facts = []
    for index, issue in enumerate(result.get("issues", []) if isinstance(result.get("issues"), list) else [], start=1):
        if not isinstance(issue, dict):
            continue
        loc = f"{issue.get('file')}:{issue.get('line')}" if issue.get("file") else ""
        facts.append(
            " ".join(part for part in [
                f"VAL{index}",
                str(issue.get("severity") or "issue"),
                loc,
                str(issue.get("summary") or ""),
            ] if part)
        )
    return facts


def _validation_repair_candidates(result: dict) -> list[dict]:
    candidates = []
    for index, issue in enumerate(result.get("issues", []) if isinstance(result.get("issues"), list) else [], start=1):
        if not isinstance(issue, dict):
            continue
        candidates.append({
            "id": f"VAL{index}",
            "kind": "validation_repair",
            "severity": issue.get("severity") or "issue",
            "file": issue.get("file") or "",
            "line": issue.get("line") or 0,
            "safe_action": "repair",
            "reason": issue.get("summary") or "",
        })
    return candidates
