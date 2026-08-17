from __future__ import annotations

import json

from ..agent_runtime import call_model, content_text
from ..status import task_analysis
from ..status.task_updates import _normalize_task_updates, _seed_task_updates


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
        [{"role": "user", "content": _prompt(base_analysis, messages)}],
        tools=[],
        use_skills=False,
    )
    authored = _parse_task_analysis(content_text(assistant.get("content") or ""))
    authored["id"] = base_analysis.get("id", "")
    authored["origin_message"] = base_analysis.get("origin_message", "")
    authored["source"] = "light_agent"
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


def _prompt(base_analysis: dict, messages: list[dict]) -> str:
    return json.dumps({
        "task": "Author a task_analysis contract for the legacy state machine from the light agent's current understanding.",
        "rules": [
            "Return only one compact JSON object.",
            "Do not use the mechanical fallback unknown unless it is truly the remaining uncertainty.",
            "Reflect the current user request, selected context, and tool observations.",
            "Use execution_mode=implement only when the next state-machine call should write code.",
            "Unknowns must be concrete uncertainties that investigation or design must resolve.",
            "Acceptance criteria must describe the requested observable outcome, not just repeat the full user message.",
        ],
        "schema": {
            "intent": {"type": "feature|bugfix|refactor|question|investigation|other", "summary": "string"},
            "execution_mode": "implement|read_only",
            "acceptance_criteria": [{"id": "AC1", "text": "string"}],
            "constraints": ["string"],
            "scope": {"in": ["string"], "out": ["string"], "undecided": ["string"]},
            "behavior_contract": {
                "inputs": ["string"],
                "outputs": ["string"],
                "success_behaviors": ["string"],
                "failure_behaviors": ["string"],
                "boundaries": ["string"],
            },
            "unknowns": [{
                "id": "U1",
                "question": "string",
                "blocking": True,
                "type": "code_fact|doc_fact|runtime_fact|product_decision|engineering_decision|risk",
                "why": "string",
                "resolution_strategy": "investigate_project|clearify|deferred",
                "acceptance_criteria_ids": ["AC1"],
            }],
            "hypotheses": [{"text": "string", "certainty": "certain|uncertain|guess"}],
            "clues": [{"kind": "file|line|symbol|route|other", "value": "string", "path": "string"}],
        },
        "base_analysis": base_analysis,
        "conversation": _compact_messages(messages),
    }, ensure_ascii=False)


def _compact_messages(messages: list[dict]) -> list[dict]:
    compact = []
    for message in messages[-12:]:
        role = str(message.get("role") or "")
        if role == "assistant":
            compact.append({
                "role": role,
                "content": content_text(message.get("content")),
                "tool_calls": [
                    {
                        "name": ((call.get("function") or {}).get("name") or ""),
                        "arguments": ((call.get("function") or {}).get("arguments") or "")[:1200],
                    }
                    for call in message.get("tool_calls") or []
                    if isinstance(call, dict)
                ],
            })
        elif role == "tool":
            compact.append({
                "role": role,
                "tool_call_id": message.get("tool_call_id", ""),
                "content": content_text(message.get("content"))[:4000],
            })
        elif role == "user":
            compact.append({"role": role, "content": content_text(message.get("content"))[:4000]})
    return compact


def _parse_task_analysis(raw: str) -> dict:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("task analysis must be an object")
    return parsed
