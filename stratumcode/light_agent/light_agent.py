from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator
from uuid import uuid4

from .. import model_settings, providers, skill_runtime
from .. import memory_system
from ..agent_runtime import (
    add_usage,
    assistant_message,
    assistant_visible_text,
    call_model,
    content_text,
    empty_usage,
    finish_initial_skill_selection,
    start_event,
    tool_arguments,
    usage_delta,
)
from .clearify import event_sink
from .prompting import build_light_agent_prompt
from .task_seed import light_task_analysis
from .task_state import LightTaskState
from .tools import default_light_tools, execute_tool_call, tool_schema


def ask(
    prompt: str,
    *,
    workspace_dir: str = ".",
    tool_names: list[str] | None = None,
    setting: dict | None = None,
    task_state: LightTaskState | None = None,
    session_id: int | None = None,
    skill_selection_context: str = "",
) -> str:
    setting = setting or model_settings.resolve(model_settings.LIGHT_AGENT)
    if setting is None:
        raise ValueError("no model configured for light_agent stage")

    provider = setting["provider"]
    model = setting["model_id"]
    pricing_rules = _pricing_rules(setting)
    usage_total = empty_usage(pricing_rules)
    messages = [{"role": "user", "content": prompt}]
    tools = tool_schema(list(tool_names or default_light_tools()))

    with skill_runtime.target_scope(skill_runtime.GLOBAL_TARGET):
        _select_initial_skills(
            provider,
            model,
            skill_selection_context or prompt,
            pricing_rules,
            usage_total,
        )
        while True:
            thinking_id = f"light-thinking-{uuid4().hex[:8]}"
            _emit(start_event(thinking_id, "thinking", {
                "text": "Thinking about the next action.",
                "done": False,
                "open": False,
            }))
            assistant = call_model(provider, model, messages, tools=tools, use_skills=True)
            _emit_usage(thinking_id, pricing_rules, usage_total, assistant)
            tool_calls = assistant.get("tool_calls") or []
            content = assistant_visible_text(assistant)
            if not tool_calls:
                _emit({"op": "update", "id": thinking_id, "patch": {
                    "text": content or "Ready to answer.",
                    "done": True,
                    "open": False,
                }})
                return content_text(assistant.get("content") or "")

            messages.append(assistant_message(assistant))
            _emit({"op": "update", "id": thinking_id, "patch": {
                "text": "\n\n".join(item for item in (content, _tool_call_summary(tool_calls)) if item),
                "done": True,
                "open": False,
            }})
            for call in tool_calls:
                if task_state is not None:
                    task_events, task_assistant = _state_machine_task_events(
                        task_state,
                        call,
                        messages,
                        provider,
                        model,
                    )
                    if task_assistant is not None:
                        _emit_usage(f"{thinking_id}-task-author", pricing_rules, usage_total, task_assistant)
                    for event in task_events:
                        _emit(event)
                    call = _with_current_analysis(call, task_state)
                output = execute_tool_call(call, workspace_dir, session_id=session_id)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id") or "",
                    "content": output,
                })
            if task_state is not None:
                messages.append({"role": "user", "content": _task_state_prompt(task_state)})


def stream(message: str, context: list[str], workspace_dir: str, *, session_id: int | None = None) -> Iterator[dict]:
    output_id = "light-agent-output"
    events: queue.Queue = queue.Queue()
    task_state = LightTaskState(light_task_analysis(message, context))
    turn_id = f"turn-{uuid4().hex[:12]}"

    def publish(event: dict) -> None:
        task_state.observe(event)
        events.put(event)

    def run_agent() -> None:
        try:
            setting = model_settings.resolve(model_settings.LIGHT_AGENT)
            if setting is None:
                raise ValueError("no model configured for light_agent stage")
            analysis = task_state.current()
            if analysis is None:
                raise ValueError("light agent task state is unavailable")
            memory_snapshot = memory_system.select(
                workspace_dir=workspace_dir,
                session_id=session_id,
                query=message,
                analysis=analysis,
                scopes=("turn", "session", "project"),
                token_budget=3500,
            )
            memory_context = memory_system.render_snapshot(memory_snapshot, consumer="light_agent")
            with event_sink(publish):
                if memory_snapshot.references:
                    publish(start_event(f"memory-reference-{uuid4().hex[:8]}", "memory_reference", {
                        "items": memory_snapshot.references,
                        "status": "resolved",
                    }))
                stage_id = "light-agent-stage"
                publish(start_event(stage_id, "stage", {
                    "name": "light_agent",
                    "label": "Light agent",
                    "state": "running",
                    "phase": "deciding",
                    **_stage_model_data(setting),
                }))
                result = ask(
                    build_light_agent_prompt(message, context, workspace_dir, memory_context),
                    workspace_dir=workspace_dir,
                    setting=setting,
                    task_state=task_state,
                    session_id=session_id,
                    skill_selection_context=_skill_selection_context(
                        message,
                        context,
                        workspace_dir,
                        analysis,
                        memory_context,
                    ),
                )
                publish({"op": "update", "id": stage_id, "patch": {
                    "state": "done",
                    "phase": "answered",
                }})
                _record_direct_output_memory(workspace_dir, session_id, turn_id, result, publish)
            publish(start_event(output_id, "output", {
                    "content": result,
                    "streaming": False,
            }))
            publish({"op": "done"})
        except Exception as exc:
            publish({"op": "error", "message": f"Light agent stream failed: {exc}"})
        finally:
            events.put(None)

    threading.Thread(target=run_agent, daemon=True).start()
    while True:
        event = events.get()
        if event is None:
            break
        yield event


