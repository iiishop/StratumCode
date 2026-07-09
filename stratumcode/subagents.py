from __future__ import annotations

import asyncio
import json
import platform
import re
from collections.abc import Iterator
from uuid import uuid4

from . import mcp, model_settings, providers
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
from .subagent_catalog import list_available, normalize_agent_name
from .tools import registry
from .tools.spec import ToolResult


MAX_INSTALLER_ROUNDS = 8


def run_stream(agent: str, task: str, workspace_dir: str = ".") -> Iterator[dict]:
    name = normalize_agent_name(agent)
    if name == "mcp-installer":
        yield from mcp_install_stream(task, workspace_dir)
        return
    raise ValueError(f"unknown subagent: {agent}")


def mcp_install_stream(hint: str, workspace_dir: str = ".") -> Iterator[dict]:
    hint = (hint or "").strip()
    if not hint:
        yield {"op": "done", "error": "hint is required"}
        return

    run_id = uuid4().hex[:10]
    agent_id = f"{run_id}-agent"
    yield start_event(agent_id, "subagent", {
        "name": "@mcp-installer",
        "task": f"Install MCP from: {_short(hint, 140)}",
        "status": "running",
        "open": True,
    })

    try:
        server = None
        setting = _installer_setting()
        if setting is None:
            yield _thinking(run_id, 0, "No installer model is configured. Using deterministic discovery fallback.")
        else:
            try:
                server = yield from _react_install(hint, workspace_dir, run_id, setting)
            except Exception as exc:
                yield _thinking(
                    run_id,
                    999,
                    f"Model-driven installer failed: {exc}. Falling back to deterministic discovery.",
                )

        if server is None:
            server = yield from _fallback_install(hint, workspace_dir, run_id)

        result = _server_summary(server)
        yield {"op": "update", "id": agent_id, "patch": {
            "status": "done" if server.get("status") == "running" else server.get("status", "done"),
            "result": result,
        }}
        yield start_event(f"{run_id}-output", "output", {"content": result, "streaming": False})
        yield {"op": "done", "server": server}
    except Exception as exc:
        yield {"op": "update", "id": agent_id, "patch": {
            "status": "error",
            "result": str(exc),
        }}
        yield {"op": "error", "message": str(exc)}
        yield {"op": "done", "error": str(exc)}


def _installer_setting() -> dict | None:
    return (
        model_settings.resolve(model_settings.DEFAULT_STAGE)
        or model_settings.resolve(model_settings.EVIDENCE_STAGE)
    )


def _react_install(
    hint: str,
    workspace_dir: str,
    run_id: str,
    setting: dict,
) -> Iterator[dict]:
    provider = setting["provider"]
    model = setting["model_id"]
    pricing_rules = providers.get_model_pricing(provider["id"], model)
    usage_total = _empty_usage(pricing_rules)
    observations: list[str] = []
    messages = [
        {"role": "system", "content": _installer_system_prompt()},
        {"role": "user", "content": _installer_user_prompt(hint, workspace_dir)},
    ]

    for round_index in range(MAX_INSTALLER_ROUNDS):
        thinking_id = f"{run_id}-thinking-{round_index}"
        yield start_event(thinking_id, "thinking", {
            "text": "",
            "done": False,
            "open": True,
        })
        assistant = _call_model(provider, model, messages, tools=_installer_tools())
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{round_index}", "usage", {
                "delta": usage,
                "total": usage_total,
            })

        content = _content_text(assistant.get("content"))
        tool_calls = assistant.get("tool_calls") or []
        if content:
            yield {"op": "update", "id": thinking_id, "patch": {
                "text": content,
                "done": True,
                "open": bool(tool_calls),
            }}
        else:
            yield {"op": "update", "id": thinking_id, "patch": {
                "done": True,
            }}
        messages.append({
            "role": "assistant",
            "content": assistant.get("content") or "",
            **({"tool_calls": tool_calls} if tool_calls else {}),
        })

        if not tool_calls:
            continue

        for raw_call in tool_calls:
            call_id = raw_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = raw_call.get("function") or {}
            name = function.get("name") or ""
            try:
                arguments = _tool_arguments(function.get("arguments"))
                gen = _handle_installer_tool(
                    name=name,
                    call_id=call_id,
                    arguments=arguments,
                    hint=hint,
                    observations=observations,
                    workspace_dir=workspace_dir,
                )
                try:
                    while True:
                        yield next(gen)
                except StopIteration as e:
                    output, server = e.value
            except Exception as exc:
                output = tool_error_json(exc, name)
                yield start_event(call_id, "tool", {
                    "name": name or "invalid",
                    "description": "MCP installer tool",
                    "status": "error",
                    "open": False,
                    "input": function.get("arguments") or "{}",
                    "output": output,
                })
                server = None
            observations.append(output)
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": output,
            })
            if server is not None:
                return server

    return None


