from __future__ import annotations

import json

from .. import app_settings
from ..agent_runtime import content_text


def build_light_agent_prompt(message: str, context: list[str], workspace_dir: str, memory_context: str = "") -> str:
    return _json({
        "role": "light_agent",
        "mission": (
            "Act as the foreground coordinator. Understand the request quickly, do the minimum "
            "necessary read-only routing, then either answer or delegate to the legacy state machine."
        ),
        "user_request": message,
        "workspace_dir": workspace_dir,
        "context": context,
        "memory_context": memory_context,
        "decision_policy": {
            "direct_answer": [
                "Use when the answer does not depend on unknown project facts.",
                "Use when existing conversation context is already sufficient.",
            ],
            "cheap_read_only_tools": [
                "Use for narrow routing, locating likely files, or confirming one concrete uncertainty.",
                "Every tool call must resolve a named uncertainty or enable a workflow decision.",
                "Stop when the next read would only make you more comfortable rather than change the decision.",
            ],
            "run_investigation": [
                "Use when the answer requires grounded project investigation but should not write files.",
                "Use for broad evaluations such as project review; do not personally expand every review axis.",
                "The runtime will inject an authored task_analysis argument.",
            ],
            "run_write_loop": [
                "Use only after analysis and investigation are available.",
                "It starts at design; it must not be used as a way to trigger analyzer or investigation.",
            ],
            "run_full_pipeline": [
                "Use for large or multi-sided implementation requests.",
                "Pass only the focused requirement; the legacy task analyzer will author the task contract.",
                "Split a large request into focused requirements only when separate surfaces can be completed independently.",
            ],
        },
        "investigation_principles": [
            "No arbitrary round cap is needed; stop by information value.",
            "Do not turn a broad request into a long local investigation when the delegated investigator is the right tool.",
            "Prefer delegation once the workflow choice is clear.",
            "Do not use direct read-only tools as a substitute for delegated investigation.",
        ],
        "task_state_rules": [
            "Internal fallback task memory is not the user request.",
            "Do not infer execution_mode, scope, or unknowns from a mechanical fallback task.",
            "For run_investigation and run_write_loop, task authoring is handled by the runtime.",
        ],
        "tool_call_requirements": [
            "Before calling tools, put a concise user-visible reason in assistant content.",
            "When a tool schema has a reason or operation_summary field, fill it with the concrete reason for that call.",
            "The reason must state the uncertainty resolved or decision enabled by that call.",
        ],
        "thinking_content_shape": [
            "State your current judgment.",
            "State the remaining uncertainty.",
            "State why the next tool or delegation path is appropriate.",
        ],
    })


def build_task_authoring_prompt(base_analysis: dict, messages: list[dict]) -> str:
    return _json({
        "role": "task_contract_compiler",
        "task": "Compile the light agent's current understanding into a task_analysis contract for the legacy state machine.",
        "rules": [
            "Return only one compact JSON object.",
            "Do not use the mechanical fallback unknown unless it is truly the remaining uncertainty.",
            "Reflect the current user request, selected context, and tool observations.",
            "Use execution_mode=implement only when the next state-machine call should write code.",
            "Do not add or change effort; it is controlled outside this authoring step.",
            "Unknowns must be concrete uncertainties that investigation or design must resolve.",
            _unknown_limit_rule(base_analysis),
            "Merge related uncertainties instead of listing every review axis.",
            "For broad requests such as project evaluation, create a small set of synthesis unknowns rather than exhaustive category unknowns.",
            "For run_investigation, prefer read_only task contracts and do not create implementation-oriented unknowns.",
            "For run_write_loop, focus unknowns on design inputs, files, validation, and patch risks.",
            "Acceptance criteria must describe requested observable output, not just repeat the full user message.",
        ],
        "unknown_priority": [
            "User intent or product decision that cannot be inferred.",
            "Target project facts, architecture paths, or behavior boundaries.",
            "Validation evidence needed to trust the result.",
            "Material risks that affect the answer or implementation.",
        ],
        "acceptance_shape": [
            "A grounded project investigation or answer is produced.",
            "The output covers the requested dimensions without modifying unrelated files.",
            "Open risks and confidence are explicit.",
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
        "conversation": compact_messages(messages),
    })


def compact_messages(messages: list[dict]) -> list[dict]:
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


def _unknown_limit_rule(base_analysis: dict) -> str:
    limit = _unknown_limit(base_analysis)
    if not limit:
        return "Unknowns are unlimited by settings, but still prefer the smallest sufficient set."
    return f"Unknowns must contain at most {limit} items; merge related uncertainties instead of listing more."


def _unknown_limit(base_analysis: dict) -> int:
    effort = str(base_analysis.get("effort") or "").strip().casefold()
    if effort:
        return int(app_settings.get_effort_profile(effort)["unknown_limit"] or 0)
    return int(app_settings.get_task_limit("task_unknowns") or 0)


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
