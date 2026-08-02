from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4

from . import model_settings, skill_runtime
from .agent_runtime import finish_initial_skill_selection, start_event


class ChatState(StrEnum):
    INITIALIZING = "initializing"
    ANALYZING = "analyzing"
    INVESTIGATING = "investigating"
    WAITING_FOR_USER = "waiting_for_user"
    DESIGNING = "designing"
    PATCH_PLANNING = "patch_planning"
    IMPLEMENTING = "implementing"
    VALIDATING = "validating"
    SAVING_SESSION = "saving_session"
    COMPLETED = "completed"
    FAILED = "failed"


_CHAT_TRANSITIONS = {
    ChatState.INITIALIZING: {ChatState.ANALYZING, ChatState.INVESTIGATING, ChatState.FAILED},
    ChatState.ANALYZING: {ChatState.INVESTIGATING, ChatState.FAILED},
    ChatState.INVESTIGATING: {
        ChatState.INVESTIGATING,
        ChatState.WAITING_FOR_USER,
        ChatState.DESIGNING,
        ChatState.SAVING_SESSION,
        ChatState.COMPLETED,
        ChatState.FAILED,
    },
    ChatState.DESIGNING: {
        ChatState.INVESTIGATING,
        ChatState.WAITING_FOR_USER,
        ChatState.PATCH_PLANNING,
        ChatState.SAVING_SESSION,
        ChatState.COMPLETED,
        ChatState.FAILED,
    },
    ChatState.PATCH_PLANNING: {
        ChatState.ANALYZING,
        ChatState.INVESTIGATING,
        ChatState.PATCH_PLANNING,
        ChatState.IMPLEMENTING,
        ChatState.SAVING_SESSION,
        ChatState.COMPLETED,
        ChatState.FAILED,
    },
    ChatState.IMPLEMENTING: {
        ChatState.INVESTIGATING,
        ChatState.WAITING_FOR_USER,
        ChatState.VALIDATING,
        ChatState.SAVING_SESSION,
        ChatState.COMPLETED,
        ChatState.FAILED,
    },
    ChatState.VALIDATING: {
        ChatState.VALIDATING,
        ChatState.DESIGNING,
        ChatState.INVESTIGATING,
        ChatState.WAITING_FOR_USER,
        ChatState.SAVING_SESSION,
        ChatState.COMPLETED,
        ChatState.FAILED,
    },
    ChatState.SAVING_SESSION: {ChatState.COMPLETED, ChatState.FAILED},
}
_TERMINAL_CHAT_STATES = {ChatState.WAITING_FOR_USER, ChatState.COMPLETED, ChatState.FAILED}


@dataclass
class ChatRun:
    message: str
    context: list[str]
    workspace_dir: str
    max_rounds: int | None = None
    analysis: dict | None = None
    session_id: int | None = None
    state: ChatState = ChatState.INITIALIZING
    session_context: dict = field(default_factory=dict)
    selected_session_context: dict = field(default_factory=dict)
    analyzer_session_context: dict = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    continuation_context: list[str] = field(default_factory=list)
    investigation_observations: list[dict] = field(default_factory=list)
    investigation_grounding_observations: list[dict] = field(default_factory=list)
    investigation_knowledge: list[dict] = field(default_factory=list)
    last_investigation: dict | None = None
    design_plan: dict | None = None
    design_revision_mode: str = ""
    patch_retries: int = 0
    bugfix_readiness: dict | None = None
    continuation_context: list[str] = field(default_factory=list)
    investigation_passes: int = 0
    validation_inconclusive_count: int = 0
    patch_plan: dict | None = None
    implementation_result: dict | None = None
    changed_files: list[str] = field(default_factory=list)
    validation_result: dict | None = None
    answered_task: dict | None = None
    error: str = ""
    transition_events: list[dict] = field(default_factory=list)
    design_gap_attempts: dict[str, int] = field(default_factory=dict)

    def transition(self, next_state: ChatState, reason: str = "") -> None:
        if next_state not in _CHAT_TRANSITIONS.get(self.state, set()):
            raise ValueError(f"invalid chat state transition: {self.state} -> {next_state}")
        previous = self.state
        event = start_event(f"state-{uuid4().hex[:8]}", "state_transition", {
            "from_state": previous.value,
            "to_state": next_state.value,
            "reason": reason.strip(),
        })
        self.state = next_state
        self.transition_events.append(event)

    def pop_transition_events(self) -> list[dict]:
        events = self.transition_events
        self.transition_events = []
        return events


def analyzed_stream(
    message: str,
    context: list[str],
    workspace_dir: str,
    max_rounds: int | None = None,
    analysis: dict | None = None,
    session_id: int | None = None,
) -> Iterator[dict]:
    yield from _chat_events(ChatRun(
        message=message,
        context=context,
        workspace_dir=workspace_dir,
        max_rounds=max_rounds,
        analysis=analysis,
        session_id=session_id,
    ))


