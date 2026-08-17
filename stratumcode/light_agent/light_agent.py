from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol

from .. import chat, model_settings, skill_runtime, subagents
from ..agent.policy import DISCOVERY_TOOLS
from ..agent_runtime import (
    assistant_message,
    call_model,
    content_text,
    execute_skill_tool_call,
    tool_arguments,
)
from ..status import task_analysis
from ..tools import registry
from ..tools.spec import ToolResult

UNSAFE_LIGHT_TOOLS = {
    "apply_patch",
    "finish_step",
    "process",
    "read_terminal",
    "rollback_patch",
    "terminal",
}
EXTRA_READ_ONLY_TOOLS = ("git", "lsp_tool", "patch_history", "python_static_check")
LOCAL_LIGHT_TOOLS = ("run_investigation", "run_write_loop", "run_subagent")


class LightToolExecutor(Protocol):
    def __call__(self, arguments: dict, workspace_dir: str) -> str:
        ...


@dataclass(frozen=True)
class LightTool:
    name: str
    description: str
    parameters: dict
    execute: LightToolExecutor


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
    tools = _tool_schema(list(tool_names or _default_light_tools()))

    with skill_runtime.target_scope(skill_runtime.GLOBAL_TARGET):
        while True:
            assistant = call_model(provider, model, messages, tools=tools, use_skills=True)
            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                return content_text(assistant.get("content") or "")

            messages.append(assistant_message(assistant))
            for call in tool_calls:
                output = _execute_tool_call(call, workspace_dir)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id") or "",
                    "content": output,
                })


def _default_light_tools() -> tuple[str, ...]:
    names = [
        *DISCOVERY_TOOLS,
        *EXTRA_READ_ONLY_TOOLS,
        *LOCAL_LIGHT_TOOLS,
    ]
    return tuple(
        dict.fromkeys(name for name in names if name not in UNSAFE_LIGHT_TOOLS)
    )


def _tool_schema(names: list[str]) -> list[dict]:
    schemas = []
    local_tools = _light_tools()
    for name in names:
        local = local_tools.get(name)
        if local is not None:
            schemas.append(_schema_item(local.name, local.description, local.parameters))
            continue
        registered = registry.get(name)
        if registered is not None:
            schemas.append(_schema_item(registered.name, registered.description, registered.parameters))
    return schemas


def _schema_item(name: str, description: str, parameters: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def _execute_tool_call(call: dict, workspace_dir: str) -> str:
    fn = call.get("function") or {}
    name = fn.get("name") or ""
    arguments = tool_arguments(fn.get("arguments"))
    local = _light_tools().get(name)
    try:
        if name == "load_skill":
            _, output, _ = execute_skill_tool_call(call)
            return output
        if local is not None:
            return local.execute(arguments, workspace_dir)
        tool = registry.get(name)
        if tool is None:
            return f"[error] unknown tool: {name}"
        return _tool_result_text(asyncio.run(tool.execute(arguments, {"directory": workspace_dir})))
    except Exception as exc:
        return f"[error] {exc}"


def _tool_result_text(result: ToolResult) -> str:
    return result.output or result.title


def _light_tools() -> dict[str, LightTool]:
    return {
        "run_investigation": LightTool(
            name="run_investigation",
            description=(
                "Run the existing investigation state for complex read-only codebase investigation. "
                "Use this when simple read/grep/code_nav is not enough."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "context": {"type": "array", "items": {"type": "string"}},
                    "analysis": {"type": "object"},
                },
                "required": ["message"],
                "additionalProperties": False,
            },
            execute=_run_investigation_tool,
        ),
        "run_write_loop": LightTool(
            name="run_write_loop",
            description=(
                "Run the existing rigorous write loop. It analyzes when needed, investigates when "
                "needed, then runs design, patch planning, implementation, and validation. "
                "Returns validation-centered output instead of raw patch output."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "context": {"type": "array", "items": {"type": "string"}},
                    "analysis": {"type": "object"},
                    "investigation": {"type": "object"},
                    "design_plan": {"type": "object"},
                },
                "required": ["message"],
                "additionalProperties": False,
            },
            execute=_run_write_loop_tool,
        ),
        "run_subagent": LightTool(
            name="run_subagent",
            description="Run an existing StratumCode subagent and return its final streamed result.",
            parameters={
                "type": "object",
                "properties": {
                    "agent": {"type": "string"},
                    "task": {"type": "string"},
                },
                "required": ["agent", "task"],
                "additionalProperties": False,
            },
            execute=_run_subagent_tool,
        ),
    }


