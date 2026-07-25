from __future__ import annotations

from .. import design_planner
from .clearifying import queue_clearify
from .task_contract import run_request


def handle(run):
    from .. import chat

    run.design_plan = None
    run.patch_plan = None
    request = run_request(run)
    for event in design_planner.design_planning_stream(
        message=request,
        analysis=run.analysis,
        investigation=run.last_investigation or {},
        workspace_dir=run.workspace_dir,
    ):
        if event.get("op") == "done" and isinstance(event.get("design_plan"), dict):
            run.design_plan = event["design_plan"]
        yield event
    if not run.design_plan:
        run.transition(chat._chat_finish_state(run), "Design planning finished without a plan.")
        return
    gap = design_planner.blocking_gap(run.design_plan)
    if gap:
        queue_clearify(
            run,
            gap.get("question") or gap.get("why") or str(gap.get("id") or ""),
            reason=gap.get("why") or "Design planning requires a product decision.",
            unknown_id=str(gap.get("id") or ""),
        )
        run.transition(chat.ChatState.INVESTIGATING, "Design planning queued a clearify decision.")
        return
    run.transition(chat.ChatState.PATCH_PLANNING, "Design plan has no blocking gaps.")
