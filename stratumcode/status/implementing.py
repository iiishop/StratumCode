from __future__ import annotations

from .. import implementation_runner
from ..agent_runtime import start_event
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
            question = (event.get("data") or {}).get("question") or "Implementation requested a legacy checkpoint."
            yield start_event(f"{event.get('id', 'implementation-question')}-unsupported", "output", {
                "content": f"Legacy checkpoint is disabled: {question}",
                "streaming": False,
            })
            run.transition(chat.ChatState.FAILED, "Implementation emitted a legacy user question.")
            return
        if event.get("op") == "done" and isinstance(event.get("implementation"), dict):
            run.implementation_result = event["implementation"]
            run.changed_files = [str(path) for path in run.implementation_result.get("changed_files", [])]
        yield event
    if run.state == chat.ChatState.IMPLEMENTING and (run.implementation_result or {}).get("patch_applied"):
        run.transition(chat.ChatState.VALIDATING, "Implementation patching completed; starting validation.")
    elif run.state == chat.ChatState.IMPLEMENTING:
        run.transition(chat._chat_finish_state(run), "Implementation stream completed.")
