from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from .. import clearify_runtime, model_settings
from ..agent_runtime import call_model, content_text, start_event

EventSink = Callable[[dict], None]
_EVENT_SINK: ContextVar[EventSink | None] = ContextVar("light_agent_event_sink", default=None)


@contextmanager
def event_sink(sink: EventSink) -> Iterator[None]:
    token = _EVENT_SINK.set(sink)
    try:
        yield
    finally:
        _EVENT_SINK.reset(token)


def mediate_state_question(event: dict, workspace_dir: str) -> dict:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    _emit_tool(
        "light_agent_clearify",
        {"state_question": data},
        "Evaluating delegated clearify question.",
        status="running",
    )
    decision = _decide_state_answer(data, workspace_dir, user_answers=[])
    answers = []
    while _needs_user(decision):
        answers.extend(_ask_user_questions(decision, data))
        decision = _decide_state_answer(data, workspace_dir, user_answers=answers)
    response = str(decision.get("answer") or "").strip()
    if not response:
        response = _ask_user_questions(_fallback_question(data), data)[0]["response"]
    payload = {
        "question": data.get("question") or "",
        "response": response,
    }
    if data.get("id"):
        clearify_runtime.answer(str(data["id"]), payload)
    _emit_tool(
        "light_agent_clearify",
        {"state_question": data, "user_answers": answers},
        json.dumps(payload, ensure_ascii=False),
        status="done",
    )
    return payload


def _decide_state_answer(data: dict, workspace_dir: str, *, user_answers: list[dict]) -> dict:
    setting = model_settings.resolve(model_settings.LIGHT_AGENT)
    if setting is None:
        if user_answers:
            return _answer_from_user_answers(user_answers)
        return _fallback_question(data)
    prompt = {
        "role": "user",
        "content": json.dumps({
            "task": "Answer a delegated state-machine clearify question as the light agent.",
            "rules": [
                "Answer the state question yourself only when you are fully confident.",
                "If product intent, preference, or external knowledge is uncertain, ask the user your own focused questions.",
                "Do not contradict or reinterpret user answers.",
                "Return only JSON.",
            ],
            "workspace_dir": workspace_dir,
            "state_question": data,
            "user_answers": user_answers,
            "response_schema": {
                "answer": "string; set only when fully confident enough to answer the state machine",
                "confidence": "certain|uncertain",
                "questions": [{"question": "string", "reason": "string"}],
            },
        }, ensure_ascii=False),
    }
    assistant = call_model(
        setting["provider"],
        setting["model_id"],
        [{"role": "system", "content": _MEDIATOR_SYSTEM}, prompt],
        tools=[],
        use_skills=False,
    )
    decision = _json_object(content_text(assistant.get("content") or ""))
    if decision is not None:
        return decision
    if user_answers:
        return _answer_from_user_answers(user_answers)
    return _fallback_question(data)


def _ask_user_questions(decision: dict, state_question: dict) -> list[dict]:
    answers = []
    questions = decision.get("questions") if isinstance(decision.get("questions"), list) else []
    if not questions:
        questions = _fallback_question(state_question)["questions"]
    for item in questions:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        question_id = clearify_runtime.create_pending()
        _emit(start_event(question_id, "user_question", {
            "id": question_id,
            "question_id": question_id,
            "question": question,
            "reason": str(item.get("reason") or "").strip(),
            "origin_message": state_question.get("origin_message") or "",
            "checkpoint_phase": "light_agent_clearify",
            "resume_state": "light_agent",
            "light_agent_mediated": True,
            "state_question": state_question,
        }))
        answer = clearify_runtime.wait(question_id)
        answers.append({
            "question": question,
            "response": str(answer.get("response") or answer.get("selected_option_label") or "").strip(),
        })
    return answers


def _emit(event: dict) -> None:
    sink = _EVENT_SINK.get()
    if sink is not None:
        sink(event)


def emit(event: dict) -> None:
    _emit(event)


def _emit_tool(name: str, input_value: dict, output: str, *, status: str) -> None:
    _emit(start_event(f"light-tool-{name}-{uuid4().hex[:8]}", "tool", {
        "name": name,
        "description": "Light agent mediation",
        "status": status,
        "input": json.dumps(input_value, ensure_ascii=False),
        "output": output,
        "open": False,
    }))


def _needs_user(decision: dict) -> bool:
    answer = str(decision.get("answer") or "").strip()
    confidence = str(decision.get("confidence") or "").strip().casefold()
    return not answer or confidence != "certain"


def _fallback_question(data: dict) -> dict:
    question = str(data.get("question") or "Which behavior should I use?").strip()
    return {
        "confidence": "uncertain",
        "questions": [{
            "question": question,
            "reason": str(data.get("reason") or "The delegated workflow needs a product decision.").strip(),
        }],
    }


def _answer_from_user_answers(user_answers: list[dict]) -> dict:
    lines = [
        f"{item.get('question')}: {item.get('response')}"
        for item in user_answers
        if str(item.get("response") or "").strip()
    ]
    return {
        "answer": "\n".join(lines),
        "confidence": "certain",
    }


def _json_object(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


_MEDIATOR_SYSTEM = """You are the light agent mediating a state-machine clarification.
You may answer the delegated state question only when the available context and user answers make it certain.
If uncertain, ask the user focused questions. You may ask multiple questions.
Never contradict, narrow, or distort the user's answer when forming the final state-machine answer.
Return strict JSON only."""
