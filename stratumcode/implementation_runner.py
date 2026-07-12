from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from uuid import uuid4

from . import app_settings, model_settings, providers
from .agent.tools import openai_tool_schema
from .agent_runtime import (
    add_usage as _add_usage,
    call_model as _call_model,
    content_text as _content_text,
    empty_usage as _empty_usage,
    start_event,
    usage_delta as _usage_delta,
)
from .tools import registry

MAX_IMPLEMENTATION_ROUNDS = 6
MAX_CONSECUTIVE_TOOL_ERROR_ROUNDS = 3
IMPLEMENTATION_TOOLS = ("read", "apply_patch", "patch_history", "rollback_patch")


def implementation_stream(
    *,
    message: str,
    analysis: dict,
    patch_plan: dict,
    workspace_dir: str,
) -> Iterator[dict]:
    setting = model_settings.resolve(model_settings.DEFAULT_STAGE)
    if setting is None:
        raise ValueError("No model configured for implementation. Configure a default model in Providers.")

    provider = setting["provider"]
    model = setting["model_id"]
    pricing_rules = providers.get_model_pricing(provider["id"], model)
    usage_total = _empty_usage(pricing_rules)
    run_id = uuid4().hex[:10]
    stage_id = f"{run_id}-stage"

    yield start_event(stage_id, "stage", {
        "name": "implementation",
        "label": "Apply authorized patch",
        "state": "running",
        "phase": "implementation",
        "model": model,
        "context_length": providers.model_context_length(provider["base_url"], provider["api_key"], model),
        "provider": provider["name"],
        "inherited": setting["inherited"],
    })

    messages = [
        {"role": "system", "content": _system_prompt(app_settings.get_output_language())},
        {"role": "user", "content": json.dumps({
            "user_request": message,
            "analysis": {
                "id": analysis.get("id", ""),
                "intent": analysis.get("intent", {}),
                "acceptance_criteria": analysis.get("acceptance_criteria", []),
            },
            "workspace_dir": workspace_dir,
            "patch_plan": patch_plan,
        }, ensure_ascii=False)},
    ]
    tools = _implementation_tools()
    final_text = ""
    consecutive_error_rounds = 0

    for round_index in range(1, MAX_IMPLEMENTATION_ROUNDS + 1):
        assistant = _call_model(provider, model, messages, tools=tools)
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{round_index}", "usage", {"delta": usage, "total": usage_total})

        text = _content_text(assistant.get("content"))
        tool_calls = assistant.get("tool_calls") or []
        messages.append({
            "role": "assistant",
            "content": text,
            "tool_calls": tool_calls,
        })
        if not tool_calls:
            final_text = text.strip()
            break

        abort = False
        round_had_success = False
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name") or ""
            call_id = call.get("id") or f"{run_id}-tool-{round_index}"
            try:
                arguments = _tool_arguments(function.get("arguments"))
                arguments = _prepare_tool_arguments(name, arguments, patch_plan)
                output = yield from _run_tool(name, call_id, arguments, workspace_dir)
                round_had_success = round_had_success or not _tool_failed(output)
            except Exception as exc:
                output = json.dumps({"error": str(exc)}, ensure_ascii=False)
                yield start_event(call_id, "tool", {
                    "name": name or "invalid",
                    "description": "Implementation tool",
                    "status": "error",
                    "open": False,
                    "input": function.get("arguments") or "{}",
                    "output": output,
                })
                final_text = f"Implementation stopped because the tool call was invalid: {exc}"
                abort = True
            messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
        if abort:
            break
        consecutive_error_rounds = 0 if round_had_success else consecutive_error_rounds + 1
        if consecutive_error_rounds >= MAX_CONSECUTIVE_TOOL_ERROR_ROUNDS:
            final_text = (
                "Implementation stopped after repeated tool errors. "
                "The file path could not be resolved from the current patch plan; return to patch planning or use the read suggestions."
            )
            break
    else:
        final_text = "Implementation stopped after the maximum patch rounds."

    yield start_event(f"{run_id}-output", "output", {
        "content": final_text or "Implementation finished.",
        "streaming": False,
    })
    yield {"op": "update", "id": stage_id, "patch": {"state": "done", "phase": "implementation_done"}}
    yield {"op": "done", "implementation": {"summary": final_text}}


