from __future__ import annotations

from .. import design_planner
from .clearifying import queue_clearify, queue_investigation_unknown
from .task_contract import run_request
from .user_waiting import user_question_event


def handle(run):
    from .. import chat

    previous_plan = run.design_plan
    run.design_plan = None
    run.patch_plan = None
    request = run_request(run)
    for event in design_planner.design_planning_stream(
        message=request,
        analysis=run.analysis,
        investigation=run.last_investigation or {},
        workspace_dir=run.workspace_dir,
        previous_plan=previous_plan,
        revision_mode=run.design_revision_mode,
        revision_context=run.continuation_context,
    ):
        if event.get("op") == "done" and isinstance(event.get("design_plan"), dict):
            run.design_plan = event["design_plan"]
        yield event
    run.design_revision_mode = ""
    if not run.design_plan:
        run.transition(chat._chat_finish_state(run), "Design planning finished without a plan.")
        return
    gap = design_planner.blocking_gap(run.design_plan)
    if gap:
        signature = design_planner.gap_signature(gap)
        run.design_gap_attempts[signature] = run.design_gap_attempts.get(signature, 0) + 1
        if run.design_gap_attempts[signature] > 1:
            run.transition(chat.ChatState.FAILED, "Design gap repeated without new progress.")
            return
        question = gap.get("question") or gap.get("why") or str(gap.get("id") or "")
        reason = gap.get("why") or "Design planning requires more information."
        unknown_id = design_planner.gap_unknown_id(gap)
        if gap.get("type") == "code_fact" or gap.get("resolution_strategy") == "investigate_project":
            queue_investigation_unknown(
                run,
                question,
                reason=reason,
                unknown_id=unknown_id,
            )
            run.design_revision_mode = "gap_resolution"
            run.transition(chat.ChatState.INVESTIGATING, "Design planning queued project investigation.")
            return
        queue_clearify(
            run,
            question,
            reason=reason,
            unknown_id=unknown_id,
            unknown_type=str(gap.get("type") or "product_decision"),
        )
        yield user_question_event(
            run,
            question=question,
            reason=reason,
            unknown_id=unknown_id,
            checkpoint_phase="design_checkpoint",
            resume_state="patch_planning",
            extra={
                "analysis": run.analysis,
                "design_plan": run.design_plan,
            },
        )
        run.transition(chat.ChatState.WAITING_FOR_USER, "Design planning queued a clearify decision.")
        return
    run.transition(chat.ChatState.PATCH_PLANNING, "Design plan has no blocking gaps.")
