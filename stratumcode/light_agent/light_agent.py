from __future__ import annotations

import asyncio
import json

from .. import model_settings
from ..agent_runtime import call_model, content_text, assistant_message, tool_arguments, _schema
from ..tools import registry


def _schema(names: list[str]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for name in names
        if (tool := registry.get(name))
    ]
def ask(
    prompt: str,
    *,
    workspace_dir: str = ".",
    tool_names: list[str] | None = None,
    max_steps: int = 8,
) -> str:
    setting = model_settings.resolve(model_settings.LIGHT_AGENT)
    if setting is None:
        raise ValueError("no model configured for light_agent stage")

    provider = setting["provider"]
    model = setting["model_id"]
    messages = [{"role": "user", "content": prompt}]
    tools = [_schema(tool_names) if tool_names else None][0]

    for _ in range(max_steps):
        assistant = call_model(provider, model, messages, tools=tools, use_skills=False)
        tool_calls = assistant.get("tool_calls") or []

        # 没有工具调用 → 本轮结束，返回文本
        if not tool_calls:
            return content_text(assistant.get("content") or "")

        # 有工具调用 → assistant 消息（带 tool_calls）必须先塞回历史
        messages.append(assistant_message(assistant))

        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            arguments = tool_arguments(fn.get("arguments"))
            call_id = call.get("id") or ""

            tool = registry.get(name)
            if tool is None:
                output = f"[error] unknown tool: {name}"
            else:
                try:
                    result = asyncio.run(tool.execute(arguments, {"directory": workspace_dir}))
                    output = result.output or result.title
                except Exception as exc:
                    output = f"[error] {exc}"

            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": output,
            })

    return "Reached max steps without a final text response."