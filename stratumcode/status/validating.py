from __future__ import annotations

from .. import implementation_runner
from .clearifying import queue_clearify
from .task_contract import run_request
from .user_waiting import prepared_user_question_event, user_question_event


def handle(run):
    from .. import chat

    if run.changed_files:
        changed_files = [str(path) for path in run.changed_files]
    else:
        changed_files = []
    patch_records = (run.implementation_result or {}).get("patch_records") or []
    satisfied_steps = (run.implementation_result or {}).get("satisfied_steps") or []
    # Hard gate: py_compile every changed Python file before semantic validation
    py_issues = _check_python_syntax(changed_files, run.workspace_dir)
    if py_issues:
        run.validation_result = implementation_runner._validation_result(
            "local_repair",
            "Python syntax check failed. The patch introduced invalid syntax.",
            changed_files,
            issues=py_issues,
        )
        yield from _emit_validation_done(run)
        # Manually run state transition since we bypass the normal loop
        next_state = _state_after_validation(run)
        if next_state == "clearify":
            _add_validation_context(run)
            result = run.validation_result or {}
            queue_clearify(
                run,
                result.get("question") or result.get("summary") or "Which validated behavior should be accepted?",
                reason=result.get("summary") or "Validation requires a product decision.",
            )
            yield user_question_event(
                run,
                question=result.get("question") or result.get("summary") or "Which validated behavior should be accepted?",
                reason=result.get("summary") or "Validation requires a product decision.",
                checkpoint_phase="validation_checkpoint",
                resume_state="validating",
                extra={
                    "validation_result": result,
                    "patch_plan": run.patch_plan or {},
                    "changed_files": changed_files,
                    **({"options": result["options"]} if isinstance(result.get("options"), list) else {}),
                },
            )
            run.transition(chat.ChatState.WAITING_FOR_USER, "Validation queued a clearify decision.")
        elif next_state in {chat.ChatState.DESIGNING, chat.ChatState.INVESTIGATING}:
            _add_validation_context(run)
            if next_state == chat.ChatState.DESIGNING:
                run.design_revision_mode = "validation"
            run.transition(next_state, "Python syntax check failed; returning for repair.")
        else:
            run.transition(next_state, "Python syntax check failed.")
        return
    run.validation_result = None
    # Build validation hint for retry after inconclusive
    validation_hint = ""
    if run.validation_inconclusive_count == 1:
        validation_hint = (
            "Last validation was inconclusive — finish_validation was not called. "
            "You MUST call finish_validation with a definitive verdict this round."
        )
    elif run.validation_inconclusive_count >= 2:
        validation_hint = (
            f"STOPPING IS NOT AN OPTION. The last {run.validation_inconclusive_count} "
            "validation rounds were inconclusive because you did not call finish_validation. "
            "You MUST call finish_validation this round with verdict passed, local_repair, or redesign. "
            "If you inspected the code and found issues, report them and use local_repair. "
            "If the code is correct, use passed. Do not leave without a verdict."
        )
    for event in implementation_runner.validation_stream(
        message=run_request(run),
        analysis=run.analysis,
        patch_plan=run.patch_plan or {},
        workspace_dir=run.workspace_dir,
        changed_files=changed_files,
        patch_records=patch_records,
        satisfied_steps=satisfied_steps,
        validation_hint=validation_hint,
    ):
        if event.get("op") == "start" and event.get("event") == "user_question":
            data = event.get("data") or {}
            queue_clearify(
                run,
                data.get("question") or "Which validated behavior should be accepted?",
                reason=data.get("reason") or "Validation requires a product decision.",
                unknown_id=str(data.get("unknown_id") or ""),
            )
            yield prepared_user_question_event(
                event,
                checkpoint_phase="validation_checkpoint",
                resume_state="validating",
                extra={
                    "patch_plan": run.patch_plan or {},
                    "changed_files": changed_files,
                },
            )
            run.transition(chat.ChatState.WAITING_FOR_USER, "Validation queued a clearify decision.")
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
            yield user_question_event(
                run,
                question=result.get("question") or result.get("summary") or "Which validated behavior should be accepted?",
                reason=result.get("summary") or "Validation requires a product decision.",
                checkpoint_phase="validation_checkpoint",
                resume_state="validating",
                extra={
                    "validation_result": result,
                    "patch_plan": run.patch_plan or {},
                    "changed_files": changed_files,
                    **({"options": result["options"]} if isinstance(result.get("options"), list) else {}),
                },
            )
            run.transition(chat.ChatState.WAITING_FOR_USER, "Validation queued a clearify decision.")
        elif next_state in {chat.ChatState.DESIGNING, chat.ChatState.INVESTIGATING}:
            _add_validation_context(run)
            if next_state == chat.ChatState.DESIGNING:
                run.design_revision_mode = "validation"
            run.transition(next_state, "Validation completed.")
        else:
            run.transition(next_state, "Validation completed.")


