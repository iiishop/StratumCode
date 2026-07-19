from __future__ import annotations

from . import (
    analyzing,
    designing,
    implementing,
    initializing,
    investigating,
    patch_planning,
    preparing_investigation,
    saving_session,
    validating,
    waiting_for_user,
)


def handlers():
    from ..chat import ChatState

    return {
        ChatState.INITIALIZING: initializing.handle,
        ChatState.ANALYZING: analyzing.handle,
        ChatState.PREPARING_INVESTIGATION: preparing_investigation.handle,
        ChatState.INVESTIGATING: investigating.handle,
        ChatState.DESIGNING: designing.handle,
        ChatState.WAITING_FOR_USER: waiting_for_user.handle,
        ChatState.PATCH_PLANNING: patch_planning.handle,
        ChatState.IMPLEMENTING: implementing.handle,
        ChatState.VALIDATING: validating.handle,
        ChatState.SAVING_SESSION: saving_session.handle,
    }
