from __future__ import annotations

import asyncio
import json
import platform
import re
from collections.abc import Iterator
from itertools import count
from uuid import uuid4

from . import app_settings, model_settings, prompt, providers
from .agent.tools import openai_tool_schema
from .agent_runtime import (
    add_usage as _add_usage,
    call_model as _call_model,
    assistant_message as _assistant_message,
    assistant_visible_text as _assistant_visible_text,
    content_text as _content_text,
    empty_usage as _empty_usage,
    start_event,
    tool_error_json,
    usage_delta as _usage_delta,
)
from .tools import registry

FINALIZATION_OUTPUT_TOKENS = 4096
INVESTIGATION_TOOLS = ("glob", "grep", "read", "code_nav", "python_static_check", "websearch", "webfetch", "subagent")
MAX_HYPOTHESIS_VERIFIER_CALLS = 2
DISCOVERY_BATCH_OBSERVATIONS = 3
FINDING_FIELDS = (
    "beliefs",
    "resolutions",
    "new_unknowns",
    "unknowns",
    "user_decisions_required",
    "task_updates",
)


def investigation_stream(
    *,
    message: str,
    analysis: dict,
    context: list[str],
    workspace_dir: str,
    max_rounds: int | None = None,
    findings: list[str] | None = None,
    previous_observations: list[dict] | None = None,
    previous_knowledge: list[dict] | None = None,
) -> Iterator[dict]:
    setting = (
        model_settings.resolve(model_settings.DEFAULT_STAGE)
        or model_settings.resolve(model_settings.EVIDENCE_STAGE)
    )
    if setting is None:
        raise ValueError(
            "No model configured for investigation. Configure a default or evidence model in Providers."
        )

    provider = setting["provider"]
    model = setting["model_id"]
    pricing_rules = providers.get_model_pricing(provider["id"], model)
    usage_total = _empty_usage(pricing_rules)
    max_rounds = app_settings.get_round_limit("investigation_rounds") if max_rounds is None else int(max_rounds or 0)
    run_id = uuid4().hex[:10]
    stage_id = f"{run_id}-stage"
    yield start_event(stage_id, "stage", {
        "name": "investigation",
        "label": "Investigate before patch planning",
        "state": "running",
        "phase": "understand",
        "model": model,
        "context_length": providers.model_context_length(provider["base_url"], provider["api_key"], model),
        "provider": provider["name"],
        "inherited": setting["inherited"],
    })

    messages = [
        {"role": "system", "content": prompt.build_investigation_static(app_settings.get_output_language())},
        {
            "role": "system",
            "content": prompt.build_investigation_context(
                analysis=analysis,
                message=message,
                directory=workspace_dir,
                platform=platform.system(),
                model=model,
                context=context,
                max_rounds=max_rounds,
            ),
        },
        {"role": "user", "content": message},
    ]
    if findings:
        messages.insert(2, {"role": "system", "content": "\n".join(findings)})
    prior_lines = _previous_context(previous_observations, previous_knowledge)
    if prior_lines:
        messages.insert(2, {"role": "system", "content": "\n".join(prior_lines)})
    tools = _investigation_tools()
    final = None
    observations = []
    tool_cache = {}
    recorded_findings = _empty_recorded_findings()
    finalization_reason = "Investigation model stopped before finish_investigation; summarizing observed facts."
    pending_observation_ids: list[str] = []
    hypothesis_verifier_calls = 0

    for round_index in _round_indexes(max_rounds, start=0):
        thinking_id = f"{run_id}-thinking-{round_index}"
        yield start_event(thinking_id, "thinking", {"text": "", "done": False, "open": True})
        current_tools = tools
        current_tool_choice = "required"
        if _recorded_resolves_initial_unknowns(recorded_findings, analysis):
            current_tools = [_finish_tool_schema()]
            current_tool_choice = {"type": "function", "function": {"name": "finish_investigation"}}
        elif len(pending_observation_ids) >= DISCOVERY_BATCH_OBSERVATIONS:
            messages.append({"role": "user", "content": (
                "You have a full unrecorded discovery batch: "
                f"{', '.join(pending_observation_ids[-6:])}. "
                "Call record_investigation_findings before any more discovery. "
                "Then call finish_investigation if the task contract is covered."
            )})
            current_tools = [_record_findings_tool_schema(), _finish_tool_schema()]
        allowed_tool_names = {
            str(((tool.get("function") or {}).get("name")) or "")
            for tool in current_tools
            if isinstance(tool, dict)
        }
        try:
            assistant = _call_model(provider, model, messages, tools=current_tools, tool_choice=current_tool_choice)
        except ValueError as exc:
            reason = str(exc)
            yield {"op": "update", "id": thinking_id, "patch": {
                "text": reason,
                "done": True,
                "open": False,
            }}
            yield start_event(f"{run_id}-provider-checkpoint", "user_question", _provider_checkpoint_question(
                reason,
                origin_message=message,
                analysis_id=analysis.get("id", ""),
            ))
            yield {"op": "update", "id": stage_id, "patch": {"state": "waiting", "phase": "provider_checkpoint"}}
            return
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{round_index}", "usage", {
                "delta": usage,
                "total": usage_total,
            })

        tool_calls = assistant.get("tool_calls") or []
        content = _assistant_visible_text(assistant) or _tool_call_summary(tool_calls)
        messages.append(_assistant_message(assistant))
        yield {"op": "update", "id": thinking_id, "patch": {
            "text": content,
            "done": True,
            "open": bool(tool_calls),
        }}

        if not tool_calls:
            messages.append({"role": "user", "content": (
                "You did not call a tool. Continue by making an actual tool call, "
                "or call finish_investigation if the investigation is complete. "
                "Do not describe intended tool use in prose."
            )})
            continue

        for raw_call in tool_calls:
            call_id = raw_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = raw_call.get("function") or {}
            name = function.get("name") or ""
            try:
                arguments = _tool_arguments(function.get("arguments"))
                if name not in allowed_tool_names:
                    if len(pending_observation_ids) >= DISCOVERY_BATCH_OBSERVATIONS:
                        raise ValueError(
                            f"tool not allowed until the current discovery batch is recorded: {name}. "
                            "Call record_investigation_findings first."
                        )
                    raise ValueError(f"tool not allowed at this investigation step: {name}")
                if name == "record_investigation_findings":
                    _require_control_reason(arguments, name)
                    recorded_findings = _merge_recorded_findings(recorded_findings, arguments)
                    pending_observation_ids.clear()
                    output = json.dumps({
                        "recorded": True,
                        "counts": {field: len(recorded_findings.get(field, [])) for field in FINDING_FIELDS},
                    }, ensure_ascii=False)
                    yield start_event(call_id, "tool", {
                        "name": name,
                        "description": "Record investigation findings",
                        "status": "done",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                    continue
                if name == "finish_investigation":
                    _require_control_reason(arguments, name)
                    final = _finish_payload(
                        _finish_arguments(recorded_findings, arguments),
                        analysis=analysis,
                        observations=observations,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(final, ensure_ascii=False),
                    })
                    break
                if _is_hypothesis_verifier_call(name, arguments):
                    if hypothesis_verifier_calls >= MAX_HYPOTHESIS_VERIFIER_CALLS:
                        raise ValueError(
                            "hypothesis-verifier budget reached. Use recorded evidence, "
                            "python_static_check, grep, read, or code_nav instead, then record findings."
                        )
                    hypothesis_verifier_calls += 1
                cache_key = _tool_cache_key(name, arguments)
                if cache_key in tool_cache:
                    output = tool_cache[cache_key]
                    yield start_event(call_id, _tool_event_type(name), {
                        "name": name,
                        "description": "Investigation tool",
                        "status": "done",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                        "deduplicated": True,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": (
                            "Duplicate tool call. Previous result summary: "
                            f"{_short_observation(output)}. "
                            "Do not repeat this call again; record the finding or choose a different check."
                        ),
                    })
                    continue
                output = yield from _run_tool_stream(name, call_id, arguments, workspace_dir, analysis)
                tool_cache[cache_key] = output
                observations.append(_tool_observation(name, call_id, output))
                pending_observation_ids.append(call_id)
            except Exception as exc:
                output = tool_error_json(exc, name)
                yield start_event(call_id, _tool_event_type(name), {
                    "name": name or "invalid",
                    "description": "Investigation tool",
                    "status": "error",
                    "open": False,
                    "input": function.get("arguments") or "{}",
                    "output": output,
                })
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": output,
            })
        if final is not None:
            break
    else:
        finalization_reason = "Investigation step limit reached. Summarizing observed facts."

    if final is None:
        final = yield from _finalize_investigation(
            provider=provider,
            model=model,
            messages=messages,
            pricing_rules=pricing_rules,
            usage_total=usage_total,
            run_id=run_id,
            analysis=analysis,
            observations=observations,
            recorded_findings=recorded_findings,
            reason=finalization_reason,
        )
    final["observations"] = observations + [
        item for item in final.get("observations", [])
        if isinstance(item, dict)
    ]

    implementation_intent = _wants_implementation(analysis, message)
    yield {"op": "update", "id": stage_id, "patch": {
        "state": "done",
        "phase": "patch_planning_ready" if final.get("ready_for_patch_planning") and implementation_intent else "done",
    }}
    step = _step_result(final, implementation_intent=implementation_intent)
    final["step_result"] = step
    yield start_event(f"{run_id}-step-result", "step_result", step)
    if final.get("task_updates"):
        yield start_event(f"{run_id}-task-update", "task_update", {
            "analysis_id": analysis.get("id", ""),
            "items": final["task_updates"],
        })
    if step["next_step"] == "ask_user":
        yield start_event(f"{run_id}-user-question", "user_question", _user_question(final, step, message, analysis.get("id", "")))
        yield {"op": "done", "investigation": final}
        return
    yield start_event(f"{run_id}-output", "output", {
        "content": _summary(final),
        "streaming": False,
    })
    yield {"op": "done", "investigation": final}