def _implementation_tools() -> list[dict]:
    tools = []
    for name in IMPLEMENTATION_TOOLS:
        tool = registry.get(name)
        if tool:
            if name == "apply_patch":
                tools.append(openai_tool_schema(tool.name, tool.description, _apply_patch_parameters()))
            else:
                tools.append(openai_tool_schema(tool.name, tool.description, tool.parameters))
    return tools


def _apply_patch_parameters() -> dict:
    return {
        "type": "object",
        "properties": {
            "patch_id": {"type": "string"},
            "step_id": {"type": "string"},
            "reason": {"type": "string"},
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "snapshot_id": {"type": "string"},
                        "mode": {"type": "string", "enum": ["modify", "create"]},
                        "content": {"type": "string"},
                        "operations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "op": {"type": "string", "enum": ["replace_exact", "insert_before", "insert_after", "delete_exact"]},
                                    "old_text": {"type": "string"},
                                    "new_text": {"type": "string"},
                                    "anchor": {"type": "string"},
                                    "text": {"type": "string"},
                                    "expected_count": {"type": "integer"},
                                },
                                "required": ["op"],
                            },
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        "required": ["step_id", "reason", "files"],
    }


def _prepare_tool_arguments(name: str, arguments: dict, patch_plan: dict) -> dict:
    if name != "apply_patch":
        return arguments
    step_id = str(arguments.get("step_id") or "").strip()
    auth = patch_plan.get("execution_authorization") or {}
    step = next(
        (item for item in patch_plan.get("implementation_steps") or [] if str(item.get("id") or "") == step_id),
        None,
    )
    if not step:
        raise ValueError(f"patch step is not in the authorized patch plan: {step_id}")
    acceptance_ids = [
        str(item.get("acceptance_id") or "")
        for item in patch_plan.get("acceptance_mapping") or []
        if step_id in [str(value) for value in item.get("covered_by") or []]
    ]
    patched = dict(arguments)
    patched["authorization_id"] = auth.get("authorization_id", "")
    patched["plan_hash"] = auth.get("plan_hash", "")
    patched["acceptance_ids"] = [item for item in acceptance_ids if item]
    patched["patch_id"] = patched.get("patch_id") or f"{step_id}-patch"
    return patched


def _run_tool(name: str, call_id: str, arguments: dict, workspace_dir: str) -> Iterator[dict]:
    tool = registry.get(name)
    if name not in IMPLEMENTATION_TOOLS or tool is None:
        output = json.dumps({"error": f"tool not allowed in implementation: {name}"}, ensure_ascii=False)
        yield start_event(call_id, "tool", {
            "name": name or "invalid",
            "description": "Implementation tool",
            "status": "error",
            "open": False,
            "input": json.dumps(arguments, ensure_ascii=False, indent=2),
            "output": output,
        })
        return output

    event_type = "patch" if name in {"apply_patch", "patch_history", "rollback_patch"} else "tool"
    yield start_event(call_id, event_type, {
        "name": name,
        "description": tool.description,
        "status": "running",
        "open": False,
        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
        "output": "",
    })
    result = asyncio.run(tool.execute(arguments, {"directory": workspace_dir}))
    status = "error" if result.title.startswith("[error]") else "done"
    yield {"op": "update", "id": call_id, "patch": {
        "status": status,
        "title": result.title,
        "output": result.output,
        "metadata": result.metadata,
    }}
    return json.dumps({
        "title": result.title,
        "output": result.output,
        "metadata": result.metadata,
    }, ensure_ascii=False)


def _tool_arguments(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid tool JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("tool arguments must be an object")
    return data


def _tool_failed(output: str) -> bool:
    try:
        data = json.loads(output or "{}")
    except json.JSONDecodeError:
        return False
    title = str(data.get("title") or "")
    if title.startswith("[error]"):
        return True
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return bool(metadata.get("error_code") or data.get("error"))


def _system_prompt(language: str) -> str:
    return f"""\
You are StratumCode's implementation runner. Write user-visible text in {language}.

Apply the already authorized patch plan. Do not redesign the solution.
Use read to obtain fresh snapshot_id values before modifying existing files.
Use apply_patch for all file writes. Its authorization_id, plan_hash, and
acceptance_ids are injected by runtime; do not include them. For an existing
empty file, use replace_exact with old_text="" and new_text set to the whole file.

After a successful apply_patch, stop unless the patch plan explicitly has another
implementation step. If apply_patch reports STALE_SNAPSHOT, read the file again
and retry once with the new snapshot. If authorization fails, stop and explain.
"""
