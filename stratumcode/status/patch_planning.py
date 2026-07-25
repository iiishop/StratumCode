from __future__ import annotations

from .. import patch_planner
from .task_contract import run_request


def handle(run):
    from .. import chat

    run.patch_plan = None
    failed = False
    for event in patch_planner.patch_planning_stream(
        message=run_request(run),
        analysis=run.analysis,
        investigation=run.last_investigation,
        design_plan=run.design_plan,
        workspace_dir=run.workspace_dir,
    ):
        if event.get("op") == "done" and isinstance(event.get("patch_plan"), dict):
            run.patch_plan = event["patch_plan"]
        if event.get("op") == "update" and event.get("patch", {}).get("state") == "error":
            failed = True
        yield event
    if run.patch_plan:
        run.transition(chat.ChatState.IMPLEMENTING, "Patch plan is ready.")
    elif failed:
        run.transition(chat.ChatState.FAILED, "Patch planning failed before producing a patch plan.")
    else:
        run.transition(chat._chat_finish_state(run), "Patch planning finished without a patch plan.")