def _investigation_tools() -> list[dict]:
    tools = [
        _investigation_tool_schema(tool.name, tool.description, tool.parameters)
        for name in INVESTIGATION_TOOLS
        if (tool := registry.get(name))
    ]
    tools.append(_record_findings_tool_schema())
    tools.append(_finish_tool_schema())
    return tools


def _round_indexes(limit: int, start: int = 0):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


def _tool_call_summary(tool_calls: list[dict]) -> str:
    items = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        name = function.get("name") or "tool"
        try:
            arguments = _tool_arguments(function.get("arguments"))
        except ValueError:
            arguments = {}
        reason = str(arguments.get("reason") or arguments.get("operation_summary") or "").strip()
        targets = arguments.get("target_unknown_ids") if isinstance(arguments.get("target_unknown_ids"), list) else []
        subject = _tool_call_subject(name, arguments)
        line = f"{name}{subject}"
        if targets:
            line += f" for {', '.join(str(item) for item in targets if str(item).strip())}"
        if reason:
            line += f": {reason}"
        items.append(line)
    if not items:
        return ""
    return "Calling tools:\n" + "\n".join(f"- {item}" for item in items)


def _tool_call_subject(name: str, arguments: dict) -> str:
    if name in {"read", "glob", "grep", "code_nav", "webfetch"}:
        value = arguments.get("path") or arguments.get("pattern") or arguments.get("query") or arguments.get("url")
        if not value and name == "grep":
            patterns = arguments.get("patterns") if isinstance(arguments.get("patterns"), list) else []
            if patterns:
                value = f"{len(patterns)} patterns"
        return f"({value})" if value else ""
    if name == "subagent":
        value = arguments.get("agent") or arguments.get("name")
        return f"({value})" if value else ""
    if name in {"record_investigation_findings", "finish_investigation"}:
        summary = str(arguments.get("summary") or "").strip()
        return f"({summary[:80]})" if summary else ""
    return ""


def _tool_cache_key(name: str, arguments: dict) -> str:
    ignored = {"reason"}
    comparable = {
        key: value
        for key, value in arguments.items()
        if key not in ignored
    }
    return f"{name}:{json.dumps(comparable, ensure_ascii=False, sort_keys=True)}"


def _is_hypothesis_verifier_call(name: str, arguments: dict) -> bool:
    if name != "subagent":
        return False
    agent = str(arguments.get("agent") or arguments.get("name") or "")
    return agent.strip().removeprefix("@").casefold() == "hypothesis-verifier"


def _recorded_resolves_initial_unknowns(recorded: dict, analysis: dict | None) -> bool:
    initial = [item for item in (analysis or {}).get("unknowns", []) if isinstance(item, dict) and item.get("id")]
    if not initial:
        return False
    resolved = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict) and str(item.get("status") or "") in {"resolved", "partially_resolved", "needs_user", "deferred"}
    }
    return all(str(item["id"]) in resolved for item in initial)


def _require_control_reason(arguments: dict, name: str) -> None:
    if not str(arguments.get("reason") or "").strip():
        raise ValueError(f"{name} requires reason")


def _investigation_tool_schema(name: str, description: str, parameters: dict) -> dict:
    schema = json.loads(json.dumps(parameters))
    properties = schema.setdefault("properties", {})
    properties["target_unknown_ids"] = {
        "type": "array",
        "items": {"type": "string"},
        "description": "Task contract unknown IDs this tool call is intended to resolve or reduce.",
    }
    properties["reason"] = {
        "type": "string",
        "description": "One short sentence explaining why this call helps those unknowns.",
    }
    properties["orientation"] = {
        "type": "boolean",
        "description": "True only for a broad first-pass orientation call that cannot yet target a specific unknown.",
    }
    required = schema.setdefault("required", [])
    for field in ("target_unknown_ids", "reason"):
        if field not in required:
            required.append(field)
    return openai_tool_schema(name, description, schema)


def _previous_context(observations: list[dict] | None, knowledge: list[dict] | None) -> list[str]:
    lines = []
    fresh_knowledge = [item for item in knowledge or [] if item.get("fresh", True)]
    fresh_observations = [item for item in observations or [] if item.get("fresh")]
    if fresh_knowledge:
        lines.append("PREVIOUS SUPPORTED KNOWLEDGE:")
        lines.extend(f"- {item.get('id', '')}: {item.get('statement', '')}" for item in fresh_knowledge[:12])
    if fresh_observations:
        lines.append("PREVIOUS OBSERVATIONS:")
        lines.extend(f"- {item.get('id', '')}: {item.get('summary') or item.get('title') or item.get('tool', '')}" for item in fresh_observations[:20])
    return lines


