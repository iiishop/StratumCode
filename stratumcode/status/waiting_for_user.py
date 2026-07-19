from __future__ import annotations

from .. import checkpoint
from .user_context import _design_plan_with_answer


def handle(run):
    from .. import chat

    answer = run.answer or {}
    resume_state = checkpoint.answer_resume_state(answer, chat.ChatState, chat._CHAT_TRANSITIONS)
    if answer.get("selected_option_id") == "stop":
        run.answer = None
        run.transition(chat._chat_finish_state(run), "User stopped at checkpoint.")
        return
    checkpoint.restore_context(run, answer)
    run.answer = None
    if answer.get("selected_option_id") == "continue_investigation":
        run.transition(chat.ChatState.INVESTIGATING, "User requested more investigation.")
        return
    if resume_state == chat.ChatState.PATCH_PLANNING:
        run.design_plan = _design_plan_with_answer(run.design_plan or {}, answer)
    run.transition(resume_state or chat._chat_finish_state(run), "User answered; resuming run.")
