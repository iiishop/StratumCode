from __future__ import annotations

from .. import implementation_runner
from .clearifying import queue_clearify
from .task_contract import run_request


def handle(run):
    from .. import chat

    if run.changed_files:
        changed_files = [str(path) for path in run.changed_files]
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
            data = event.get("data") or {}
            queue_clearify(
                run,
                data.get("question") or "Which validated behavior should be accepted?",
                reason=data.get("reason") or "Validation requires a product decision.",
                unknown_id=str(data.get("unknown_id") or ""),
            )
            run.transition(chat.ChatState.INVESTIGATING, "Validation queued a clearify decision.")
            return
        if event.get("op") == "done" and isinstance(event.get("validation_result"), dict):
            run.validation_result = event["validation_result"]
        yield event
    if run.state == chat.ChatState.VALIDATING:
        next_state = _state_after_validation(run)
        if next_state == "clearify":
            _add_validation_context(run)
            result = run.validation_result or {}
            queue_clearify(
                run,
                result.get("question") or result.get("summary") or "Which validated behavior should be accepted?",
                reason=result.get("summary") or "Validation requires a product decision.",
            )
            run.transition(chat.ChatState.INVESTIGATING, "Validation queued a clearify decision.")
        elif next_state in {chat.ChatState.DESIGNING, chat.ChatState.INVESTIGATING}:
            _add_validation_context(run)
            run.transition(next_state, "Validation completed.")
        else:
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
    if verdict == "clearify":
        return "clearify"
    return chat.ChatState.FAILED


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
