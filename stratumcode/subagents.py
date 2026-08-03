from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from itertools import count
from pathlib import Path
from uuid import uuid4

from . import app_settings, hypothesis_verifier, mcp, model_settings, prompt, providers, skills, skill_runtime
from .agent.tools import openai_tool_schema
from .agent_runtime import (
    add_usage as _add_usage,
    call_model as _call_model,
    assistant_message as _assistant_message,
    assistant_visible_text as _assistant_visible_text,
    content_text as _content_text,
    empty_usage as _empty_usage,
    execute_skill_tool_call,
    finish_initial_skill_selection,
    start_event,
    tool_error_json,
    usage_delta as _usage_delta,
)
from .subagent_catalog import normalize_agent_name
from .tools import registry
from .tools.spec import ToolResult

def run_stream(agent: str, task: str, workspace_dir: str = ".") -> Iterator[dict]:
    name = normalize_agent_name(agent)
    with skill_runtime.target_scope(f"subagent:{name}"):
        yield from _select_initial_subagent_skills(name, task)
        yield from skill_runtime.pop_events()
        if name == "mcp-installer":
            yield from mcp_install_stream(task, workspace_dir)
            return
        if name == "hypothesis-verifier":
            yield from hypothesis_verify_stream(task, workspace_dir)
            return
        if name == "skill-placer":
            yield from skill_placement_stream(task, workspace_dir)
            return
        raise ValueError(f"unknown subagent: {agent}")


def _select_initial_subagent_skills(name: str, task: str) -> Iterator[dict]:
    setting = (
        model_settings.resolve(model_settings.DEFAULT_STAGE)
        or model_settings.resolve(model_settings.EVIDENCE_STAGE)
    )
    if setting is None:
        return
    prompt_text = skill_runtime.initial_selection_prompt(f"subagent: {name}\ntask: {task}")
    yield from skill_runtime.pop_events()
    finish_initial_skill_selection(
        setting["provider"],
        setting["model_id"],
        prompt_text,
    )


def hypothesis_verify_stream(task: str, workspace_dir: str = ".") -> Iterator[dict]:
    payload = _task_payload(task)
    hypothesis = str(payload.get("hypothesis") or payload.get("task") or task or "").strip()
    if not hypothesis:
        yield {"op": "done", "error": "hypothesis is required"}
        return

    context = payload.get("context")
    if not isinstance(context, list):
        context = []
    context = [str(item) for item in context if str(item).strip()]
    max_rounds = payload.get("max_rounds")
    try:
        max_rounds = int(max_rounds) if max_rounds not in (None, "") else None
    except (TypeError, ValueError):
        max_rounds = None

    run_id = uuid4().hex[:10]
    agent_id = f"{run_id}-agent"
    yield start_event(agent_id, "subagent", {
        "name": "@hypothesis-verifier",
        "task": f"Verify hypothesis: {_short(hypothesis, 140)}",
        "status": "running",
        "open": True,
    })

    try:
        done = {}
        for packet in hypothesis_verifier.evidence_stream(
            hypothesis,
            context,
            workspace_dir,
            max_rounds=max_rounds,
        ):
            if packet.get("op") == "done":
                done = packet
            else:
                yield packet

        run = done.get("run") or {}
        result = run.get("summary") or "Hypothesis verification completed."
        yield {"op": "update", "id": agent_id, "patch": {
            "status": "done",
            "result": result,
        }}
        yield done or {"op": "done", "run": run}
    except Exception as exc:
        yield {"op": "update", "id": agent_id, "patch": {
            "status": "error",
            "result": str(exc),
        }}
        yield {"op": "done", "error": str(exc)}


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


def _task_payload(task: str) -> dict:
    text = (task or "").strip()
    if not text.startswith("{"):
        return {"task": text}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"task": text}
    return payload if isinstance(payload, dict) else {"task": text}