def _record_findings_tool_schema() -> dict:
    return openai_tool_schema(
        "record_investigation_findings",
        "Record grounded investigation findings incrementally before finishing.",
        {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Visible one-sentence reason for recording these findings now.",
                },
                "beliefs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "statement": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": [
                                    "unverified",
                                    "plausible",
                                    "supported",
                                    "strongly_supported",
                                    "runtime_confirmed",
                                    "contradicted",
                                    "invalidated",
                                ],
                            },
                            "evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Exact observation ids/tool_call_ids only, never file paths, line ranges, UI titles, or prose summaries.",
                            },
                        },
                        "required": ["statement", "status"],
                    },
                },
                "resolutions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "unknown_id": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["resolved", "partially_resolved", "needs_user", "deferred"],
                            },
                            "answer": {"type": "string"},
                            "evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Exact observation ids/tool_call_ids only. Prefer belief_ids for summarized conclusions.",
                            },
                            "belief_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Ids of beliefs recorded in this or a prior record_investigation_findings call, e.g. B1.",
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["unknown_id", "status"],
                    },
                },
                "new_unknowns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "question": {"type": "string"},
                            "blocking": {"type": "boolean"},
                            "resolution_strategy": {
                                "type": "string",
                                "enum": ["investigate_project", "ask_user", "deferred"],
                            },
                        },
                        "required": ["id", "question", "blocking", "resolution_strategy"],
                    },
                },
                "user_decisions_required": {"type": "array", "items": {"type": "string"}},
                "unknowns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "question": {"type": "string"},
                            "blocking": {"type": "boolean"},
                            "resolution_strategy": {
                                "type": "string",
                                "enum": ["investigate_project", "ask_user", "deferred"],
                            },
                        },
                        "required": ["id", "question", "blocking", "resolution_strategy"],
                    },
                },
                "task_updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "kind": {
                                "type": "string",
                                "enum": ["goal", "constraint", "hypothesis", "clue", "unknown", "work"],
                            },
                            "text": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["unknown", "known", "deferred", "blocked", "added", "updated"],
                            },
                            "reason": {"type": "string"},
                            "trace": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["text", "status"],
                    },
                },
            },
            "required": ["reason"],
        },
    )


def _finish_tool_schema() -> dict:
    return openai_tool_schema(
        "finish_investigation",
        "Finish investigation using previously recorded findings.",
        {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Visible one-sentence reason why the investigation is complete or must hand off.",
                },
                "summary": {"type": "string"},
                "patch_planning_facts": {"type": "array", "items": {"type": "string"}},
                "patch_planning_context": {"type": "array", "items": {"type": "string"}},
                "recommended_next_step": {
                    "type": "string",
                    "enum": ["patch_planning", "ask_user", "continue_investigation", "done"],
                },
            },
            "required": ["reason", "summary", "recommended_next_step"],
        },
    )


def _finalize_investigation(
    *,
    provider: dict,
    model: str,
    messages: list[dict],
    pricing_rules: list[dict],
    usage_total: dict,
    run_id: str,
    analysis: dict | None = None,
    observations: list[dict] | None = None,
    recorded_findings: dict | None = None,
    reason: str = "Investigation needs a final structured summary.",
) -> Iterator[dict]:
    messages.append({"role": "user", "content": prompt.build_investigation_finalize(reason)})
    last_error = ""
    last_content = ""
    last_arguments: dict | None = None

    attempts = app_settings.get_round_limit("investigation_finalization_attempts")
    for attempt in _round_indexes(attempts, start=0):
        thinking_id = f"{run_id}-thinking-final-{attempt}"
        yield start_event(thinking_id, "thinking", {
            "text": reason,
            "done": False,
            "open": True,
        })
        assistant = _call_model(
            provider,
            model,
            messages,
            tools=[_record_findings_tool_schema(), _finish_tool_schema()],
            max_tokens=FINALIZATION_OUTPUT_TOKENS,
            tool_choice="required",
        )
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-final-{attempt}", "usage", {
                "delta": usage,
                "total": usage_total,
            })

        tool_calls = assistant.get("tool_calls") or []
        content = _assistant_visible_text(assistant) or _tool_call_summary(tool_calls)
        last_content = content or last_content
        record_calls = [
            call for call in tool_calls
            if ((call.get("function") or {}).get("name") == "record_investigation_findings")
        ]
        finish_call = next(
            (call for call in tool_calls if ((call.get("function") or {}).get("name") == "finish_investigation")),
            None,
        )
        replay = {"role": "assistant", "content": assistant.get("content") or ""}
        for key in ("reasoning_content", "reasoning"):
            if assistant.get(key):
                replay[key] = assistant[key]
        if record_calls or finish_call:
            replay["tool_calls"] = record_calls + ([finish_call] if finish_call else [])
        messages.append(replay)
        yield {"op": "update", "id": thinking_id, "patch": {
            "text": content,
            "done": True,
            "open": bool(finish_call),
        }}

        for record_call in record_calls:
            call_id = record_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = record_call.get("function") or {}
            try:
                record_arguments = _tool_arguments(function.get("arguments"))
                _require_control_reason(record_arguments, "record_investigation_findings")
                recorded_findings = _merge_recorded_findings(
                    recorded_findings or _empty_recorded_findings(),
                    record_arguments,
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps({"recorded": True}, ensure_ascii=False),
                })
            except Exception as exc:
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": tool_error_json(exc, "record_investigation_findings"),
                })

        if finish_call:
            call_id = finish_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = finish_call.get("function") or {}
            try:
                last_arguments = _tool_arguments(function.get("arguments"))
                _require_control_reason(last_arguments, "finish_investigation")
                final = _finish_payload(
                    _finish_arguments(recorded_findings or _empty_recorded_findings(), last_arguments),
                    analysis=analysis,
                    observations=observations or [],
                )
            except Exception as exc:
                last_error = f"finish_investigation arguments were invalid: {exc}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": tool_error_json(ValueError(last_error), "finish_investigation"),
                })
            else:
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(final, ensure_ascii=False),
                })
                return final
        elif content:
            last_error = "Investigation finalization must use record_investigation_findings and finish_investigation tool calls."
        else:
            last_error = "Investigation finalization started before finish_investigation was called."

        if attempts <= 0 or attempt < attempts - 1:
            messages.append({
                "role": "user",
                "content": (
                    f"Previous finalization failed: {last_error}\n"
                    "Do not call discovery tools or repeat investigation. "
                    "Fix only the finalization arguments: create/cite belief ids like B1 in "
                    "record_investigation_findings, use those ids in resolution.belief_ids, "
                    "then call finish_investigation with summary and recommended_next_step."
                ),
            })

    if last_arguments:
        try:
            return _finish_payload(
                _finish_arguments(recorded_findings or _empty_recorded_findings(), last_arguments),
                analysis=analysis,
                observations=observations or [],
                repair_conflicts=True,
            )
        except Exception:
            pass

    if last_content and _wants_implementation(analysis):
        return {
            "summary": last_content,
            "ready_for_patch_planning": True,
            "runtime_recovered": True,
            "beliefs": [],
            "open_questions": [],
            "patch_planning_context": [last_content],
            "task_updates": [{
                "id": "runtime-finalization",
                "kind": "unknown",
                "text": "Investigation finalization used the model's written summary after tool finalization failed.",
                "status": "known",
                "reason": last_error or "finish_investigation did not produce a usable result.",
            }],
        }

    return {
        "summary": last_content or "Investigation finalization did not produce a final summary.",
        "ready_for_patch_planning": False,
        "runtime_failure": True,
        "beliefs": [],
        "open_questions": [last_error or "finish_investigation did not produce a usable result."],
        "patch_planning_context": [],
    }


