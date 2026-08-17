from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator
from uuid import uuid4

from .. import model_settings, providers, skill_runtime
from ..agent_runtime import (
    add_usage,
    assistant_message,
    assistant_visible_text,
    call_model,
    content_text,
    empty_usage,
    start_event,
    tool_arguments,
    usage_delta,
)
from .clearify import event_sink
from .task_seed import light_task_analysis, light_task_events
from .task_state import LightTaskState
from .tools import default_light_tools, execute_tool_call, tool_schema


def ask(
    prompt: str,
    *,
    workspace_dir: str = ".",
    tool_names: list[str] | None = None,
    setting: dict | None = None,
    task_state: LightTaskState | None = None,
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
                output = execute_tool_call(call, workspace_dir)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id") or "",
                    "content": output,
                })
            if task_state is not None:
                messages.append({"role": "user", "content": _task_state_prompt(task_state)})


def stream(message: str, context: list[str], workspace_dir: str) -> Iterator[dict]:
    output_id = "light-agent-output"
    events: queue.Queue = queue.Queue()
    task_state = LightTaskState(light_task_analysis(message, context))

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
            with event_sink(publish):
                for event in light_task_events(analysis):
                    publish(event)
                stage_id = "light-agent-stage"
                publish(start_event(stage_id, "stage", {
                    "name": "light_agent",
                    "label": "Light agent",
                    "state": "running",
                    "phase": "deciding",
                    **_stage_model_data(setting),
                }))
                result = ask(
                    _prompt(message, context, workspace_dir, analysis),
                    workspace_dir=workspace_dir,
                    setting=setting,
                    task_state=task_state,
                )
                publish({"op": "update", "id": stage_id, "patch": {
                    "state": "done",
                    "phase": "answered",
                }})
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


def _prompt(message: str, context: list[str], workspace_dir: str, analysis: dict) -> str:
    return _json({
        "user_request": message,
        "workspace_dir": workspace_dir,
        "context": context,
        "task_analysis": analysis,
        "light_agent_instructions": [
            "Before calling tools, put a concise user-visible reason in assistant content.",
            "When a tool schema has a reason or operation_summary field, fill it with the concrete reason for that call.",
            "The reason must explain what uncertainty the tool call resolves or what decision it enables.",
            "When calling run_investigation, pass task_analysis as the analysis argument unless you intentionally need a fresh full analyzer pass.",
            "Call run_write_loop only after analysis and investigation are available; it starts at design and will not run analyzer or investigation.",
        ],
    })


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


def _tool_call_subject(name: str, arguments: dict) -> str:
    value = arguments.get("path") or arguments.get("pattern") or arguments.get("query") or arguments.get("url")
    if not value and name in {"run_investigation", "run_write_loop", "run_full_pipeline"}:
        value = arguments.get("message")
    if not value and name in {"subagent", "run_subagent"}:
        value = arguments.get("agent") or arguments.get("name")
    return f"({value})" if value else ""


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
