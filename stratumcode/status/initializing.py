from __future__ import annotations

from .. import memory_system, sessions
from .investigating_refactored import prepare_investigation
from .session_memory import _select_session_memory, _session_context
from .user_context import _workspace_snapshot

SKILL_TARGET = False


def handle(run):
    from .. import chat

    try:
        state = sessions.get(run.session_id)["state"] if run.session_id else {}
    except ValueError:
        state = {}
    memory_snapshot = memory_system.select(
        workspace_dir=run.workspace_dir,
        session_id=run.session_id,
        query=run.message,
        analysis=run.analysis,
        scopes=("turn", "session", "project"),
        token_budget=3500,
    )
    run.session_context = memory_snapshot.to_legacy_context() or _session_context(state)
    run.analyzer_session_context = _select_session_memory(run.message, None, run.session_context)
    workspace_context = _workspace_snapshot(run.workspace_dir)
    if workspace_context:
        run.context = workspace_context + run.context
    memory_context = memory_system.render_snapshot(memory_snapshot, consumer="analyzer")
    if memory_context:
        run.context.append(memory_context)
    if run.analysis is None:
        run.transition(chat.ChatState.ANALYZING, "No prior analysis was supplied.")
    else:
        yield from prepare_investigation(run)
        run.transition(chat.ChatState.INVESTIGATING, "A prior analysis was supplied with the answer.")