def _run_tool_stream(name: str, call_id: str, arguments: dict, workspace_dir: str, analysis: dict | None = None) -> Iterator[dict]:
    if name not in INVESTIGATION_TOOLS:
        raise ValueError(f"unknown investigation tool: {name or 'tool'}")
    target_unknown_ids = _target_unknown_ids(arguments)
    reason = str(arguments.pop("reason", "") or "").strip()
    orientation = bool(arguments.pop("orientation", False))
    arguments.pop("target_unknown_ids", None)
    _validate_tool_contract(
        name,
        target_unknown_ids=target_unknown_ids,
        reason=reason,
        orientation=orientation,
        analysis=analysis,
    )

    if name == "subagent" and (arguments.get("agent") or arguments.get("name")):
        agent = str(arguments.get("agent") or arguments.get("name"))
        if agent.strip().removeprefix("@").casefold() == "hypothesis-verifier":
            from . import subagents

            task = str(arguments.get("task") or "")
            _reject_batched_hypothesis(task)
            done = {}
            for packet in subagents.run_stream(agent, task, workspace_dir):
                if packet.get("op") == "done":
                    done = packet
                else:
                    yield packet
            done["target_unknown_ids"] = target_unknown_ids
            done["reason"] = reason
            done["orientation"] = orientation
            return json.dumps(done, ensure_ascii=False)

    tool = registry.get(name)
    if tool is None:
        raise ValueError(f"unknown tool: {name}")
    yield start_event(call_id, _tool_event_type(name), {
        "name": name,
        "description": tool.description,
        "status": "running",
        "open": False,
        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
        "output": "",
        "target_unknown_ids": target_unknown_ids,
        "reason": reason,
        "orientation": orientation,
    })
    result = asyncio.run(tool.execute(arguments, {"directory": workspace_dir}))
    yield {"op": "update", "id": call_id, "patch": {
        "status": "error" if result.title.startswith("[error]") else "done",
        "title": result.title,
        "output": result.output,
        "metadata": {
            **result.metadata,
            "target_unknown_ids": target_unknown_ids,
            "reason": reason,
            "orientation": orientation,
        },
    }}
    return json.dumps({
        "tool_call_id": call_id,
        "target_unknown_ids": target_unknown_ids,
        "reason": reason,
        "orientation": orientation,
        "title": result.title,
        "output": result.output,
        "metadata": {
            **result.metadata,
            "target_unknown_ids": target_unknown_ids,
            "reason": reason,
            "orientation": orientation,
        },
    }, ensure_ascii=False)