def _installer_system_prompt() -> str:
    return (
        "You are @mcp-installer, a focused ReAct subagent. Your job is to install one MCP "
        "server into StratumCode's MCP registry.\n\n"
        "The user may provide a docs URL, repository URL, package name, prose hint, or raw config. "
        "If the config is not explicit, use webfetch and/or websearch to identify the MCP server, "
        "transport, command, URL, args, cwd, and required environment variables. Do not invent an "
        "endpoint or command that the source does not support.\n\n"
        "When confident, call install_mcp exactly once. Prefer a canonical config object. HTTP "
        "MCP configs require {name, transport:'http', url}. Stdio MCP configs require "
        "{name, transport:'stdio', command, args}. If the source clearly identifies a supported "
        "MCP but you do not have perfect JSON, call install_mcp with hint/source_text/rationale "
        "so the installer can infer the saved config. Put API keys and tokens in env with empty "
        "placeholder values so the UI can ask the user to configure them. Do not run shell "
        "installers; StratumCode only needs the saved MCP launch config.\n\n"
        "For agent installers such as CodeGraph, do not register a command that configures other "
        "agents, such as an interactive install command. Register the command that runs the MCP "
        "server itself, for example the docs' MCP server launch command."
    )


def _installer_user_prompt(hint: str, workspace_dir: str) -> str:
    return (
        f"User MCP hint:\n{hint}\n\n"
        f"Current workspace directory: {workspace_dir}\n"
        f"Platform: {platform.system()}\n\n"
        "Install this MCP into StratumCode. Gather enough facts first, then call install_mcp."
    )


def _installer_tools() -> list[dict]:
    return [
        openai_tool_schema(
            "websearch",
            "Search the public web for MCP docs, package pages, or install examples.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                },
                "required": ["query"],
            },
        ),
        openai_tool_schema(
            "webfetch",
            "Fetch and read a URL as text.",
            {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        ),
        openai_tool_schema(
            "install_mcp",
            "Persist and start the MCP server in StratumCode.",
            {
                "type": "object",
                "properties": {
                    "hint": {"type": "string"},
                    "source_text": {"type": "string"},
                    "config": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "transport": {"type": "string", "enum": ["http", "stdio"]},
                            "url": {"type": "string"},
                            "command": {"type": "string"},
                            "args": {"type": "array", "items": {"type": "string"}},
                            "cwd": {"type": "string"},
                            "env": {"type": "object"},
                            "enabled": {"type": "boolean"},
                        },
                    },
                    "rationale": {"type": "string"},
                    "source_url": {"type": "string"},
                },
            },
        ),
    ]


def _handle_installer_tool(
    *,
    name: str,
    call_id: str,
    arguments: dict,
    hint: str,
    observations: list[str],
    workspace_dir: str,
):
    """Yields stream packets directly. Returns (output, server) via StopIteration."""
    if name not in {"websearch", "webfetch", "install_mcp"}:
        raise ValueError(f"unknown installer tool: {name or 'tool'}")

    yield start_event(call_id, "tool", {
        "name": name,
        "description": _tool_description(name),
        "status": "running",
        "open": name == "install_mcp",
        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
        "output": "",
    })

    server = None
    if name == "install_mcp":
        server = _install_from_arguments(arguments, hint, observations, workspace_dir)
        result = ToolResult.ok(
            f"install {server.get('name', 'mcp')}",
            _server_summary(server),
            server=server,
        )
    else:
        result = _run_registry_tool(name, arguments, workspace_dir)

    status = "error" if result.title.startswith("[error]") else "done"
    yield {"op": "update", "id": call_id, "patch": {
        "status": status,
        "title": result.title,
        "output": result.output,
        "metadata": result.metadata,
    }}
    output = json.dumps({
        "tool_call_id": call_id,
        "title": result.title,
        "output": result.output,
        "metadata": result.metadata,
    }, ensure_ascii=False)
    return output, server


def _run_registry_tool(name: str, arguments: dict, workspace_dir: str) -> ToolResult:
    tool = registry.get(name)
    if tool is None:
        raise ValueError(f"unknown tool: {name}")
    return asyncio.run(tool.execute(arguments, {"directory": workspace_dir}))


def _install_from_arguments(
    arguments: dict,
    hint: str,
    observations: list[str],
    workspace_dir: str,
) -> dict:
    config = arguments.get("config")
    if not isinstance(config, dict) or not config:
        source_text = "\n\n".join(
            part for part in (
                arguments.get("source_text") or "",
                arguments.get("rationale") or "",
                "\n\n".join(observations),
            )
            if part
        )
        server = mcp.install_from_hint(arguments.get("hint") or hint, source_text)
        return _ensure_workspace_cwd(server, workspace_dir)

    config = dict(config)
    if not config.get("name") and arguments.get("name"):
        config["name"] = arguments["name"]
    config.setdefault("enabled", True)
    config["source_text"] = "\n\n".join(
        part for part in (
            hint,
            arguments.get("source_url") or "",
            arguments.get("rationale") or "",
            "\n\n".join(observations[-4:]),
        )
        if part
    )
    config = _normalize_installer_config(config, workspace_dir)
    server_id = mcp.save_server(config)
    return mcp.start_server(server_id)


