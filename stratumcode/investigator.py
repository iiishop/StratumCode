from __future__ import annotations

import asyncio
import json
import platform
import re
from collections.abc import Iterator
from uuid import uuid4

from . import model_settings, prompt, providers
from .agent.tools import openai_tool_schema
from .agent_runtime import (
    add_usage as _add_usage,
    call_model as _call_model,
    content_text as _content_text,
    empty_usage as _empty_usage,
    start_event,
    tool_error_json,
    usage_delta as _usage_delta,
)
from .tools import registry

MAX_INVESTIGATION_ROUNDS = 10
MAX_FINALIZATION_ATTEMPTS = 2
FINALIZATION_OUTPUT_TOKENS = 4096
INVESTIGATION_TOOLS = ("glob", "grep", "read", "websearch", "webfetch", "subagent")


def investigation_stream(
    *,
    message: str,
    analysis: dict,
    context: list[str],
    workspace_dir: str,
    max_rounds: int | None = None,
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
    max_rounds = max_rounds or MAX_INVESTIGATION_ROUNDS
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
        {"role": "system", "content": prompt.build_investigation_static()},
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
    tools = _investigation_tools()
    final = None

    for round_index in range(max_rounds):
        thinking_id = f"{run_id}-thinking-{round_index}"
        yield start_event(thinking_id, "thinking", {"text": "", "done": False, "open": True})
        assistant = _call_model(provider, model, messages, tools=tools)
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{round_index}", "usage", {
                "delta": usage,
                "total": usage_total,
            })

        content = _content_text(assistant.get("content"))
        tool_calls = assistant.get("tool_calls") or []
        messages.append({
            "role": "assistant",
            "content": assistant.get("content") or "",
            **({"tool_calls": tool_calls} if tool_calls else {}),
        })
        yield {"op": "update", "id": thinking_id, "patch": {
            "text": content,
            "done": True,
            "open": bool(tool_calls),
        }}

        if not tool_calls:
            if content:
                final = {"summary": content, "ready_for_patch_planning": False}
            break

        for raw_call in tool_calls:
            call_id = raw_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = raw_call.get("function") or {}
            name = function.get("name") or ""
            try:
                arguments = _tool_arguments(function.get("arguments"))
                if name == "finish_investigation":
                    final = _finish_payload(arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(final, ensure_ascii=False),
                    })
                    break
                output = yield from _run_tool_stream(
                    name,
                    call_id,
                    arguments,
                    workspace_dir,
                )
            except Exception as exc:
                output = tool_error_json(exc, name)
                yield start_event(call_id, "tool", {
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

    if final is None:
        final = yield from _finalize_investigation(
            provider=provider,
            model=model,
            messages=messages,
            pricing_rules=pricing_rules,
            usage_total=usage_total,
            run_id=run_id,
        )

    yield {"op": "update", "id": stage_id, "patch": {
        "state": "done",
        "phase": "patch_planning_ready" if final.get("ready_for_patch_planning") else "needs_more_info",
    }}
    step = _step_result(final)
    yield start_event(f"{run_id}-step-result", "step_result", step)
    if step["next_step"] == "ask_user":
        yield start_event(f"{run_id}-user-question", "user_question", _user_question(final, step, message))
        yield {"op": "done", "investigation": final}
        return
    yield start_event(f"{run_id}-output", "output", {
        "content": _summary(final),
        "streaming": False,
    })
    yield {"op": "done", "investigation": final}


def _investigation_tools() -> list[dict]:
    tools = [
        openai_tool_schema(tool.name, tool.description, tool.parameters)
        for name in INVESTIGATION_TOOLS
        if (tool := registry.get(name))
    ]
    tools.append(_finish_tool_schema())
    return tools


def _finish_tool_schema() -> dict:
    return openai_tool_schema(
        "finish_investigation",
        "Finish investigation when enough project facts exist for patch planning, or when user input is required.",
        {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "ready_for_patch_planning": {"type": "boolean"},
                "beliefs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
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
                            "evidence": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["statement", "status"],
                    },
                },
                "open_questions": {"type": "array", "items": {"type": "string"}},
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
                "patch_planning_context": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "ready_for_patch_planning"],
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
) -> Iterator[dict]:
    messages.append({"role": "user", "content": prompt.build_investigation_finalize()})
    last_error = ""
    last_content = ""

    for attempt in range(MAX_FINALIZATION_ATTEMPTS):
        thinking_id = f"{run_id}-thinking-final-{attempt}"
        yield start_event(thinking_id, "thinking", {
            "text": "Investigation step limit reached. Summarizing observed facts.",
            "done": False,
            "open": True,
        })
        assistant = _call_model(
            provider,
            model,
            messages,
            tools=[_finish_tool_schema()],
            max_tokens=FINALIZATION_OUTPUT_TOKENS,
        )
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-final-{attempt}", "usage", {
                "delta": usage,
                "total": usage_total,
            })

        content = _content_text(assistant.get("content"))
        last_content = content or last_content
        tool_calls = assistant.get("tool_calls") or []
        finish_call = next(
            (
                call for call in tool_calls
                if ((call.get("function") or {}).get("name") == "finish_investigation")
            ),
            None,
        )
        messages.append({
            "role": "assistant",
            "content": assistant.get("content") or "",
            **({"tool_calls": [finish_call]} if finish_call else {}),
        })
        yield {"op": "update", "id": thinking_id, "patch": {
            "text": content,
            "done": True,
            "open": bool(finish_call),
        }}

        if finish_call:
            call_id = finish_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = finish_call.get("function") or {}
            try:
                final = _finish_payload(_tool_arguments(function.get("arguments")))
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
        else:
            last_error = "Investigation step limit reached before finish_investigation was called."

        if attempt < MAX_FINALIZATION_ATTEMPTS - 1:
            messages.append({
                "role": "user",
                "content": (
                    f"Previous finalization failed: {last_error}\n"
                    "Call finish_investigation again with valid JSON arguments."
                ),
            })

    return {
        "summary": last_content or "Investigation step limit reached before a final summary was produced.",
        "ready_for_patch_planning": False,
        "beliefs": [],
        "open_questions": [last_error or "finish_investigation did not produce a usable result."],
        "patch_planning_context": [],
    }


