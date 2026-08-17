from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator
from uuid import uuid4

from .. import model_settings, skill_runtime
from ..agent_runtime import (
    assistant_message,
    call_model,
    content_text,
    start_event,
)
from .clearify import event_sink
from .tools import default_light_tools, execute_tool_call, tool_schema


def ask(
    prompt: str,
    *,
    workspace_dir: str = ".",
    tool_names: list[str] | None = None,
) -> str:
    setting = model_settings.resolve(model_settings.LIGHT_AGENT)
    if setting is None:
        raise ValueError("no model configured for light_agent stage")

    provider = setting["provider"]
    model = setting["model_id"]
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
            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                _emit({"op": "update", "id": thinking_id, "patch": {
                    "text": "Ready to answer.",
                    "done": True,
                    "open": False,
                }})
                return content_text(assistant.get("content") or "")

            messages.append(assistant_message(assistant))
            _emit({"op": "update", "id": thinking_id, "patch": {
                "text": _tool_call_summary(tool_calls),
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


def stream(message: str, context: list[str], workspace_dir: str) -> Iterator[dict]:
    output_id = "light-agent-output"
    events: queue.Queue = queue.Queue()

    def publish(event: dict) -> None:
        events.put(event)

    def run_agent() -> None:
        try:
            with event_sink(publish):
                stage_id = "light-agent-stage"
                publish(start_event(stage_id, "stage", {
                    "name": "light_agent",
                    "label": "Light agent",
                    "state": "running",
                    "phase": "deciding",
                }))
                result = ask(_prompt(message, context, workspace_dir), workspace_dir=workspace_dir)
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


def _prompt(message: str, context: list[str], workspace_dir: str) -> str:
    return _json({
        "user_request": message,
        "workspace_dir": workspace_dir,
        "context": context,
    })


def _emit(event: dict) -> None:
    from .clearify import emit

    emit(event)


def _tool_call_summary(tool_calls: list[dict]) -> str:
    lines = ["Calling tools:"]
    for call in tool_calls:
        function = call.get("function") or {}
        name = function.get("name") or "unknown_tool"
        lines.append(f"- {name}: execute requested tool")
    return "\n".join(lines)


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
