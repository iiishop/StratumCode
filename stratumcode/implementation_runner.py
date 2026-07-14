from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from itertools import count
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

IMPLEMENTATION_TOOLS = ("read", "apply_patch", "patch_history", "rollback_patch")
VALIDATION_TOOLS = ("read", "code_nav")


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
    patch_applied = False
    changed_files: list[str] = []
    patch_required = bool(patch_plan.get("implementation_steps"))

    tool_error_limit = app_settings.get_round_limit("implementation_tool_error_rounds")
    for round_index in _round_indexes(app_settings.get_round_limit("implementation_rounds")):
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
                if name == "apply_patch" and not _tool_failed(output):
                    patch_applied = True
                    changed_files.extend(_patch_files_from_output(output))
            except Exception as exc:
                output = json.dumps({
                    "error": str(exc),
                    "retryable": True,
                    "hint": _tool_retry_hint(name, str(exc), patch_plan),
                }, ensure_ascii=False)
                yield start_event(call_id, "tool", {
                    "name": name or "invalid",
                    "description": "Implementation tool",
                    "status": "error",
                    "open": False,
                    "input": function.get("arguments") or "{}",
                    "output": output,
                })
            messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
        consecutive_error_rounds = 0 if round_had_success else consecutive_error_rounds + 1
        if tool_error_limit and consecutive_error_rounds >= tool_error_limit:
            yield start_event(f"{run_id}-tool-error-checkpoint", "user_question", _checkpoint_question(
                analysis.get("id", ""),
                message,
                "Implementation hit repeated tool errors. The patch plan or file path likely needs correction.",
            ))
            yield {"op": "update", "id": stage_id, "patch": {"state": "waiting", "phase": "implementation_tool_error_checkpoint"}}
            return
    else:
        if patch_required and not patch_applied:
            yield start_event(f"{run_id}-checkpoint", "user_question", _checkpoint_question(
                analysis.get("id", ""),
                message,
                "Implementation reached its checkpoint before applying a patch.",
                checkpoint_phase="implementation_checkpoint",
                patch_plan=patch_plan,
            ))
            yield {"op": "update", "id": stage_id, "patch": {"state": "waiting", "phase": "implementation_checkpoint"}}
            return
        final_text = "Implementation patching reached its checkpoint; continuing with validation."

    if patch_required and not patch_applied:
        yield start_event(f"{run_id}-checkpoint", "user_question", _checkpoint_question(
            analysis.get("id", ""),
            message,
            "Implementation stopped before applying the authorized patch plan.",
            checkpoint_phase="implementation_checkpoint",
            patch_plan=patch_plan,
        ))
        yield {"op": "update", "id": stage_id, "patch": {"state": "waiting", "phase": "implementation_checkpoint"}}
        return

    yield start_event(f"{run_id}-output", "output", {
        "content": final_text or "Implementation finished.",
        "streaming": False,
    })
    yield {"op": "update", "id": stage_id, "patch": {"state": "done", "phase": "implementation_done"}}
    yield {"op": "done", "implementation": {
        "status": "patch_applied" if patch_applied else "no_patch",
        "summary": final_text,
        "patch_applied": patch_applied,
        "changed_files": sorted(set(changed_files)),
        "semantic_checked": False,
    }}


def validation_stream(
    *,
    message: str,
    analysis: dict,
    patch_plan: dict,
    workspace_dir: str,
    changed_files: list[str],
) -> Iterator[dict]:
    result = yield from _validation_stream(
        message=message,
        analysis=analysis,
        patch_plan=patch_plan,
        workspace_dir=workspace_dir,
        changed_files=changed_files,
    )
    if result:
        yield {"op": "done", "validation_result": result}


def resume_validation_stream(
    *,
    message: str,
    analysis: dict,
    patch_plan: dict,
    workspace_dir: str,
    changed_files: list[str],
) -> Iterator[dict]:
    result = yield from _validation_stream(
        message=message,
        analysis=analysis,
        patch_plan=patch_plan,
        workspace_dir=workspace_dir,
        changed_files=changed_files,
    )
    if result:
        yield {"op": "done", "validation_result": result}


def _implementation_tools() -> list[dict]:
    return _tools_for(IMPLEMENTATION_TOOLS)


