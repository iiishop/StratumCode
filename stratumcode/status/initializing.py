from __future__ import annotations

from .. import sessions
from .session_memory import _select_session_memory, _session_context
from .user_context import _answer_context, _workspace_snapshot


def handle(run):
    from .. import chat

    try:
        state = sessions.get(run.session_id)["state"] if run.session_id else {}
    except ValueError:
        state = {}
    run.session_context = _session_context(state)
    run.analyzer_session_context = _select_session_memory(run.message, None, run.session_context)
    workspace_context = _workspace_snapshot(run.workspace_dir)
    if workspace_context:
        run.context = workspace_context + run.context
    answer_context = _answer_context(run.answer)
    if answer_context:
        run.context = run.context + answer_context
    if run.analysis is None:
        run.transition(chat.ChatState.ANALYZING, "No prior analysis was supplied.")
    else:
        run.transition(chat.ChatState.PREPARING_INVESTIGATION, "A prior analysis was supplied with the answer.")
