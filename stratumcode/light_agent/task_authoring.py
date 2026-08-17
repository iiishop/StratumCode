from __future__ import annotations

import json
import re
from copy import deepcopy

from ..agent_runtime import call_model, content_text
from ..status import task_analysis
from ..status.task_updates import _normalize_task_updates, _seed_task_updates
from .prompting import build_task_authoring_prompt


def author_task_analysis(
    *,
    base_analysis: dict,
    messages: list[dict],
    provider: dict,
    model: str,
) -> tuple[dict, dict]:
    assistant = call_model(
        provider,
        model,
        [{"role": "user", "content": build_task_authoring_prompt(base_analysis, messages)}],
        tools=[],
        use_skills=False,
    )
    authored = _parse_task_analysis(content_text(assistant.get("content") or ""))
    authored["id"] = base_analysis.get("id", "")
    authored["origin_message"] = base_analysis.get("origin_message", "")
    authored["source"] = "light_agent"
    if base_analysis.get("effort"):
        authored["effort"] = base_analysis["effort"]
    else:
        authored.pop("effort", None)
    authored = task_analysis._validate_task_analysis(authored)
    authored["id"] = base_analysis.get("id", authored.get("id", ""))
    authored["origin_message"] = base_analysis.get("origin_message", authored.get("origin_message", ""))
    authored["source"] = "light_agent"
    authored["task_updates"] = _normalize_task_updates(
        authored["id"],
        _seed_task_updates(authored),
        [],
    )
    return authored, assistant

def contextual_fallback_task_analysis(
    *,
    base_analysis: dict,
    messages: list[dict],
    tool_name: str,
) -> dict:
    analysis = deepcopy(base_analysis)
    request = _latest_user_request(messages) or str(base_analysis.get("origin_message") or "").strip()
    request = request or str((base_analysis.get("intent") or {}).get("summary") or "Handle the current request.").strip()
    summary = _contextual_summary(messages, request)
    execution_mode = "implement" if tool_name == "run_write_loop" else "read_only"
    analysis.update({
        "intent": {
            "type": "investigation" if execution_mode == "read_only" else "other",
            "summary": summary,
        },
        "execution_mode": execution_mode,
        "acceptance_criteria": [_fallback_acceptance(request, execution_mode)],
        "constraints": _fallback_constraints(execution_mode),
        "scope": _fallback_scope(request, execution_mode),
        "behavior_contract": {
            "inputs": ["Current user request", "Light agent reasoning", "Available tool observations"],
            "outputs": ["Grounded legacy state-machine task result"],
            "success_behaviors": ["The legacy state machine receives a task contract specific to the current request."],
            "failure_behaviors": ["Unresolved project facts remain explicit as investigation unknowns."],
            "boundaries": ["Do not publish the mechanical initial fallback task after task authoring fails."],
        },
        "unknowns": [_fallback_unknown(request, execution_mode)],
        "hypotheses": _fallback_hypotheses(messages),
        "clues": _fallback_clues(messages, base_analysis),
        "source": "light_agent",
    })
    analysis = task_analysis._validate_task_analysis(analysis)
    analysis["id"] = base_analysis.get("id", analysis.get("id", ""))
    analysis["origin_message"] = base_analysis.get("origin_message", analysis.get("origin_message", ""))
    analysis["source"] = "light_agent"
    analysis["task_updates"] = _normalize_task_updates(
        analysis["id"],
        _seed_task_updates(analysis),
        [],
    )
    return analysis


def _latest_user_request(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            text = content_text(message.get("content"))
            if text.strip():
                return _compact_text(text, 220)
    return ""


def _contextual_summary(messages: list[dict], request: str) -> str:
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        text = content_text(message.get("content")) or content_text(message.get("reasoning_content"))
        sentence = _first_sentence(text)
        if sentence:
            return sentence
    return _compact_text(request, 160)


def _fallback_acceptance(request: str, execution_mode: str) -> dict:
    if execution_mode == "implement":
        text = f"Implement the requested change with validation evidence for: {_compact_text(request, 160)}"
    else:
        text = f"Produce a grounded answer or investigation result for: {_compact_text(request, 170)}"
    return {"id": "AC1", "text": text}


def _fallback_constraints(execution_mode: str) -> list[str]:
    constraints = ["Reuse light agent observations before starting broad discovery."]
    if execution_mode == "read_only":
        constraints.append("Do not modify files for this state-machine call.")
    return constraints


def _fallback_scope(request: str, execution_mode: str) -> dict:
    scope_out = ["Unrelated project areas"]
    if execution_mode == "read_only":
        scope_out.append("File writes")
    return {
        "in": [f"Current request: {_compact_text(request, 180)}"],
        "out": scope_out,
        "undecided": [],
    }


def _fallback_unknown(request: str, execution_mode: str) -> dict:
    if execution_mode == "implement":
        question = f"What exact files, code paths, and validation checks are required for: {_compact_text(request, 140)}?"
    else:
        question = f"What project facts, architecture paths, and risks are needed to answer: {_compact_text(request, 140)}?"
    return {
        "id": "U1",
        "question": question,
        "blocking": True,
        "type": "code_fact",
        "why": "The legacy state machine needs a concrete project-grounded target before it can proceed reliably.",
        "resolution_strategy": "investigate_project",
        "acceptance_criteria_ids": ["AC1"],
    }


def _fallback_hypotheses(messages: list[dict]) -> list[dict]:
    summary = _contextual_summary(messages, "")
    if not summary:
        return []
    return [{"text": summary, "certainty": "uncertain"}]


def _fallback_clues(messages: list[dict], base_analysis: dict) -> list[dict]:
    clues = [item for item in base_analysis.get("clues", []) if isinstance(item, dict)]
    for message in reversed(messages[-8:]):
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else {}
            name = str((function or {}).get("name") or "").strip()
            if name:
                clues.append({"kind": "other", "value": f"light agent tool call: {name}", "path": ""})
        if clues:
            break
    return clues[:6]


def _first_sentence(text: str) -> str:
    text = _compact_text(text, 500)
    if not text:
        return ""
    parts = [part.strip(" :-") for part in re.split(r"[\u3002.!?\n\r]+", text) if part.strip(" :-")]
    return _compact_text(parts[0], 160) if parts else ""


def _compact_text(text: str, limit: int) -> str:
    compact = " ".join(str(text or "").split()).strip()
    return compact[:limit]


def _parse_task_analysis(raw: str) -> dict:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("task analysis must be an object")
    return parsed