def _round_indexes(limit: int, start: int = 1):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


def _validation_tools() -> list[dict]:
    tools = _tools_for(VALIDATION_TOOLS)
    tools.append(openai_tool_schema("finish_validation", "Finish semantic validation with a structured verdict.", {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["passed", "local_repair", "redesign", "missing_evidence", "user_decision", "inconclusive", "failed"],
            },
            "summary": {"type": "string"},
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "severity": {"type": "string"},
                        "summary": {"type": "string"},
                        "file": {"type": "string"},
                        "line": {"type": "integer"},
                    },
                },
            },
            "repair_plan": {"type": "object"},
            "question": {"type": "string"},
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "label": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["id", "label"],
                },
            },
        },
        "required": ["verdict", "summary", "issues"],
    }))
    return tools


def _tools_for(names: tuple[str, ...]) -> list[dict]:
    tools = []
    for name in names:
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
            "attempt_id": {"type": "string"},
            "operation_summary": {"type": "string"},
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
        "required": ["step_id", "operation_summary", "files"],
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
    allowed = (auth.get("allowed_steps") or {}).get(step_id) or {}
    patched = dict(arguments)
    patched["authorization_id"] = auth.get("authorization_id", "")
    patched["plan_hash"] = auth.get("plan_hash", "")
    patched["acceptance_ids"] = allowed.get("acceptance_ids") or step.get("acceptance_ids") or []
    patched["purpose"] = allowed.get("purpose") or step.get("purpose") or ""
    patched["decision_ids"] = allowed.get("decision_ids") or step.get("decision_ids") or []
    patched["project_fact_ids"] = allowed.get("project_fact_ids") or step.get("project_fact_ids") or []
    patched["completion_conditions"] = allowed.get("completion_conditions") or step.get("completion_conditions") or []
    patched["required_behavior_if_removed"] = allowed.get("required_behavior_if_removed") or step.get("required_behavior_if_removed") or ""
    patched["reason"] = patched["purpose"] or step.get("action") or str(arguments.get("operation_summary") or "")
    patched["attempt_id"] = patched.get("attempt_id") or f"{step_id}-A1"
    patched["patch_id"] = patched.get("patch_id") or patched["attempt_id"]
    return patched


def _run_tool(name: str, call_id: str, arguments: dict, workspace_dir: str) -> Iterator[dict]:
    tool = registry.get(name)
    if name not in (*IMPLEMENTATION_TOOLS, *VALIDATION_TOOLS) or tool is None:
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

    event_type = "patch" if name in {"apply_patch", "patch_history", "rollback_patch"} else ("code_nav" if name == "code_nav" else "tool")
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


def _tool_retry_hint(name: str, error: str, patch_plan: dict) -> str:
    if name == "apply_patch" and "authorized patch plan" in error:
        steps = [
            str(item.get("id") or "")
            for item in patch_plan.get("implementation_steps") or []
            if item.get("id")
        ]
        suffix = f" Authorized step ids: {', '.join(steps)}." if steps else ""
        return (
            "Use exactly one authorized step_id per apply_patch call. "
            "Do not combine step ids such as IS1+IS2; call apply_patch once per step."
            + suffix
        )
    if "invalid tool JSON" in error:
        return "Call the tool again with valid JSON arguments only."
    return "Correct the tool arguments and retry. If the patch plan is wrong, stop and explain why."


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


def _code_nav_validated(output: str) -> bool:
    try:
        data = json.loads(output or "{}")
    except json.JSONDecodeError:
        return False
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return metadata.get("status") == "ok"


def _patch_files_from_output(output: str) -> list[str]:
    try:
        data = json.loads(output or "{}")
        payload = json.loads(data.get("output") or "{}")
    except (TypeError, json.JSONDecodeError):
        return []
    return [
        str(item.get("path") or "")
        for item in payload.get("files", [])
        if isinstance(item, dict) and item.get("path")
    ]


