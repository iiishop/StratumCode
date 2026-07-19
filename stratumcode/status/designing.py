from __future__ import annotations

from .. import checkpoint, design_planner
from ..agent_runtime import start_event
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
        question = design_planner.user_question(
            gap,
            analysis_id=run.analysis["id"],
            origin_message=request,
        )
        question.update({
            "checkpoint_phase": "design_checkpoint",
            "design_plan": run.design_plan,
            "investigation": run.last_investigation,
        })
        checkpoint.prepare_question(
            question,
            resume_state=chat.ChatState.PATCH_PLANNING,
            analysis=run.analysis,
            investigation=run.last_investigation,
            design_plan=run.design_plan,
        )
        yield start_event(f"{run.analysis['id']}-design-question", "user_question", question)
        run.transition(chat.ChatState.WAITING_FOR_USER, "Design planning needs user input.")
    else:
        run.transition(chat.ChatState.PATCH_PLANNING, "Design plan has no blocking gaps.")