def _run_tool_stream(name: str, call_id: str, arguments: dict, workspace_dir: str) -> Iterator[dict]:
    if name not in INVESTIGATION_TOOLS:
        raise ValueError(f"unknown investigation tool: {name or 'tool'}")

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
            return json.dumps(done, ensure_ascii=False)

    tool = registry.get(name)
    if tool is None:
        raise ValueError(f"unknown tool: {name}")
    yield start_event(call_id, "tool", {
        "name": name,
        "description": tool.description,
        "status": "running",
        "open": False,
        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
        "output": "",
    })
    result = asyncio.run(tool.execute(arguments, {"directory": workspace_dir}))
    yield {"op": "update", "id": call_id, "patch": {
        "status": "error" if result.title.startswith("[error]") else "done",
        "title": result.title,
        "output": result.output,
        "metadata": result.metadata,
    }}
    return json.dumps({
        "tool_call_id": call_id,
        "title": result.title,
        "output": result.output,
        "metadata": result.metadata,
    }, ensure_ascii=False)


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


def _finish_payload(arguments: dict) -> dict:
    unknowns = _unknowns(arguments.get("unknowns"))
    open_questions = arguments.get("open_questions") if isinstance(arguments.get("open_questions"), list) else []
    if open_questions and not unknowns:
        unknowns = [
            {
                "id": f"Q{index}",
                "question": question,
                "blocking": True,
                "resolution_strategy": "ask_user",
            }
            for index, question in enumerate(_clean_questions(open_questions), start=1)
        ]
    ready = bool(arguments.get("ready_for_patch_planning"))
    if any(item["blocking"] for item in unknowns):
        ready = False
    return {
        "summary": str(arguments.get("summary") or "").strip(),
        "ready_for_patch_planning": ready,
        "beliefs": arguments.get("beliefs") if isinstance(arguments.get("beliefs"), list) else [],
        "open_questions": open_questions,
        "unknowns": unknowns,
        "patch_planning_context": (
            arguments.get("patch_planning_context")
            if isinstance(arguments.get("patch_planning_context"), list)
            else []
        ),
    }


def _clean_questions(value: list) -> list[str]:
    return [text for item in value if (text := str(item).strip())]