def _validation_stream(
    *,
    message: str,
    analysis: dict,
    patch_plan: dict,
    workspace_dir: str,
    changed_files: list[str],
) -> Iterator[dict]:
    setting = model_settings.resolve(model_settings.DEFAULT_STAGE)
    if setting is None:
        return _validation_result("inconclusive", "Semantic validation skipped; no model configured.", changed_files)
    provider = setting["provider"]
    model = setting["model_id"]
    pricing_rules = providers.get_model_pricing(provider["id"], model)
    usage_total = _empty_usage(pricing_rules)
    run_id = uuid4().hex[:10]
    stage_id = f"{run_id}-stage"
    yield start_event(stage_id, "stage", {
        "name": "validation",
        "label": "Validate implementation",
        "state": "running",
        "phase": "semantic_validation",
        "model": model,
        "context_length": providers.model_context_length(provider["base_url"], provider["api_key"], model),
        "provider": provider["name"],
        "inherited": setting["inherited"],
    })
    messages = [
        {"role": "system", "content": _validation_prompt(app_settings.get_output_language())},
        {"role": "user", "content": json.dumps({
            "user_request": message,
            "acceptance_criteria": analysis.get("acceptance_criteria", []),
            "patch_plan": patch_plan,
            "changed_files": sorted(set(changed_files)),
            "workspace_dir": workspace_dir,
        }, ensure_ascii=False)},
    ]
    tools = _validation_tools()
    semantic_checked = False
    validation_result = None
    for round_index in _round_indexes(app_settings.get_round_limit("validation_rounds")):
        assistant = _call_model(provider, model, messages, tools=tools)
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{round_index}", "usage", {"delta": usage, "total": usage_total})
        text = _content_text(assistant.get("content"))
        tool_calls = assistant.get("tool_calls") or []
        messages.append({"role": "assistant", "content": text, "tool_calls": tool_calls})
        if not tool_calls:
            messages.append({"role": "user", "content": "Call finish_validation with the required JSON verdict. Do not finish validation in prose."})
            continue
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name") or ""
            call_id = call.get("id") or f"{run_id}-tool-{round_index}"
            try:
                arguments = _tool_arguments(function.get("arguments"))
                if name == "finish_validation":
                    validation_result = _finish_validation_arguments(arguments, changed_files)
                    if not semantic_checked and validation_result["verdict"] == "passed":
                        output = json.dumps({
                            "error": "finish_validation verdict passed requires at least one successful code_nav call first.",
                            "retryable": True,
                        }, ensure_ascii=False)
                        validation_result = None
                    else:
                        output = json.dumps(validation_result, ensure_ascii=False)
                    yield start_event(call_id, "tool", {
                        "name": name,
                        "description": "Finish semantic validation",
                        "status": "error" if validation_result is None else "done",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                    })
                else:
                    output = yield from _run_tool(name, call_id, arguments, workspace_dir)
                if name == "code_nav" and _code_nav_validated(output):
                    semantic_checked = True
            except Exception as exc:
                output = json.dumps({"error": str(exc), "retryable": True}, ensure_ascii=False)
                yield start_event(call_id, "tool", {
                    "name": name or "invalid",
                    "description": "Validation tool",
                    "status": "error",
                    "open": False,
                    "input": function.get("arguments") or "{}",
                    "output": output,
                })
            messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
            if validation_result is not None:
                break
        if validation_result is not None:
            break
    else:
        yield start_event(f"{run_id}-checkpoint", "user_question", _checkpoint_question(
            analysis.get("id", ""),
            message,
            "Semantic validation reached its checkpoint before a clear pass/fail result.",
            checkpoint_phase="validation_checkpoint",
            patch_plan=patch_plan,
            changed_files=changed_files,
        ))
        yield {"op": "update", "id": stage_id, "patch": {"state": "waiting", "phase": "validation_checkpoint"}}
        return None
    yield start_event(f"{run_id}-output", "output", {
        "content": validation_result["summary"],
        "streaming": False,
    })
    yield {"op": "update", "id": stage_id, "patch": {"state": "done", "phase": "validation_done"}}
    return validation_result


def _validation_result(
    verdict: str,
    summary: str,
    changed_files: list[str],
    repair_plan: dict | None = None,
    issues: list[dict] | None = None,
    question: str = "",
    options: list[dict] | None = None,
) -> dict:
    return {
        "verdict": verdict,
        "summary": summary,
        "issues": issues or [],
        "changed_files": sorted(set(changed_files)),
        "repair_plan": repair_plan or {},
        "question": question,
        "options": options or [],
    }