def _round_indexes(limit: int, start: int = 0):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


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
        {"role": "system", "content": prompt.build_mcp_installer_system(app_settings.get_output_language())},
        {"role": "user", "content": prompt.build_mcp_installer_user(hint, workspace_dir)},
    ]

    for round_index in _round_indexes(app_settings.get_round_limit("installer_rounds"), start=0):
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

        content = _assistant_visible_text(assistant)
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
        messages.append(_assistant_message(assistant))

        if not tool_calls:
            continue

        for raw_call in tool_calls:
            call_id = raw_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = raw_call.get("function") or {}
            name = function.get("name") or ""
            if name == "load_skill":
                _, output, _ = execute_skill_tool_call(raw_call)
                yield from skill_runtime.pop_events()
                observations.append(output)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": output,
                })
                continue
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


def skill_placement_stream(skill_ref: str, workspace_dir: str = ".") -> Iterator[dict]:
    """判断一个 skill 最适合放在哪个 target（global / state:<name> / subagent:<name>）。

    skill_ref 可以是 skill 名（name）、id（目录路径）、或 skill_file 路径。
    读取当前全部 targets（含每个部分的 skill 指南）+ 该 skill 的 SKILL.md，
    让模型给出放置建议（target_id + rationale + confidence + alternatives）。
    """
    skill_ref = (skill_ref or "").strip()
    if not skill_ref:
        yield {"op": "done", "error": "skill reference is required"}
        return

    run_id = uuid4().hex[:10]
    agent_id = f"{run_id}-agent"
    yield start_event(agent_id, "subagent", {
        "name": "@skill-placer",
        "task": f"Place skill: {_short(skill_ref, 140)}",
        "status": "running",
        "open": True,
    })

    try:
        skill = _find_skill(skill_ref)
        if skill is None:
            result = {
                "ok": False,
                "error": f"skill not found: {skill_ref}",
                "target_id": None,
                "rationale": "",
                "confidence": "low",
                "alternatives": [],
            }
            yield {"op": "update", "id": agent_id, "patch": {
                "status": "error",
                "result": json.dumps(result, ensure_ascii=False),
            }}
            yield {"op": "done", "result": result}
            return

        content = ""
        skill_file = Path(str(skill.get("skill_file") or ""))
        if skill_file.is_file():
            try:
                content = skill_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = ""

        targets_json = json.dumps(
            [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "name": item["name"],
                    "label": item["label"],
                    "guide": str(item.get("guide") or ""),
                }
                for item in skill_runtime.targets()
            ],
            ensure_ascii=False,
            indent=2,
        )

        setting = _installer_setting()
        if setting is None:
            result = _deterministic_placement(skill, content)
        else:
            result = yield from _react_place(skill, content, targets_json, run_id, setting)

        yield {"op": "update", "id": agent_id, "patch": {
            "status": "done",
            "result": json.dumps(result, ensure_ascii=False),
        }}
        yield start_event(f"{run_id}-output", "output", {
            "content": json.dumps(result, ensure_ascii=False),
            "streaming": False,
        })
        yield {"op": "done", "result": result}
    except Exception as exc:
        yield {"op": "update", "id": agent_id, "patch": {
            "status": "error",
            "result": str(exc),
        }}
        yield {"op": "done", "error": str(exc)}


def _find_skill(skill_ref: str) -> dict | None:
    """按 name / id（目录路径）/ skill_file 路径找本地 skill。"""
    ref = (skill_ref or "").strip().casefold()
    if not ref:
        return None
    items = skills.list_local()["items"]
    for item in items:
        if str(item.get("name") or "").casefold() == ref:
            return item
    for item in items:
        if str(item.get("id") or "").casefold() == ref:
            return item
    for item in items:
        if str(item.get("skill_file") or "").casefold() == ref:
            return item
    return None