def _task_state_prompt(task_state: LightTaskState) -> str:
    rendered = task_state.render()
    if not rendered:
        return ""
    return "Updated task state after the previous tool call:\n" + rendered


def _emit(event: dict) -> None:
    from .clearify import emit

    emit(event)


def _emit_usage(event_id: str, pricing_rules: list[dict], usage_total: dict, assistant: dict) -> None:
    usage = usage_delta(pricing_rules, assistant.pop("_usage", {}))
    if not usage:
        return
    add_usage(usage_total, usage)
    _emit(start_event(f"{event_id}-usage", "usage", {
        "delta": usage,
        "total": usage_total,
    }))


def _select_initial_skills(
    provider: dict,
    model: str,
    context: str,
    pricing_rules: list[dict],
    usage_total: dict,
) -> None:
    prompt_text = skill_runtime.initial_selection_prompt(context)
    for event in skill_runtime.pop_events():
        _emit(event)
    if not prompt_text:
        return
    assistant = finish_initial_skill_selection(provider, model, prompt_text)
    _emit_usage(f"light-skill-selection-{uuid4().hex[:8]}", pricing_rules, usage_total, assistant)
    for event in skill_runtime.pop_events():
        _emit(event)


def _skill_selection_context(
    message: str,
    context: list[str],
    workspace_dir: str,
    analysis: dict,
    memory_context: str,
) -> str:
    lines = [
        "target: light_agent",
        f"user_request: {message}",
        f"workspace_dir: {workspace_dir}",
    ]
    if context:
        lines.append("attached_context: " + ", ".join(context))
    intent = analysis.get("intent") if isinstance(analysis.get("intent"), dict) else {}
    summary = analysis.get("summary") or intent.get("summary")
    if summary:
        lines.append(f"task_summary: {summary}")
    if analysis.get("execution_mode"):
        lines.append(f"execution_mode: {analysis.get('execution_mode')}")
    if memory_context:
        lines.append("selected_memory:")
        lines.append(memory_context)
    return "\n".join(line for line in lines if str(line).strip())


def _record_direct_output_memory(
    workspace_dir: str,
    session_id: int | None,
    turn_id: str,
    output: str,
    publish,
) -> None:
    delta = memory_system.delta_from_output(session_id=session_id, turn_id=turn_id, output=output)
    result = memory_system.record_delta(workspace_dir, delta)
    if result.get("refs"):
        publish(start_event(f"memory-write-{uuid4().hex[:8]}", "memory_write", {
            "status": "accepted",
            "summary": f"Recorded {len(result.get('refs', []))} conversation reference(s).",
            "records": result.get("records", []),
            "refs": result.get("refs", []),
        }))


def _state_machine_task_events(
    task_state: LightTaskState,
    call: dict,
    messages: list[dict],
    provider: dict,
    model: str,
) -> tuple[list[dict], dict | None]:
    try:
        return task_state.publish_events_for_tool(
            _tool_call_name(call),
            messages=messages,
            provider=provider,
            model=model,
        )
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        _emit(start_event(f"light-task-authoring-{uuid4().hex[:8]}", "thinking", {
            "text": f"Task authoring failed; using contextual fallback task state. Reason: {exc}",
            "done": True,
            "open": False,
        }))
        return task_state.fallback_events_for_tool(_tool_call_name(call), messages=messages), None


def _with_current_analysis(call: dict, task_state: LightTaskState) -> dict:
    name = _tool_call_name(call)
    if name not in {"run_investigation", "run_write_loop"}:
        return call
    analysis = task_state.current()
    if analysis is None:
        return call
    result = dict(call)
    function = dict(result.get("function") or {})
    arguments = tool_arguments(function.get("arguments"))
    arguments["analysis"] = analysis
    function["arguments"] = json.dumps(arguments, ensure_ascii=False)
    result["function"] = function
    return result


def _pricing_rules(setting: dict) -> list[dict]:
    provider_id = setting.get("provider_id")
    if provider_id is None:
        return []
    return providers.get_model_pricing(int(provider_id), setting["model_id"])


def _stage_model_data(setting: dict) -> dict:
    provider = setting["provider"]
    return {
        "model": setting["model_id"],
        "provider": provider.get("name", ""),
        "context_length": providers.model_context_length(
            provider["base_url"],
            provider["api_key"],
            setting["model_id"],
        ) or 0,
        "inherited": bool(setting.get("inherited")),
    }


def _tool_call_summary(tool_calls: list[dict]) -> str:
    items = []
    for call in tool_calls:
        function = call.get("function") or {}
        name = function.get("name") or "unknown_tool"
        arguments = tool_arguments(function.get("arguments"))
        reason = str(arguments.get("reason") or arguments.get("operation_summary") or "").strip()
        targets = arguments.get("target_unknown_ids") if isinstance(arguments.get("target_unknown_ids"), list) else []
        line = f"{name}{_tool_call_subject(name, arguments)}"
        if targets:
            line += f" for {', '.join(str(item) for item in targets if str(item).strip())}"
        if reason:
            line += f": {reason}"
        items.append(line)
    if not items:
        return ""
    return "Calling tools:\n" + "\n".join(f"- {item}" for item in items)


def _tool_call_name(call: dict) -> str:
    function = call.get("function") or {}
    return str(function.get("name") or "")


def _tool_call_subject(name: str, arguments: dict) -> str:
    value = arguments.get("path") or arguments.get("pattern") or arguments.get("query") or arguments.get("url")
    if not value and name in {"run_investigation", "run_write_loop", "run_full_pipeline"}:
        value = arguments.get("message")
    if not value and name in {"subagent", "run_subagent"}:
        value = arguments.get("agent") or arguments.get("name")
    return f"({value})" if value else ""
