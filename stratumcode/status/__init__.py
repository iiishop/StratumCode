from __future__ import annotations

from . import (
    analyzing,
    designing,
    implementing,
    initializing,
    investigating,
    patch_planning,
    saving_session,
    validating,
)


def handlers():
    from ..chat import ChatState

    return {
        ChatState.INITIALIZING: initializing.handle,
        ChatState.ANALYZING: analyzing.handle,
        ChatState.INVESTIGATING: investigating.handle,
        ChatState.DESIGNING: designing.handle,
        ChatState.PATCH_PLANNING: patch_planning.handle,
        ChatState.IMPLEMENTING: implementing.handle,
        ChatState.VALIDATING: validating.handle,
        ChatState.SAVING_SESSION: saving_session.handle,
    }