def _validate_tool_contract(
    name: str,
    *,
    target_unknown_ids: list[str],
    reason: str,
    orientation: bool,
    analysis: dict | None,
) -> None:
    if not reason:
        raise ValueError(f"{name} requires reason")
    known = {
        str(item.get("id") or "").strip()
        for item in (analysis or {}).get("unknowns", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    if not orientation and not target_unknown_ids:
        raise ValueError(f"{name} requires target_unknown_ids unless orientation is true")
    unknown = [item for item in target_unknown_ids if known and item not in known]
    if unknown:
        raise ValueError(f"{name} target_unknown_ids not in task contract: {', '.join(unknown)}")


def _target_unknown_ids(arguments: dict) -> list[str]:
    raw = arguments.get("target_unknown_ids")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [item for value in raw if (item := str(value).strip())]


def _tool_observation(name: str, call_id: str, output: str) -> dict:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        data = {}
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return {
        "id": call_id,
        "tool": name,
        "title": str(data.get("title") or name),
        "summary": _short_observation(data.get("output") or output),
        "target_unknown_ids": data.get("target_unknown_ids") or metadata.get("target_unknown_ids") or [],
        "reason": data.get("reason") or metadata.get("reason") or "",
        "path": metadata.get("path", ""),
        "mtime_ns": metadata.get("mtime_ns"),
        "size": metadata.get("size"),
    }


def _short_observation(value) -> str:
    text = " ".join(str(value or "").split())
    return text[:240]


def _tool_event_type(name: str) -> str:
    if name == "code_nav":
        return "code_nav"
    if name in {"apply_patch", "rollback_patch", "patch_history"}:
        return "patch"
    return "tool"


def _reject_batched_hypothesis(task: str) -> None:
    text = " ".join((task or "").split())
    numbered = sum(1 for marker in ("1.", "2.", "3.", "4.", "5.", "6.", "7.") if marker in text)
    if numbered >= 2:
        raise ValueError(
            "hypothesis-verifier accepts exactly one atomic belief; split this numbered list into separate calls"
        )
    lowered = text.casefold()
    if any(phrase in lowered for phrase in (
        "following clauses",
        "for each clause",
        "all of these",
        "the following hypotheses",
        "verify these",
    )):
        raise ValueError(
            "hypothesis-verifier accepts exactly one atomic belief; verify one belief at a time"
        )


def _tool_arguments(raw: str | None) -> dict:
    try:
        arguments = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid tool JSON: {exc}") from exc
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    return arguments


def _finish_payload(
    arguments: dict,
    *,
    analysis: dict | None = None,
    observations: list[dict] | None = None,
    expected_step: dict | None = None,
    required_items: list[dict] | None = None,
    repair_conflicts: bool = False,
) -> dict:
    repairs: list[str] = []
    explicit_unknowns = _unknowns(arguments.get("unknowns"))
    unknowns = list(explicit_unknowns)
    new_unknowns = _unknowns(arguments.get("new_unknowns"))
    initial_unknowns = _initial_unknowns(analysis)
    user_decisions = _string_list(arguments.get("user_decisions_required"))
    decision_questions = [_decision_question(item) for item in user_decisions]
    known_unknowns = initial_unknowns + unknowns + new_unknowns
    decision_unknowns = [
        {
            "id": _unknown_id_for_question(question, known_unknowns) or f"D{index}",
            "question": question,
            "blocking": True,
            "resolution_strategy": "ask_user",
        }
        for index, question in enumerate(decision_questions, start=1)
    ]
    open_questions = arguments.get("open_questions") if isinstance(arguments.get("open_questions"), list) else []
    open_questions = [
        _decision_question(question) for question in _clean_questions(open_questions)
        if _decision_question(question) not in set(decision_questions)
    ]
    if open_questions and not unknowns and not decision_unknowns:
        unknowns = [
            {
                "id": f"Q{index}",
                "question": question,
                "blocking": True,
                "resolution_strategy": "ask_user",
            }
            for index, question in enumerate(_clean_questions(open_questions), start=1)
        ]
    beliefs = _beliefs(arguments.get("beliefs"))
    resolutions = _resolutions(arguments.get("resolutions"))
    resolutions = _complete_resolutions(resolutions, initial_unknowns, unknowns)
    if repair_conflicts:
        resolutions = _drop_invalid_resolution_refs(resolutions, beliefs, observations or [], repairs)
    else:
        _validate_resolution_refs(resolutions, beliefs, observations or [])
    resolutions = _enforce_resolution_evidence(resolutions, initial_unknowns, strict=not repair_conflicts)
    unresolved = _unresolved_from_resolutions(resolutions, initial_unknowns)
    unknowns = _merge_unknowns(unresolved + unknowns + decision_unknowns + new_unknowns)
    patch_context = _patch_context(arguments.get("patch_planning_facts"), repairs, repair_conflicts)
    if not patch_context:
        patch_context = _patch_context(arguments.get("patch_planning_context"), repairs, repair_conflicts)
    model_ready = bool(arguments.get("ready_for_patch_planning"))
    ready = model_ready
    if expected_step and expected_step.get("next_step") == "write_code" and not ready:
        if not repair_conflicts:
            raise ValueError("finish_investigation conflicts with accepted write_code checkpoint")
        ready = True
        for item in unknowns:
            item["blocking"] = False
            item["resolution_strategy"] = "deferred"
        repairs.append("Deferred blockers that conflicted with accepted write_code checkpoint")
    if any(item["blocking"] for item in unknowns):
        if model_ready and any(item["blocking"] for item in explicit_unknowns) and not repair_conflicts:
            raise ValueError("ready_for_patch_planning conflicts with blocking unknowns")
        ready = False
    _require_items_accounted(required_items, arguments.get("task_updates"), resolutions, repair_conflicts)
    unknowns = _resolve_task_update_conflicts(unknowns, arguments.get("task_updates"), repairs, repair_conflicts)
    if not unknowns and repair_conflicts:
        ready = True
    readiness = _runtime_readiness(
        model_ready=model_ready,
        analysis=analysis,
        initial_unknowns=initial_unknowns,
        resolutions=resolutions,
        unknowns=unknowns,
        patch_context=patch_context,
    )
    ready = readiness["ready"]
    return {
        "summary": str(arguments.get("summary") or "").strip(),
        "ready_for_patch_planning": ready,
        "beliefs": beliefs,
        "open_questions": open_questions,
        "resolutions": resolutions,
        "new_unknowns": new_unknowns,
        "user_decisions_required": user_decisions,
        "unknowns": unknowns,
        "task_updates": _task_updates(arguments.get("task_updates"), beliefs, unknowns, resolutions),
        "patch_planning_context": patch_context,
        "patch_planning_facts": patch_context,
        "recommended_next_step": str(arguments.get("recommended_next_step") or "").strip(),
        "readiness": readiness,
        "protocol_repairs": repairs,
    }


def _empty_recorded_findings() -> dict:
    return {field: [] for field in FINDING_FIELDS}


def _merge_recorded_findings(current: dict, update: dict) -> dict:
    merged = {field: list(current.get(field, [])) for field in FINDING_FIELDS}
    for field in FINDING_FIELDS:
        value = update.get(field)
        if isinstance(value, list):
            merged[field] = _merge_list_by_identity(merged[field], value)
    return merged


def _merge_list_by_identity(left: list, right: list) -> list:
    result = list(left)
    positions: dict[str, int] = {}
    for index, item in enumerate(result):
        key = _identity_key(item)
        if key:
            positions[key] = index
    for item in right:
        key = _identity_key(item)
        if key and key in positions:
            result[positions[key]] = item
        else:
            if key:
                positions[key] = len(result)
            result.append(item)
    return result


def _identity_key(item) -> str:
    if not isinstance(item, dict):
        return str(item)
    for field in ("unknown_id", "id", "text", "statement", "question"):
        value = str(item.get(field) or "").strip()
        if value:
            return f"{field}:{value}"
    return ""


def _finish_arguments(recorded: dict, finish: dict) -> dict:
    combined = {field: list(recorded.get(field, [])) for field in FINDING_FIELDS}
    combined.update({
        "summary": str(finish.get("summary") or "").strip(),
        "ready_for_patch_planning": _recommended_next_step(finish) == "patch_planning",
        "recommended_next_step": _recommended_next_step(finish),
    })
    facts = finish.get("patch_planning_facts")
    context = finish.get("patch_planning_context")
    if facts is not None:
        combined["patch_planning_facts"] = facts
    if context is not None:
        combined["patch_planning_context"] = context
    return combined


def _recommended_next_step(finish: dict) -> str:
    value = str(finish.get("recommended_next_step") or "").strip()
    return value if value in {"patch_planning", "ask_user", "continue_investigation", "done"} else "done"


def _clean_questions(value: list) -> list[str]:
    return [text for item in value if (text := str(item).strip())]


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for raw in value if (item := str(raw).strip())]


def _beliefs(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    allowed = {
        "unverified",
        "plausible",
        "supported",
        "strongly_supported",
        "runtime_confirmed",
        "contradicted",
        "invalidated",
    }
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            continue
        statement = str(raw.get("statement") or raw.get("text") or "").strip()
        status = str(raw.get("status") or "unverified").strip()
        if not statement or status not in allowed:
            continue
        items.append({
            "id": str(raw.get("id") or f"B{index}").strip(),
            "statement": statement,
            "status": status,
            "evidence": _string_list(raw.get("evidence")),
        })
    return items


def _resolutions(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        unknown_id = str(raw.get("unknown_id") or raw.get("id") or "").strip()
        status = str(raw.get("status") or "").strip()
        if status not in {"resolved", "partially_resolved", "needs_user", "deferred"}:
            continue
        if not unknown_id:
            continue
        items.append({
            "unknown_id": unknown_id,
            "status": status,
            "answer": str(raw.get("answer") or "").strip(),
            "evidence": _string_list(raw.get("evidence")),
            "belief_ids": _string_list(raw.get("belief_ids")),
            "reason": str(raw.get("reason") or "").strip(),
        })
    return items


def _validate_resolution_refs(resolutions: list[dict], beliefs: list[dict], observations: list[dict]) -> None:
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    belief_by_id = {
        str(item.get("id") or "").strip(): item
        for item in beliefs
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    usable_belief_status = {"plausible", "supported", "strongly_supported", "runtime_confirmed"}
    for resolution in resolutions:
        missing_evidence = [item for item in resolution.get("evidence", []) if item not in evidence_ids]
        if missing_evidence:
            raise ValueError(
                f"resolution {resolution['unknown_id']} references unknown evidence ids: "
                + ", ".join(missing_evidence)
            )
        missing_beliefs = [item for item in resolution.get("belief_ids", []) if item not in belief_by_id]
        if missing_beliefs:
            raise ValueError(
                f"resolution {resolution['unknown_id']} references unknown belief ids: "
                + ", ".join(missing_beliefs)
            )
        weak_beliefs = [
            item for item in resolution.get("belief_ids", [])
            if belief_by_id[item].get("status") not in usable_belief_status
        ]
        if weak_beliefs:
            raise ValueError(
                f"resolution {resolution['unknown_id']} references unsupported belief ids: "
                + ", ".join(weak_beliefs)
            )


def _drop_invalid_resolution_refs(
    resolutions: list[dict],
    beliefs: list[dict],
    observations: list[dict],
    repairs: list[str],
) -> list[dict]:
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    usable_beliefs = {
        str(item.get("id") or "").strip()
        for item in beliefs
        if isinstance(item, dict)
        and str(item.get("id") or "").strip()
        and item.get("status") in {"plausible", "supported", "strongly_supported", "runtime_confirmed"}
    }
    changed = False
    for resolution in resolutions:
        evidence = [item for item in resolution.get("evidence", []) if item in evidence_ids]
        beliefs = [item for item in resolution.get("belief_ids", []) if item in usable_beliefs]
        changed = changed or evidence != resolution.get("evidence", []) or beliefs != resolution.get("belief_ids", [])
        resolution["evidence"] = evidence
        resolution["belief_ids"] = beliefs
    if changed:
        repairs.append("Dropped invalid resolution references during finalization repair")
    return resolutions


def _runtime_readiness(
    *,
    model_ready: bool,
    analysis: dict | None,
    initial_unknowns: list[dict],
    resolutions: list[dict],
    unknowns: list[dict],
    patch_context: list[str],
) -> dict:
    blockers = [item for item in unknowns if item.get("blocking")]
    reasons = []
    if blockers:
        reasons.append("blocking_unknowns_remain")
    by_id = {item["unknown_id"]: item for item in resolutions}
    for item in initial_unknowns:
        if not item.get("blocking"):
            continue
        resolution = by_id.get(item["id"])
        if not resolution or resolution.get("status") != "resolved":
            reasons.append(f"{item['id']}:not_resolved")
            continue
        if item.get("resolution_strategy") == "investigate_project" and not (
            resolution.get("evidence") or resolution.get("belief_ids")
        ):
            reasons.append(f"{item['id']}:missing_evidence")
    if isinstance(analysis, dict) and analysis.get("acceptance_criteria") and not patch_context:
        reasons.append("missing_patch_planning_facts")
    ready = bool(model_ready and not reasons)
    return {
        "ready": ready,
        "model_ready": model_ready,
        "reasons": reasons,
    }


def _initial_unknowns(analysis: dict | None) -> list[dict]:
    if not isinstance(analysis, dict):
        return []
    value = analysis.get("unknowns")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        return []
    return _unknowns(value)


def _complete_resolutions(resolutions: list[dict], initial_unknowns: list[dict], unknowns: list[dict]) -> list[dict]:
    by_id = {item["unknown_id"]: item for item in resolutions}
    unresolved_ids = {item["id"] for item in unknowns}
    for item in initial_unknowns:
        if item["id"] in by_id:
            continue
        status = "partially_resolved"
        if item.get("resolution_strategy") == "ask_user":
            status = "needs_user"
        elif item.get("resolution_strategy") == "deferred" or not item.get("blocking"):
            status = "deferred"
        if item["id"] in unresolved_ids or item.get("blocking"):
            resolutions.append({
                "unknown_id": item["id"],
                "status": status,
                "answer": "",
                "evidence": [],
                "belief_ids": [],
                "reason": "No explicit resolution was supplied for this task-contract unknown.",
            })
    return resolutions


def _enforce_resolution_evidence(resolutions: list[dict], initial_unknowns: list[dict], *, strict: bool = True) -> list[dict]:
    if not strict:
        return resolutions
    by_id = {item["id"]: item for item in initial_unknowns}
    for resolution in resolutions:
        source = by_id.get(resolution["unknown_id"])
        if not source:
            continue
        if (
            source.get("blocking")
            and source.get("resolution_strategy") == "investigate_project"
            and resolution.get("status") == "resolved"
            and not (resolution.get("evidence") or resolution.get("belief_ids"))
        ):
            resolution["status"] = "partially_resolved"
            resolution["reason"] = resolution.get("reason") or "Resolved codebase facts require evidence or belief references."
    return resolutions


def _unresolved_from_resolutions(resolutions: list[dict], initial_unknowns: list[dict]) -> list[dict]:
    by_id = {item["id"]: item for item in initial_unknowns}
    unresolved = []
    for resolution in resolutions:
        if resolution["status"] == "resolved":
            continue
        source = by_id.get(resolution["unknown_id"], {})
        strategy = "investigate_project"
        if resolution["status"] == "needs_user":
            strategy = "ask_user"
        elif resolution["status"] == "deferred":
            strategy = "deferred"
        question = source.get("question") or _question_from_resolution(resolution)
        unresolved.append({
            "id": resolution["unknown_id"],
            "question": question,
            "blocking": resolution["status"] in {"partially_resolved", "needs_user"} and bool(source.get("blocking", True)),
            "resolution_strategy": strategy,
        })
    return unresolved


def _question_from_resolution(resolution: dict) -> str:
    text = str(resolution.get("answer") or resolution.get("reason") or "").strip()
    if _looks_like_question(text):
        return text
    return "请明确这个实现决策？"


def _decision_question(value: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if _looks_like_question(text):
        return text
    return f"请明确：{text}？"


def _looks_like_question(value: str) -> bool:
    text = str(value or "")
    return "?" in text or "？" in text


def _merge_unknowns(items: list[dict]) -> list[dict]:
    merged = {}
    by_question = {}
    for item in items:
        if not item.get("id") or not item.get("question"):
            continue
        key = _question_key(item["question"])
        existing_id = by_question.get(key)
        if existing_id:
            current = merged[existing_id]
            merged[existing_id] = {
                **current,
                **item,
                "id": existing_id,
                "blocking": bool(current.get("blocking") or item.get("blocking")),
            }
            continue
        merged[item["id"]] = item
        by_question[key] = item["id"]
    return list(merged.values())


def _unknown_id_for_question(question: str, unknowns: list[dict]) -> str:
    key = _question_key(question)
    for item in unknowns:
        if item.get("id") and _question_key(item.get("question", "")) == key:
            return item["id"]
    return ""


def _question_key(value: str) -> str:
    return re.sub(r"\W+", "", str(value or "")).casefold()


def _unknown_id_tail(value: str | None) -> str:
    text = str(value or "").strip()
    return text.rsplit(":", 1)[-1] if ":" in text else text


def _same_unknown_id(left: str | None, right: str | None) -> bool:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text or not right_text:
        return False
    return left_text == right_text or _unknown_id_tail(left_text) == _unknown_id_tail(right_text)


def _is_placeholder_question(value: str | None, unknown_id: str | None = "") -> bool:
    text = " ".join(str(value or "").split())
    if not text:
        return True
    if unknown_id and _same_unknown_id(text, unknown_id):
        return True
    placeholder = "\u8bf7\u660e\u786e\u8fd9\u4e2a\u5b9e\u73b0\u51b3\u7b56"
    return text.startswith(placeholder)


def _task_update_question(final: dict, unknown_id: str | None) -> str:
    if not unknown_id:
        return ""
    for item in final.get("task_updates") or []:
        if not isinstance(item, dict) or not _same_unknown_id(item.get("id"), unknown_id):
            continue
        text = str(item.get("text") or "").strip()
        if not _is_placeholder_question(text, unknown_id):
            return text
    return ""


def _display_question_for_unknown(item: dict | None, final: dict) -> str:
    if not item:
        return ""
    unknown_id = str(item.get("id") or "").strip()
    question = str(item.get("question") or "").strip()
    if not _is_placeholder_question(question, unknown_id):
        return question
    task_question = _task_update_question(final, unknown_id)
    return task_question or question


def _best_ask_user_unknown(final: dict) -> dict | None:
    candidates = [
        item for item in final.get("unknowns", [])
        if isinstance(item, dict)
        and item.get("blocking")
        and item.get("resolution_strategy") == "ask_user"
    ]
    if not candidates:
        return None
    specific = [
        item for item in candidates
        if not _is_placeholder_question(_display_question_for_unknown(item, final), item.get("id"))
    ]
    return specific[0] if specific else candidates[0]


def _patch_context(value, repairs: list[str], repair_conflicts: bool) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        if repair_conflicts:
            repairs.append("Dropped non-array patch planning facts")
            return []
        raise ValueError("patch_planning_context must be an array of strings")
    items = []
    normalized_object = False
    for raw in value:
        if isinstance(raw, dict):
            if not repair_conflicts:
                raise ValueError("patch_planning_context must be an array of strings")
            fact = str(raw.get("fact") or raw.get("text") or raw.get("statement") or "").strip()
            source = str(raw.get("source") or raw.get("evidence") or "").strip()
            if fact:
                items.append(f"{fact} ({source})" if source else fact)
                normalized_object = True
        elif text := str(raw).strip():
            items.append(text)
    if normalized_object:
        repairs.append("Normalized patch_planning_context objects to strings")
    return items


def _require_items_accounted(required_items, task_updates, resolutions, repair_conflicts: bool) -> None:
    if not required_items:
        return
    update_ids = {
        str(item.get("id") or "").strip()
        for item in task_updates or []
        if isinstance(item, dict) and str(item.get("status") or "") in {"known", "deferred", "blocked"}
    }
    resolution_ids = {item["unknown_id"] for item in resolutions}
    missing = [
        str(item.get("id") or "").strip()
        for item in required_items
        if isinstance(item, dict) and str(item.get("id") or "").strip() not in update_ids | resolution_ids
    ]
    if missing and not repair_conflicts:
        raise ValueError("finish_investigation must account for every initial hypothesis/unknown")


def _resolve_task_update_conflicts(
    unknowns: list[dict],
    task_updates,
    repairs: list[str],
    repair_conflicts: bool,
) -> list[dict]:
    known_ids = {
        str(item.get("id") or "").strip()
        for item in task_updates or []
        if isinstance(item, dict) and str(item.get("status") or "").strip() == "known"
    }
    conflicts = [item for item in unknowns if item.get("id") in known_ids]
    if not conflicts:
        return unknowns
    if not repair_conflicts:
        raise ValueError("unknowns should contain only unresolved items")
    repairs.append("Removed unknowns already marked known by task_updates")
    return [item for item in unknowns if item.get("id") not in known_ids]


def _task_updates(value, beliefs, unknowns: list[dict], resolutions: list[dict] | None = None) -> list[dict]:
    updates = []
    if isinstance(value, list):
        for raw in value:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or "").strip()
            status = str(raw.get("status") or "").strip()
            if not text or status not in {"unknown", "known", "deferred", "blocked", "added", "updated"}:
                continue
            trace = raw.get("trace") if isinstance(raw.get("trace"), list) else []
            updates.append({
                "id": str(raw.get("id") or "").strip(),
                "kind": str(raw.get("kind") or "unknown").strip() or "unknown",
                "text": text,
                "status": status,
                "reason": str(raw.get("reason") or "").strip(),
                "trace": [str(item).strip() for item in trace if str(item).strip()][:6],
            })
    known_ids = {item["id"] for item in updates if item["status"] == "known" and item.get("id")}
    for resolution in resolutions or []:
        unknown_id = resolution.get("unknown_id", "")
        if not unknown_id or any(_same_unknown_id(unknown_id, known_id) for known_id in known_ids):
            continue
        status = {
            "resolved": "known",
            "partially_resolved": "unknown",
            "needs_user": "blocked",
            "deferred": "deferred",
        }.get(resolution.get("status"), "unknown")
        if status != "known":
            continue
        updates.append({
            "id": unknown_id,
            "kind": "unknown",
            "text": resolution.get("answer") or unknown_id,
            "status": status,
            "reason": resolution.get("reason", ""),
            "trace": resolution.get("evidence", [])[:6],
        })
        if status == "known":
            known_ids.add(unknown_id)
    for item in unknowns:
        existing_ids = {update.get("id") for update in updates if update.get("id")}
        if any(_same_unknown_id(item.get("id"), existing_id) for existing_id in existing_ids):
            continue
        updates.append({
            "id": item.get("id", ""),
            "kind": "unknown",
            "text": item["question"],
            "status": "blocked" if item.get("blocking") else "deferred",
            "reason": item.get("resolution_strategy", ""),
            "trace": [],
        })
    return updates[:8]


def _step_result(final: dict, *, implementation_intent: bool = True) -> dict:
    if final.get("runtime_failure"):
        return {
            "next_step": "failed",
            "continue_reason": final.get("summary") or "Investigation failed before producing a valid final result.",
            "target_unknown_ids": [],
            "summary": "",
            "beliefs": [],
            "ready_for_patch_planning": False,
            "patch_planning_context": [],
            "resolutions": [],
            "unknowns": [],
        }
    blockers = [item for item in final.get("unknowns", []) if item.get("blocking")]
    investigate = [item for item in blockers if item.get("resolution_strategy") == "investigate_project"]
    ask_user = [item for item in blockers if item.get("resolution_strategy") == "ask_user"]
    if investigate:
        return {
            "next_step": "continue_investigation",
            "continue_reason": "; ".join(item["question"] for item in investigate[:3]),
            "target_unknown_ids": [item["id"] for item in investigate],
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": False,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": final.get("unknowns", []),
        }
    if ask_user:
        item = _best_ask_user_unknown(final) or ask_user[0]
        question = _display_question_for_unknown(item, final)
        if _is_placeholder_question(question, item.get("id")):
            question = next(
                (
                    str(candidate.get("question") or "").strip()
                    for candidate in ask_user
                    if not _is_placeholder_question(candidate.get("question"), candidate.get("id"))
                ),
                question,
            )
        return {
            "next_step": "ask_user",
            "continue_reason": question,
            "target_unknown_ids": [item["id"]],
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": False,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": final.get("unknowns", []),
        }
    if final.get("ready_for_patch_planning") and implementation_intent:
        return {
            "next_step": "write_code",
            "continue_reason": final.get("summary") or app_settings.text("ready_patch"),
            "target_unknown_ids": [],
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": True,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": [],
        }
    if final.get("ready_for_patch_planning") and not implementation_intent:
        return {
            "next_step": "done",
            "continue_reason": final.get("summary") or "Investigation complete.",
            "target_unknown_ids": [],
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": False,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": [],
        }
    open_questions = final.get("open_questions") or []
    question = str(open_questions[0]) if open_questions else ""
    return {
        "next_step": "ask_user" if open_questions else "done",
        "continue_reason": question
        or final.get("summary")
        or "Investigation complete.",
        "target_unknown_ids": [],
        "summary": final.get("summary", "") if open_questions else "",
        "beliefs": final.get("beliefs", []),
        "ready_for_patch_planning": False,
        "patch_planning_context": final.get("patch_planning_context", []),
        "resolutions": final.get("resolutions", []),
        "unknowns": final.get("unknowns", []),
    }


def _wants_implementation(analysis: dict, message: str = "") -> bool:
    if (analysis.get("intent") or {}).get("type") in {"feature", "bugfix", "refactor"}:
        return True
    lowered = " ".join(str(message or "").split()).casefold()
    keywords = (
        "实现", "添加", "增加", "修改", "修复", "支持", "调整", "改成", "变成", "加上", "让", "不要",
        "删除", "移除", "清理", "替换", "优化", "进行修复",
        "create", "add", "implement", "change", "update", "adjust", "make", "set", "do not", "don't",
    )
    return any(word in lowered for word in keywords)


def _provider_checkpoint_question(reason: str, *, origin_message: str = "", analysis_id: str = "") -> dict:
    return {
        "id": f"provider-{uuid4().hex[:8]}",
        "analysis_id": analysis_id,
        "question": "Provider request failed during investigation. Continue this run?",
        "origin_message": origin_message,
        "reason": reason,
        "why_it_matters": "The model provider did not return a usable investigation response.",
        "blocks_next_step": "investigation",
        "unknown_id": "",
        "linked_unknown": {},
        "options": [
            {"id": "continue", "label": "Continue", "value": "Retry the investigation from this checkpoint."},
            {"id": "stop", "label": "Stop", "value": "Stop and keep the current state."},
        ],
        "custom_allowed": False,
        "checkpoint_phase": "investigation_provider_checkpoint",
    }


def _user_question(final: dict, step: dict, origin_message: str = "", analysis_id: str = "") -> dict:
    unknown = _best_ask_user_unknown(final)
    unknown_id = (unknown or {}).get("id", "")
    question = _display_question_for_unknown(unknown, final)
    if not question or question == (unknown or {}).get("id"):
        question = "请明确这个实现决策？"
    if _is_placeholder_question(question, unknown_id):
        fallback_questions = final.get("open_questions") or [step["continue_reason"]]
        question = next(
            (
                str(item).strip()
                for item in fallback_questions
                if not _is_placeholder_question(str(item), unknown_id)
            ),
            "",
        )
    if _is_placeholder_question(question, unknown_id):
        question = "\u8bf7\u660e\u786e\u4e0b\u9762\u8fd9\u4e2a\u5b9e\u73b0\u51b3\u7b56\uff1f"
    options = _question_options(question)
    reason = (unknown or {}).get("why") or ""
    return {
        "id": unknown_id or "question",
        "analysis_id": analysis_id,
        "question": question,
        "origin_message": origin_message,
        "reason": reason,
        "why_it_matters": (
            app_settings.text("resolves_unknown", id=unknown_id)
            if unknown_id
            else app_settings.text("resolves_open")
        ),
        "blocks_next_step": "patch_planning",
        "target_unknown_ids": [unknown_id] if unknown_id else list(step.get("target_unknown_ids") or []),
        "unknown_id": unknown_id,
        "linked_unknown": unknown or {},
        "related_beliefs": _related_dict_items(question, final.get("beliefs") or [], ("statement", "status"), limit=3),
        "related_open_questions": _related_text_items(question, final.get("open_questions") or [], limit=3),
        "patch_planning_context_refs": _related_text_items(
            question,
            final.get("patch_planning_context") or [],
            limit=3,
            fallback=True,
        ),
        "options": options,
        "custom_allowed": True,
    }


def _related_dict_items(question: str, items: list, fields: tuple[str, ...], *, limit: int) -> list[dict]:
    scored: list[tuple[int, int, dict]] = []
    q_tokens = _tokens(question)
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        haystack = " ".join(str(item.get(field) or "") for field in fields)
        score = len(q_tokens & _tokens(haystack))
        if score:
            scored.append((score, -index, item))
    scored.sort(reverse=True)
    return [item for _, _, item in scored[:limit]]


def _related_text_items(question: str, items: list, *, limit: int, fallback: bool = False) -> list[str]:
    q_tokens = _tokens(question)
    scored: list[tuple[int, int, str]] = []
    normalized: list[str] = []
    current = " ".join(str(question or "").split()).casefold()
    for index, item in enumerate(items):
        text = str(item).strip()
        if not text:
            continue
        if " ".join(text.split()).casefold() == current:
            continue
        normalized.append(text)
        score = len(q_tokens & _tokens(text))
        if score:
            scored.append((score, -index, text))
    scored.sort(reverse=True)
    if scored:
        return [item for _, _, item in scored[:limit]]
    return normalized[:limit] if fallback else []


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", str(value).casefold()))


def _question_options(question: str) -> list[dict]:
    text = question.casefold()
    if any(word in text for word in ("keyboard", "trigger", "modifier", "hotkey", "key")):
        return [
            {
                "id": "any_key",
                "label": app_settings.text("any_key_label"),
                "value": app_settings.text("any_key_value"),
            },
            {
                "id": "modifiers_only",
                "label": app_settings.text("modifiers_only_label"),
                "value": app_settings.text("modifiers_only_value"),
                "recommended": True,
            },
            {
                "id": "continue_research",
                "label": app_settings.text("research_more_label"),
                "value": app_settings.text("research_hotkey_value"),
            },
        ]
    if any(word in text for word in ("pyobjc", "quartz", "applescript", "window manipulation")):
        return [
            {
                "id": "pyobjc_quartz",
                "label": app_settings.text("pyobjc_label"),
                "value": app_settings.text("pyobjc_value"),
                "recommended": True,
            },
            {
                "id": "applescript",
                "label": app_settings.text("applescript_label"),
                "value": app_settings.text("applescript_value"),
            },
            {
                "id": "research_more",
                "label": app_settings.text("research_more_label"),
                "value": app_settings.text("research_window_value"),
            },
        ]
    if any(word in text for word in ("password", "hash")):
        return [
            {
                "id": "hash_passwords",
                "label": app_settings.text("hash_passwords_label"),
                "value": app_settings.text("hash_passwords_value"),
                "recommended": True,
            },
            {
                "id": "prototype_only",
                "label": app_settings.text("prototype_only_label"),
                "value": app_settings.text("prototype_only_value"),
            },
            {
                "id": "ask_details",
                "label": app_settings.text("ask_details_label"),
                "value": app_settings.text("ask_details_value"),
            },
        ]
    return [
        {
            "id": "best_practice",
            "label": app_settings.text("best_practice_label"),
            "value": app_settings.text("best_practice_value", question=question),
            "recommended": True,
        },
        {
            "id": "continue_research",
            "label": app_settings.text("research_more_label"),
            "value": app_settings.text("research_decision_value", question=question),
        },
    ]


def _unknowns(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for index, raw in enumerate(value, start=1):
        if isinstance(raw, str):
            text = raw.strip()
            if text:
                items.append({
                    "id": f"U{index}",
                    "question": text,
                    "blocking": True,
                    "resolution_strategy": "investigate_project",
                })
            continue
        if not isinstance(raw, dict):
            continue
        strategy = str(raw.get("resolution_strategy") or "").strip()
        if strategy not in {"investigate_project", "ask_user", "deferred"}:
            continue
        item = {
            "id": str(raw.get("id") or "").strip(),
            "question": str(raw.get("question") or "").strip(),
            "blocking": bool(raw.get("blocking")),
            "resolution_strategy": strategy,
            "type": str(raw.get("type") or "code_fact").strip() or "code_fact",
        }
        if item["id"] and item["question"]:
            items.append(item)
    return items


def _summary(final: dict) -> str:
    lines = [final.get("summary") or "Investigation complete."]
    if final.get("beliefs"):
        lines.append(f"\n{app_settings.text('summary_beliefs')}")
        lines.extend(
            f"- {item.get('status', 'unverified')}: {item.get('statement', '')}"
            for item in final["beliefs"][:8]
        )
    if final.get("open_questions"):
        lines.append(f"\n{app_settings.text('summary_open_questions')}")
        lines.extend(f"- {item}" for item in final["open_questions"][:5])
    if final.get("patch_planning_context"):
        lines.append(f"\n{app_settings.text('summary_patch_context')}")
        lines.extend(f"- {item}" for item in final["patch_planning_context"][:8])
    return "\n".join(lines)