def _finish_validation_arguments(arguments: dict, changed_files: list[str]) -> dict:
    verdict = _normalized_validation_verdict(str(arguments.get("verdict") or ""))
    summary = str(arguments.get("summary") or "").strip()
    if not summary:
        raise ValueError("finish_validation requires summary")
    issues = arguments.get("issues") if isinstance(arguments.get("issues"), list) else []
    repair_plan = arguments.get("repair_plan") if isinstance(arguments.get("repair_plan"), dict) else {}
    question = str(arguments.get("question") or "").strip()
    options = arguments.get("options") if isinstance(arguments.get("options"), list) else []
    if verdict == "passed" and issues:
        raise ValueError("passed verdict cannot include issues")
    if verdict == "local_repair" and not repair_plan:
        raise ValueError("local_repair verdict requires repair_plan")
    if verdict == "user_decision" and not question:
        raise ValueError("user_decision verdict requires question")
    return _validation_result(verdict, summary, changed_files, repair_plan, issues, question, options)


def _normalized_validation_verdict(value: str) -> str:
    value = value.strip().casefold()
    return value if value in {
        "passed",
        "local_repair",
        "redesign",
        "missing_evidence",
        "user_decision",
        "inconclusive",
        "failed",
    } else "inconclusive"


def _checkpoint_question(
    analysis_id: str,
    origin_message: str,
    reason: str,
    *,
    checkpoint_phase: str = "",
    patch_plan: dict | None = None,
    changed_files: list[str] | None = None,
) -> dict:
    return {
        "id": f"checkpoint-{uuid4().hex[:8]}",
        "analysis_id": analysis_id,
        "question": reason,
        "title": "Continue this run?",
        "summary": reason,
        "origin_message": origin_message,
        "checkpoint_phase": checkpoint_phase,
        "patch_plan": patch_plan or {},
        "changed_files": changed_files or [],
        "answer_status": "waiting",
        "options": [
            {"id": "continue", "label": "Continue", "value": "Continue from this checkpoint."},
            {"id": "stop", "label": "Stop", "value": "Stop and summarize the current state."},
        ],
    }


def _system_prompt(language: str) -> str:
    return f"""\
You are StratumCode's implementation runner. Write user-visible text in {language}.

Apply the already authorized patch plan. Do not redesign the solution.
Use read to obtain fresh snapshot_id values before modifying existing files.
Use apply_patch for all file writes. Its authorization_id, plan_hash, purpose,
reason, acceptance_ids, decision_ids, and project_fact_ids are injected by runtime;
do not include them. Provide step_id, operation_summary, optional attempt_id, and files.
For an existing
empty file, use replace_exact with old_text="" and new_text set to the whole file.
Each apply_patch call must use exactly one authorized implementation step_id.
Do not combine step ids such as "IS1+IS2"; call apply_patch separately for IS1
and IS2.

If apply_patch reports STALE_SNAPSHOT, read the file again and retry once with
the new snapshot. If authorization fails, stop and explain.
"""


def _validation_prompt(language: str) -> str:
    return f"""\
You are StratumCode's validation runner. Write user-visible text in {language}.

Validate the patch after implementation. Do not edit files in this stage.
Use code_nav for semantic checks, not only LSP diagnostics. Inspect changed
identifiers and member accesses that could resolve incorrectly.

Preserve explicit user contracts. If the user requested a signature such as
wait(int time), changing the parameter to seconds is a contract conflict. Prefer
reporting the conflict and the minimal repair direction, such as aliasing an
import (import time as t) instead of renaming the requested parameter.

Call code_nav at least once on a changed identifier before finalizing. If no LSP
server is available, report that semantic validation could not be completed.

Finish by calling finish_validation:
{{"verdict":"passed|local_repair|redesign|missing_evidence|user_decision|inconclusive|failed","summary":"...","issues":[],"repair_plan":{{}}}}
Use local_repair only when a minimal patch can fix the issue, and include a
repair_plan with implementation_steps. Use passed only when changed behavior
meets the acceptance criteria.
"""
