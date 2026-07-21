from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4

from .agent_runtime import start_event


class ChatState(StrEnum):
    INITIALIZING = "initializing"
    ANALYZING = "analyzing"
    INVESTIGATING = "investigating"
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
    ChatState.INVESTIGATING: {ChatState.INVESTIGATING, ChatState.DESIGNING, ChatState.SAVING_SESSION, ChatState.COMPLETED, ChatState.FAILED},
    ChatState.DESIGNING: {ChatState.PATCH_PLANNING, ChatState.SAVING_SESSION, ChatState.COMPLETED},
    ChatState.PATCH_PLANNING: {ChatState.IMPLEMENTING, ChatState.SAVING_SESSION, ChatState.COMPLETED},
    ChatState.IMPLEMENTING: {ChatState.VALIDATING, ChatState.SAVING_SESSION, ChatState.COMPLETED},
    ChatState.VALIDATING: {ChatState.DESIGNING, ChatState.INVESTIGATING, ChatState.SAVING_SESSION, ChatState.COMPLETED, ChatState.FAILED},
    ChatState.SAVING_SESSION: {ChatState.COMPLETED, ChatState.FAILED},
}
_TERMINAL_CHAT_STATES = {ChatState.COMPLETED, ChatState.FAILED}


@dataclass
class ChatRun:
    message: str
    context: list[str]
    workspace_dir: str
    max_rounds: int | None = None
    answer: dict | None = None
    analysis: dict | None = None
    prior_analysis: dict | None = None
    session_id: int | None = None
    state: ChatState = ChatState.INITIALIZING
    session_context: dict = field(default_factory=dict)
    selected_session_context: dict = field(default_factory=dict)
    analyzer_session_context: dict = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    continuation_context: list[str] = field(default_factory=list)
    investigation_observations: list[dict] = field(default_factory=list)
    investigation_knowledge: list[dict] = field(default_factory=list)
    last_investigation: dict | None = None
    design_plan: dict | None = None
    patch_plan: dict | None = None
    implementation_result: dict | None = None
    changed_files: list[str] = field(default_factory=list)
    validation_result: dict | None = None
    answered_task: dict | None = None
    error: str = ""
    transition_events: list[dict] = field(default_factory=list)
    awaiting_user: bool = False

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
    answer: dict | None = None,
    analysis: dict | None = None,
    prior_analysis: dict | None = None,
    session_id: int | None = None,
) -> Iterator[dict]:
    yield from _chat_events(ChatRun(
        message=message,
        context=context,
        workspace_dir=workspace_dir,
        max_rounds=max_rounds,
        answer=answer,
        analysis=analysis,
        prior_analysis=prior_analysis,
        session_id=session_id,
    ))


def _chat_events(run: ChatRun) -> Iterator[dict]:
    from .status import handlers

    state_handlers = handlers()
    while run.state not in _TERMINAL_CHAT_STATES:
        try:
            events = state_handlers[run.state](run)
            if events is not None:
                yield from events
            yield from run.pop_transition_events()
            if run.awaiting_user:
                break
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
    return ChatState.SAVING_SESSION if run.session_id and run.last_investigation else ChatState.COMPLETED


def stream(request: dict, workspace_dir: str = ".") -> Iterator[dict]:
    from . import checkpoint

    message = request.get("message", "").strip()
    if not message:
        raise ValueError("message is required")
    context = request.get("context", [])
    if not isinstance(context, list) or not all(isinstance(path, str) for path in context):
        raise ValueError("context must be an array of file paths")
    max_rounds = request.get("max_rounds")
    if max_rounds is not None:
        max_rounds = min(50, max(0, int(max_rounds)))
    answer = request.get("answer") if isinstance(request.get("answer"), dict) else None
    prior_analysis = request.get("analysis") if answer and isinstance(request.get("analysis"), dict) else None
    analysis = prior_analysis if answer else None
    if answer and str(answer.get("origin_message") or "").strip():
        message = str(answer["origin_message"]).strip()
    if answer:
        answer = dict(answer)
        resume_state = checkpoint.answer_resume_state(answer, ChatState, _CHAT_TRANSITIONS)
        answer["resume_state"] = str(answer.get("resume_state") or (resume_state or "")).strip()
        if resume_state:
            if analysis and "analysis" not in answer:
                answer["analysis"] = analysis
            run = ChatRun(
                message=message,
                context=context,
                workspace_dir=workspace_dir,
                max_rounds=max_rounds,
                answer=answer,
                analysis=analysis,
                prior_analysis=prior_analysis,
                session_id=request.get("session_id"),
                state=resume_state,
            )
            checkpoint.resume_from_answer(run, answer, ChatState, _CHAT_TRANSITIONS)
            return _chat_events(run)
    return analyzed_stream(
        message,
        context,
        workspace_dir,
        max_rounds=max_rounds,
        answer=answer,
        analysis=analysis,
        prior_analysis=prior_analysis,
        session_id=request.get("session_id"),
    )