def _react_place(
    skill: dict,
    content: str,
    targets_json: str,
    run_id: str,
    setting: dict,
) -> Iterator[dict]:
    provider = setting["provider"]
    model = setting["model_id"]
    pricing_rules = providers.get_model_pricing(provider["id"], model)
    usage_total = _empty_usage(pricing_rules)
    messages = [
        {"role": "system", "content": prompt.build_skill_placer_system(app_settings.get_output_language())},
        {"role": "user", "content": prompt.build_skill_placer_user(
            str(skill.get("name") or ""),
            json.dumps(skill.get("metadata") or {}, ensure_ascii=False)[:3000],
            content,
            targets_json,
        )},
    ]

    thinking_id = f"{run_id}-thinking"
    yield start_event(thinking_id, "thinking", {"text": "", "done": False, "open": True})
    assistant = _call_model(provider, model, messages, tools=None)
    if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
        _add_usage(usage_total, usage)
        yield start_event(f"{run_id}-usage", "usage", {"delta": usage, "total": usage_total})
    text = _assistant_visible_text(assistant)
    yield {"op": "update", "id": thinking_id, "patch": {"text": text, "done": True}}
    return _parse_placement_json(text)


def _parse_placement_json(text: str) -> dict:
    """从模型输出提取放置决策 JSON（容忍 markdown fence 和前后杂讯）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {
                "ok": False,
                "error": "model did not return a placement JSON",
                "target_id": None,
                "rationale": _short(raw, 300),
                "confidence": "low",
                "alternatives": [],
            }
        try:
            parsed = json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            return {
                "ok": False,
                "error": "model returned unparseable placement output",
                "target_id": None,
                "rationale": _short(raw, 300),
                "confidence": "low",
                "alternatives": [],
            }
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "error": "placement output is not an object",
            "target_id": None,
            "rationale": "",
            "confidence": "low",
            "alternatives": [],
        }
    target_id = str(parsed.get("target_id") or "").strip()
    valid = {item["id"] for item in skill_runtime.targets()}
    if target_id not in valid:
        return {
            "ok": False,
            "error": f"model suggested unknown target: {target_id or '(empty)'}",
            "target_id": target_id or None,
            "rationale": str(parsed.get("rationale") or ""),
            "confidence": str(parsed.get("confidence") or "low"),
            "alternatives": [str(x) for x in (parsed.get("alternatives") or []) if str(x) in valid],
        }
    return {
        "ok": True,
        "target_id": target_id,
        "rationale": str(parsed.get("rationale") or ""),
        "confidence": str(parsed.get("confidence") or "low"),
        "alternatives": [str(x) for x in (parsed.get("alternatives") or []) if str(x) in valid],
    }


def _deterministic_placement(skill: dict, content: str) -> dict:
    """无模型兜底：按 SKILL.md 内容关键词给出保守建议。

    不是语义判断，只是保证 subagent 在模型不可用时仍能产出有用输出。
    """
    haystack = " ".join([
        str(skill.get("name") or ""),
        str(skill.get("description") or ""),
        content,
    ]).casefold()
    if any(token in haystack for token in ("investigat", "grounding", "evidence", "grep", "trace")):
        target_id = "state:investigating"
    elif any(token in haystack for token in ("test", "validat", "quality gate", "regression")):
        target_id = "state:validating"
    elif any(token in haystack for token in ("implement", "coding convention", "refactor", "edit code")):
        target_id = "state:implementing"
    elif any(token in haystack for token in ("mcp", "model-context-protocol")):
        target_id = "subagent:mcp-installer"
    elif any(token in haystack for token in ("plan", "patch plan", "impact", "risk")):
        target_id = "state:patch_planning"
    elif any(token in haystack for token in ("analy", "task decompos", "requirement")):
        target_id = "state:analyzing"
    else:
        target_id = "global"
    return {
        "ok": True,
        "target_id": target_id,
        "rationale": "deterministic keyword fallback (no model configured); review the suggestion manually",
        "confidence": "low",
        "alternatives": ["global"],
        "fallback": True,
    }