def _step_result(final: dict) -> dict:
    blockers = [item for item in final.get("unknowns", []) if item.get("blocking")]
    investigate = [item for item in blockers if item.get("resolution_strategy") == "investigate_project"]
    ask_user = [item for item in blockers if item.get("resolution_strategy") == "ask_user"]
    if investigate:
        return {
            "next_step": "continue_investigation",
            "continue_reason": "; ".join(item["question"] for item in investigate[:3]),
            "target_unknown_ids": [item["id"] for item in investigate],
        }
    if ask_user:
        return {
            "next_step": "ask_user",
            "continue_reason": "; ".join(item["question"] for item in ask_user[:3]),
            "target_unknown_ids": [item["id"] for item in ask_user],
        }
    if final.get("ready_for_patch_planning"):
        return {
            "next_step": "write_code",
            "continue_reason": final.get("summary") or "Investigation is ready for patch planning.",
            "target_unknown_ids": [],
        }
    open_questions = final.get("open_questions") or []
    return {
        "next_step": "ask_user" if open_questions else "failed",
        "continue_reason": "; ".join(str(item) for item in open_questions[:3])
        or final.get("summary")
        or "Investigation did not reach patch planning readiness.",
        "target_unknown_ids": [],
    }


def _user_question(final: dict, step: dict, origin_message: str = "") -> dict:
    unknown = next(
        (
            item for item in final.get("unknowns", [])
            if item.get("blocking") and item.get("resolution_strategy") == "ask_user"
        ),
        None,
    )
    question = (unknown or {}).get("question") or (final.get("open_questions") or [step["continue_reason"]])[0]
    options = _question_options(question)
    unknown_id = (unknown or {}).get("id", "")
    return {
        "id": unknown_id or "question",
        "question": question,
        "origin_message": origin_message,
        "reason": step["continue_reason"],
        "why_it_matters": (
            f"This answer resolves blocking unknown {unknown_id} before patch planning."
            if unknown_id
            else "This answer resolves an open decision before patch planning."
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
    for index, item in enumerate(items):
        text = str(item).strip()
        if not text:
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
                "label": "Any key",
                "value": "Allow users to set any keyboard key as the trigger.",
            },
            {
                "id": "modifiers_only",
                "label": "Modifiers only",
                "value": "Only allow modifier keys such as Alt/Ctrl/Shift/Win as triggers.",
                "recommended": True,
            },
            {
                "id": "continue_research",
                "label": "Research more",
                "value": "Continue investigating the existing hotkey design before deciding.",
            },
        ]
    if any(word in text for word in ("pyobjc", "quartz", "applescript", "window manipulation")):
        return [
            {
                "id": "pyobjc_quartz",
                "label": "Use PyObjC/Quartz",
                "value": "Use PyObjC/Quartz Accessibility API for macOS window manipulation.",
                "recommended": True,
            },
            {
                "id": "applescript",
                "label": "Use AppleScript",
                "value": "Use AppleScript/osascript for macOS window manipulation.",
            },
            {
                "id": "research_more",
                "label": "Research more",
                "value": "Continue researching macOS window manipulation before editing code.",
            },
        ]
    if any(word in text for word in ("password", "hash")):
        return [
            {
                "id": "hash_passwords",
                "label": "Hash passwords",
                "value": "Passwords must be hashed; implement the project default securely.",
                "recommended": True,
            },
            {
                "id": "prototype_only",
                "label": "Prototype only",
                "value": "This is only a prototype; do not add password hashing yet.",
            },
            {
                "id": "ask_details",
                "label": "Ask me details",
                "value": "Ask for more password storage and authentication policy details first.",
            },
        ]
    return [
        {
            "id": "best_practice",
            "label": "Use best practice",
            "value": f"Use standard engineering best practice for this decision: {question}",
            "recommended": True,
        },
        {
            "id": "continue_research",
            "label": "Research more",
            "value": f"Continue investigating this decision before choosing: {question}",
        },
    ]


def _unknowns(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for raw in value:
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
        }
        if item["id"] and item["question"]:
            items.append(item)
    return items


def _summary(final: dict) -> str:
    lines = [final.get("summary") or "Investigation complete."]
    if final.get("beliefs"):
        lines.append("\nBeliefs:")
        lines.extend(
            f"- {item.get('status', 'unverified')}: {item.get('statement', '')}"
            for item in final["beliefs"][:8]
        )
    if final.get("open_questions"):
        lines.append("\nOpen questions:")
        lines.extend(f"- {item}" for item in final["open_questions"][:5])
    if final.get("patch_planning_context"):
        lines.append("\nPatch planning context:")
        lines.extend(f"- {item}" for item in final["patch_planning_context"][:8])
    return "\n".join(lines)
