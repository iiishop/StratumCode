from __future__ import annotations

from .. import patch_planner
from .task_contract import run_request


SKILL_GUIDE = (
    "While patch planning, the agent turns verified findings into a concrete "
    "patch plan with file changes, decision slots, and skip reviews. Best for: "
    "patch-planning procedures, change-impact analysis, safe-minimal-edit "
    "conventions, plan review checklists."
)


def handle(run):
    from .. import chat

    run.patch_plan = None
    failed = False
    next_state = ""
    next_reason = ""
    repair_needed = False
    for event in patch_planner.patch_planning_stream(
        message=run_request(run),
        analysis=run.analysis,
        investigation=run.last_investigation,
        design_plan=run.design_plan,
        workspace_dir=run.workspace_dir,
    ):
        if event.get("op") == "done" and isinstance(event.get("patch_plan"), dict):
            run.patch_plan = event["patch_plan"]
            repair_needed = bool(event.get("repair_needed"))
        if event.get("op") == "done" and event.get("next_state"):
            next_state = str(event.get("next_state") or "")
            next_reason = str(event.get("reason") or "")
        if event.get("op") == "update" and event.get("patch", {}).get("state") == "error":
            failed = True
        yield event
    if run.patch_plan:
        if repair_needed:
            MAX_PATCH_RETRIES = 2
            if run.patch_retries < MAX_PATCH_RETRIES:
                run.patch_retries += 1
                issues = run.patch_plan.get("_repair_issues", [])
                hint = "; ".join(issues[:3]) if issues else "fix validation issues"
                run.transition(chat.ChatState.PATCH_PLANNING, f"Patch plan needs repair ({run.patch_retries}/{MAX_PATCH_RETRIES}): {hint}")
                return
            run.transition(chat.ChatState.IMPLEMENTING, f"Patch plan has minor issues after {MAX_PATCH_RETRIES} repairs; proceeding anyway.")
        else:
            run.patch_retries = 0
            run.transition(chat.ChatState.IMPLEMENTING, "Patch plan is ready.")
    elif next_state == "analyzing":
        run.transition(chat.ChatState.ANALYZING, next_reason or "Patch planning requires task re-analysis.")
    elif next_state == "investigating":
        run.design_revision_mode = "grounding"
        if next_reason and next_reason not in run.continuation_context:
            run.continuation_context.append(f"Patch planning feedback: {next_reason}")
        run.transition(chat.ChatState.INVESTIGATING, next_reason or "Patch planning requires more project evidence.")
    elif failed:
        run.transition(chat.ChatState.FAILED, "Patch planning failed before producing a patch plan.")
    else:
        run.transition(chat._chat_finish_state(run), "Patch planning finished without a patch plan.")