def _run_investigation_tool(arguments: dict, workspace_dir: str) -> str:
    message = _required_text(arguments, "message")
    context = _string_list(arguments.get("context"), "context")
    analysis = _dict_or_none(arguments.get("analysis"))
    if analysis is None:
        analysis = _analyze(message, context, workspace_dir)
    investigation = _run_investigation(
        message=message,
        analysis=analysis,
        context=context,
        workspace_dir=workspace_dir,
    )
    return _json({
        "analysis": _analysis_summary(analysis),
        "investigation": investigation,
    })


def _run_write_loop_tool(arguments: dict, workspace_dir: str) -> str:
    message = _required_text(arguments, "message")
    context = _string_list(arguments.get("context"), "context")
    analysis = _dict_or_none(arguments.get("analysis"))
    if analysis is None:
        analysis = _analyze(message, context, workspace_dir)
    investigation = _dict_or_none(arguments.get("investigation"))
    if investigation is None:
        investigation = _run_investigation(
            message=message,
            analysis=analysis,
            context=context,
            workspace_dir=workspace_dir,
        )
    design_plan = _dict_or_none(arguments.get("design_plan"))
    run = chat.ChatRun(
        message=message,
        context=context,
        workspace_dir=workspace_dir,
        analysis=analysis,
        state=chat.ChatState.PATCH_PLANNING if design_plan is not None else chat.ChatState.DESIGNING,
        last_investigation=investigation,
        design_plan=design_plan,
    )
    events = _drive_chat_loop(run)
    return _json({
        "state": run.state.value,
        "analysis": _analysis_summary(run.analysis or {}),
        "investigation_summary": (run.last_investigation or {}).get("summary", ""),
        "validation_result": run.validation_result or {},
        "changed_files": run.changed_files,
        "events": _event_summary(events),
        "error": run.error,
    })


def _run_subagent_tool(arguments: dict, workspace_dir: str) -> str:
    agent = _required_text(arguments, "agent")
    task = _required_text(arguments, "task")
    events = list(subagents.run_stream(agent, task, workspace_dir))
    return _json({
        "agent": agent,
        "events": _event_summary(events),
        "final": _last_output(events),
    })


def _analyze(message: str, context: list[str], workspace_dir: str) -> dict:
    return task_analysis.analyze_task(message, context, workspace_dir)


def _run_investigation(
    *,
    message: str,
    analysis: dict,
    context: list[str],
    workspace_dir: str,
) -> dict:
    from .. import investigator

    final = None
    for event in investigator.investigation_stream(
        message=message,
        analysis=analysis,
        context=context,
        workspace_dir=workspace_dir,
    ):
        if event.get("op") == "done" and isinstance(event.get("investigation"), dict):
            final = event["investigation"]
    if final is None:
        raise ValueError("investigation finished without an investigation result")
    return final


def _drive_chat_loop(run: chat.ChatRun) -> list[dict]:
    events = []
    for event in chat._chat_loop(run):
        events.append(event)
    return events


def _event_summary(events: list[dict]) -> list[dict]:
    summary = []
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        patch = event.get("patch") if isinstance(event.get("patch"), dict) else {}
        summary.append({
            "op": event.get("op", ""),
            "event": event.get("event", ""),
            "name": data.get("name", ""),
            "state": patch.get("state") or data.get("state", ""),
            "phase": patch.get("phase") or data.get("phase", ""),
        })
    return summary


def _last_output(events: list[dict]) -> str:
    for event in reversed(events):
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        content = str(data.get("content") or "").strip()
        if content:
            return content
    return ""


def _analysis_summary(analysis: dict) -> dict:
    intent = analysis.get("intent") if isinstance(analysis.get("intent"), dict) else {}
    return {
        "id": analysis.get("id", ""),
        "intent": intent,
        "execution_mode": analysis.get("execution_mode", ""),
        "acceptance_criteria": analysis.get("acceptance_criteria", []),
    }


def _required_text(arguments: dict, field: str) -> str:
    value = str(arguments.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _string_list(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return [str(item).strip() for item in value if str(item).strip()]


def _dict_or_none(value: object) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("expected an object")
    return value


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
