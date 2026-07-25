from __future__ import annotations

from .. import implementation_runner
from .clearifying import queue_clearify
from .task_contract import run_request


def handle(run):
    from .. import chat

    for event in implementation_runner.implementation_stream(
        message=run_request(run),
        analysis=run.analysis,
        patch_plan=run.patch_plan,
        workspace_dir=run.workspace_dir,
    ):
        if event.get("op") == "start" and event.get("event") == "user_question":
            data = event.get("data") or {}
            queue_clearify(
                run,
                data.get("question") or "Which implementation behavior should be used?",
                reason=data.get("reason") or "Implementation requires a product decision.",
                unknown_id=str(data.get("unknown_id") or ""),
            )
            run.transition(chat.ChatState.INVESTIGATING, "Implementation queued a clearify decision.")
            return
        if event.get("op") == "done" and isinstance(event.get("implementation"), dict):
            run.implementation_result = event["implementation"]
            run.changed_files = [str(path) for path in run.implementation_result.get("changed_files", [])]
        yield event
    if run.state == chat.ChatState.IMPLEMENTING and (run.implementation_result or {}).get("patch_applied"):
        run.transition(chat.ChatState.VALIDATING, "Implementation patching completed; starting validation.")
    elif run.state == chat.ChatState.IMPLEMENTING:
        run.transition(chat._chat_finish_state(run), "Implementation stream completed.")
