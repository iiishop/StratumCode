from __future__ import annotations

from .. import implementation_runner
from .task_contract import run_request


SKILL_GUIDE = (
    "While implementing, the agent applies the patch plan to real files and "
    "edits code. Best for: code-generation and editing procedures, language- or "
    "framework-specific coding conventions, project-specific style rules, "
    "refactoring playbooks."
)


def handle(run):
    from .. import chat

    if run.patch_plan is None:
        run.transition(chat.ChatState.INVESTIGATING, "Patch plan missing; re-investigate.")
        return
    failed = False
    for event in implementation_runner.implementation_stream(
        message=run_request(run),
        analysis=run.analysis,
        patch_plan=run.patch_plan,
        workspace_dir=run.workspace_dir,
    ):
        if event.get("op") == "done" and isinstance(event.get("implementation"), dict):
            run.implementation_result = event["implementation"]
            run.changed_files = [str(path) for path in run.implementation_result.get("changed_files", [])]
        if event.get("op") == "update" and event.get("patch", {}).get("state") in {"error", "failed"}:
            failed = True
        yield event
    if run.state == chat.ChatState.IMPLEMENTING and failed:
        run.transition(chat.ChatState.FAILED, "Implementation failed before completing the authorized patch plan.")
    elif run.state == chat.ChatState.IMPLEMENTING and (run.implementation_result or {}).get("patch_applied"):
        run.transition(chat.ChatState.VALIDATING, "Implementation patching completed; starting validation.")
    elif run.state == chat.ChatState.IMPLEMENTING:
        run.transition(chat._chat_finish_state(run), "Implementation stream completed.")