def _chat_events(run: ChatRun) -> Iterator[dict]:
    from .status import handlers

    state_handlers = handlers()
    _last_skill_state: ChatState | None = None
    while run.state not in _TERMINAL_CHAT_STATES:
        try:
            with skill_runtime.target_scope(f"state:{run.state.value}"):
                if run.state != _last_skill_state:
                    yield from _select_initial_skills(run)
                    _last_skill_state = run.state
                yield from skill_runtime.pop_events()
                events = state_handlers[run.state](run)
                if events is not None:
                    for event in events:
                        yield from skill_runtime.pop_events()
                        yield event
                yield from skill_runtime.pop_events()
            yield from run.pop_transition_events()
        except Exception as exc:
            run.error = str(exc)
            yield start_event(f"error-{uuid4().hex[:8]}", "output", {
                "content": f"Run failed: {run.error}",
                "streaming": False,
            })
            if run.state not in _TERMINAL_CHAT_STATES and ChatState.FAILED in _CHAT_TRANSITIONS.get(run.state, set()):
                run.transition(ChatState.FAILED, run.error)
                yield from run.pop_transition_events()
            break


def _chat_finish_state(run: ChatRun) -> ChatState:
    _update_goal_status(run)
    return ChatState.SAVING_SESSION if run.session_id and run.last_investigation else ChatState.COMPLETED


def _update_goal_status(run: ChatRun) -> None:
    """Mark goal as completed when all criteria are known and blockers resolved."""
    analysis = run.analysis or {}
    investigation = run.last_investigation or {}
    goal = analysis.get("goal")
    if not isinstance(goal, dict):
        return
    criteria = analysis.get("acceptance_criteria", [])
    if not criteria:
        return
    all_ac_known = all(
        isinstance(ac, dict) and ac.get("status") == "known"
        for ac in criteria
    )
    unknowns = investigation.get("unknowns", [])
    blocking_unknowns = [
        u for u in unknowns
        if isinstance(u, dict) and u.get("blocking")
    ]
    all_blocking_resolved = len(blocking_unknowns) == 0
    step = investigation.get("step_result")
    step_done = isinstance(step, dict) and step.get("next_step") == "done"
    if all_ac_known and all_blocking_resolved and step_done:
        goal["status"] = "completed"


def _select_initial_skills(run: ChatRun) -> Iterator[dict]:
    setting = (
        model_settings.resolve(model_settings.DEFAULT_STAGE)
        or model_settings.resolve(model_settings.EVIDENCE_STAGE)
    )
    if setting is None:
        return
    prompt_text = skill_runtime.initial_selection_prompt(_skill_selection_context(run))
    yield from skill_runtime.pop_events()
    finish_initial_skill_selection(
        setting["provider"],
        setting["model_id"],
        prompt_text,
    )


def _skill_selection_context(run: ChatRun) -> str:
    lines = [
        f"state: {run.state.value}",
        f"user_request: {run.message}",
    ]
    if run.analysis:
        lines.append(f"task_summary: {run.analysis.get('summary') or run.analysis.get('intent', {}).get('summary') or ''}")
        lines.append(f"execution_mode: {run.analysis.get('execution_mode') or ''}")
    if run.last_investigation:
        lines.append(f"investigation_summary: {run.last_investigation.get('summary') or ''}")
    if run.design_plan:
        lines.append(f"design_summary: {run.design_plan.get('summary') or run.design_plan.get('title') or ''}")
    if run.patch_plan:
        lines.append(f"patch_plan_summary: {run.patch_plan.get('summary') or ''}")
    if run.changed_files:
        lines.append("changed_files: " + ", ".join(run.changed_files))
    if run.validation_result:
        lines.append(f"validation_summary: {run.validation_result.get('summary') or ''}")
    return "\n".join(line for line in lines if line.strip())


def stream(request: dict, workspace_dir: str = ".") -> Iterator[dict]:
    message = request.get("message", "").strip()
    if not message:
        raise ValueError("message is required")
    context = request.get("context", [])
    if not isinstance(context, list) or not all(isinstance(path, str) for path in context):
        raise ValueError("context must be an array of file paths")
    max_rounds = request.get("max_rounds")
    if max_rounds is not None:
        max_rounds = min(50, max(0, int(max_rounds)))
    if "answer" in request:
        raise ValueError("chat answer resume is no longer supported; use /api/chat/answer for clearify tool replies")
    return analyzed_stream(
        message,
        context,
        workspace_dir,
        max_rounds=max_rounds,
        session_id=request.get("session_id"),
    )