def _normalize_installer_config(config: dict, workspace_dir: str) -> dict:
    name = str(config.get("name") or "").casefold()
    command = str(config.get("command") or "").casefold()
    args = config.get("args") if isinstance(config.get("args"), list) else []
    joined = " ".join(str(arg) for arg in args).casefold()
    source = str(config.get("source_text") or "").casefold()

    if (
        "colbymchenry/codegraph" in source
        or "@colbymchenry/codegraph" in source
        or name == "codegraph"
        or command == "codegraph"
    ):
        config["name"] = "codegraph"
        config["transport"] = "stdio"
        config["command"] = "codegraph"
        if "serve" not in joined or "--mcp" not in joined:
            config["args"] = ["serve", "--mcp"]
        config.setdefault("cwd", workspace_dir)
        env = config.get("env") if isinstance(config.get("env"), dict) else {}
        env.setdefault("CODEGRAPH_MCP_TOOLS", mcp.CODEGRAPH_MCP_TOOLS)
        config["env"] = env

    return config


def _fallback_install(hint: str, workspace_dir: str, run_id: str) -> Iterator[dict]:
    page_text = ""
    first_url = _first_url(hint)
    if first_url:
        call_id = f"{run_id}-fallback-fetch"
        args = {"url": first_url}
        yield start_event(call_id, "tool", {
            "name": "webfetch",
            "description": _tool_description("webfetch"),
            "status": "running",
            "open": False,
            "input": json.dumps(args, ensure_ascii=False, indent=2),
            "output": "",
        })
        result = _run_registry_tool("webfetch", args, workspace_dir)
        yield {"op": "update", "id": call_id, "patch": {
            "status": "error" if result.title.startswith("[error]") else "done",
            "title": result.title,
            "output": result.output,
            "metadata": result.metadata,
        }}
        if not result.title.startswith("[error]"):
            page_text = result.output

    call_id = f"{run_id}-fallback-install"
    args = {
        "hint": hint,
        "source_text": page_text,
        "rationale": "Deterministic install fallback after model/tool discovery did not produce a final config.",
    }
    yield start_event(call_id, "tool", {
        "name": "install_mcp",
        "description": _tool_description("install_mcp"),
        "status": "running",
        "open": True,
        "input": json.dumps(args, ensure_ascii=False, indent=2),
        "output": "",
    })
    server = mcp.install_from_hint(hint, page_text)
    server = _ensure_workspace_cwd(server, workspace_dir)
    yield {"op": "update", "id": call_id, "patch": {
        "status": "done" if server.get("status") != "error" else "error",
        "title": f"install {server.get('name', 'mcp')}",
        "output": _server_summary(server),
        "metadata": {"server": server},
    }}
    return server


def _ensure_workspace_cwd(server: dict, workspace_dir: str) -> dict:
    if (
        str(server.get("name", "")).casefold() == "codegraph"
        and not server.get("cwd")
        and server.get("id")
    ):
        raw = {
            **server,
            "transport": "stdio",
            "command": "codegraph",
            "args": ["serve", "--mcp"],
            "cwd": workspace_dir,
            "env": {
                **(server.get("env") or {}),
                "CODEGRAPH_MCP_TOOLS": mcp.CODEGRAPH_MCP_TOOLS,
            },
        }
        server_id = mcp.save_server(raw)
        return mcp.start_server(server_id)
    return server


def _tool_arguments(raw: str | None) -> dict:
    try:
        arguments = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid tool JSON: {exc}") from exc
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    return arguments


def _tool_description(name: str) -> str:
    if name == "install_mcp":
        return "Save and start a StratumCode MCP server config."
    tool = registry.get(name)
    return tool.description if tool else "MCP installer tool"


def _thinking(run_id: str, index: int, text: str, open_: bool = False) -> dict:
    return start_event(f"{run_id}-thinking-{index}", "thinking", {
        "text": text,
        "done": True,
        "open": open_,
    })


def _server_summary(server: dict) -> str:
    tools = server.get("tools") or []
    status = server.get("status") or "unknown"
    details = server.get("status_message") or ""
    line = f"Installed {server.get('name', 'mcp')} MCP server. Status: {status}."
    if tools:
        line += f" Tools: {', '.join(_short(tool.get('name', 'tool'), 40) for tool in tools[:8])}."
    if details and status != "running":
        line += f" Detail: {details}"
    return line


def _first_url(text: str) -> str:
    match = re.search(r"https?://[^\s<>'\")]+", text or "")
    return match.group(0).rstrip("`'\" )]") if match else ""


def _short(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1] + "..."