def _state_after_validation(run):
    from .. import chat

    verdict = str((run.validation_result or {}).get("verdict") or "inconclusive")
    reason_code = str((run.validation_result or {}).get("reason_code") or "")
    if verdict == "passed":
        return chat._chat_finish_state(run)
    if verdict in {"local_repair", "redesign"}:
        if not _has_validation_repair_signal(run.validation_result or {}):
            return chat.ChatState.FAILED
        return chat.ChatState.DESIGNING
    if verdict == "inconclusive":
        run.validation_inconclusive_count += 1
        if reason_code == "missing_finish_validation":
            if run.validation_inconclusive_count <= 1:
                return chat.ChatState.VALIDATING
            return chat.ChatState.FAILED
        if reason_code == "insufficient_evidence":
            return chat.ChatState.INVESTIGATING
        return chat.ChatState.DESIGNING
    if verdict == "missing_evidence":
        return chat.ChatState.INVESTIGATING
    if verdict == "clearify":
        return "clearify"
    return chat.ChatState.FAILED


def _has_validation_repair_signal(result: dict) -> bool:
    if result.get("repair_plan"):
        return True
    issues = result.get("issues") if isinstance(result.get("issues"), list) else []
    return any(
        isinstance(issue, dict) and str(issue.get("summary") or "").strip()
        for issue in issues
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


def _validation_repair_facts(result: dict) -> list[dict]:
    changed_files = [
        str(item)
        for item in result.get("changed_files", [])
        if str(item).strip()
    ]
    facts = []
    summary = str(result.get("summary") or "").strip()
    if summary:
        facts.append({
            "id": "VAL0",
            "text": f"Post-patch validation summary: {summary}",
            "authority": "runtime_validation",
            "evidence_ids": ["validation:summary"],
            "supersedes_files": changed_files,
        })
    for index, issue in enumerate(result.get("issues", []) if isinstance(result.get("issues"), list) else [], start=1):
        if not isinstance(issue, dict):
            continue
        loc = f"{issue.get('file')}:{issue.get('line')}" if issue.get("file") else ""
        facts.append({
            "id": f"VAL{index}",
            "text": " ".join(part for part in [
                f"VAL{index}",
                str(issue.get("severity") or "issue"),
                loc,
                str(issue.get("summary") or ""),
            ] if part),
            "authority": "runtime_validation",
            "evidence_ids": [f"validation:{issue.get('id') or index}"],
            "supersedes_files": [str(issue.get("file"))] if issue.get("file") else [],
        })
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


def _check_python_syntax(changed_files: list[str], workspace_dir: str) -> list[dict]:
    """Run py_compile on every changed Python file. Return issues for any syntax error."""
    import subprocess
    from pathlib import Path

    issues = []
    root = Path(workspace_dir or ".").resolve()
    for file_path in changed_files:
        if not file_path.endswith(".py"):
            continue
        full = root / file_path
        if not full.is_file():
            continue
        try:
            result = subprocess.run(
                ["python", "-m", "py_compile", str(full)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip() or result.stdout.strip()
                # Extract line number if present
                line = 0
                for part in stderr.split():
                    if "line" in part.lower():
                        try:
                            line = int("".join(c for c in part if c.isdigit()))
                        except ValueError:
                            pass
                        break
                issues.append({
                    "id": f"SYNTAX-{len(issues)+1}",
                    "severity": "high",
                    "summary": f"Python syntax error in {file_path}: {stderr[:200]}",
                    "file": file_path,
                    "line": line,
                    "category": "code_defect",
                    "direction": "diverges",
                    "evidence": [stderr[:300]],
                })
        except Exception as exc:
            issues.append({
                "id": f"SYNTAX-{len(issues)+1}",
                "severity": "high",
                "summary": f"Could not compile {file_path}: {exc}",
                "file": file_path,
                "category": "code_defect",
                "direction": "diverges",
            })
    return issues


def _emit_validation_done(run):
    """Emit validation events matching the normal flow so UI renders correctly."""
    result = run.validation_result or {}
    yield {"op": "done", "validation_result": result}
