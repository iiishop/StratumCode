from __future__ import annotations

import asyncio
import ast
import json
from collections.abc import Iterator
from itertools import count
from pathlib import Path
from uuid import uuid4

from . import app_settings, model_settings, prompt, providers
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

IMPLEMENTATION_TOOLS = ("read", "apply_patch")
VALIDATION_TOOLS = ("read", "code_nav")
MAX_VALIDATION_ROUNDS = 3


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
        {"role": "system", "content": prompt.build_implementation_runner_system(app_settings.get_output_language())},
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
    no_patch_rounds = 0
    applied_steps: set[str] = set()
    changed_files: list[str] = []
    required_steps = _required_step_ids(patch_plan)
    patch_required = bool(required_steps)
    patch_call_count = 0
    patch_call_limit = max(len(required_steps) + 8, 12)

    tool_error_limit = app_settings.get_round_limit("implementation_tool_error_rounds")
    for round_index in _round_indexes(app_settings.get_round_limit("implementation_rounds")):
        assistant = _call_model(provider, model, messages, tools=tools)
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{round_index}", "usage", {"delta": usage, "total": usage_total})

        text = _content_text(assistant.get("content"))
        tool_calls = assistant.get("tool_calls") or []
        messages.append(_assistant_replay(text, tool_calls))
        if not tool_calls:
            final_text = text.strip()
            missing_steps = _missing_steps(required_steps, applied_steps)
            if missing_steps:
                messages.append({"role": "user", "content": (
                    "Implementation is not complete. Apply every authorized implementation step before finishing. "
                    f"Missing step ids: {', '.join(missing_steps)}."
                )})
                continue
            condition_issues = _completion_condition_issues(patch_plan, workspace_dir, applied_steps)
            if condition_issues:
                messages.append({"role": "user", "content": (
                    "Implementation is not complete. These completion conditions are still false:\n"
                    + "\n".join(f"- {item}" for item in condition_issues)
                    + "\nApply a focused repair patch using the affected authorized step id."
                )})
                continue
            break

        round_had_success = False
        round_applied_patch = False
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name") or ""
            call_id = call.get("id") or f"{run_id}-tool-{round_index}"
            try:
                arguments = _tool_arguments(function.get("arguments"))
                arguments = _prepare_tool_arguments(name, arguments, patch_plan)
                if name == "apply_patch":
                    patch_call_count += 1
                    if patch_call_count > patch_call_limit:
                        missing_steps = _missing_steps(required_steps, applied_steps)
                        reason = (
                            "Implementation exceeded the patch application budget without completing the authorized plan. "
                            f"Applied step ids: {', '.join(sorted(applied_steps))}. "
                            f"Missing step ids: {', '.join(missing_steps)}."
                        )
                        yield start_event(f"{run_id}-patch-budget-checkpoint", "output", _checkpoint_output(reason))
                        yield {"op": "update", "id": stage_id, "patch": {"state": "failed", "phase": "implementation_patch_budget_checkpoint"}}
                        return
                output = yield from _run_tool(name, call_id, arguments, workspace_dir)
                round_had_success = round_had_success or not _tool_failed(output)
                if name == "apply_patch" and (not _tool_failed(output) or _patch_already_applied(output)):
                    round_applied_patch = True
                    applied_steps.add(str(arguments.get("step_id") or "").strip())
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
        if patch_required and not round_applied_patch:
            no_patch_rounds += 1
            missing_steps = _missing_steps(required_steps, applied_steps)
            if no_patch_rounds == 3:
                messages.append({"role": "user", "content": (
                    "Stop reading files unless a stale snapshot error requires it. "
                    "Apply the authorized patch now. Missing step ids: "
                    f"{', '.join(missing_steps)}."
                )})
            elif no_patch_rounds >= 6:
                checkpoint_reason = (
                    "Implementation repeatedly inspected files without applying any authorized patch step."
                    if not applied_steps
                    else "Implementation applied some patch steps but stopped making progress on the remaining authorized steps."
                )
                reason = checkpoint_reason + (
                        f" Applied step ids: {', '.join(sorted(applied_steps))}. "
                        f"Missing step ids: {', '.join(missing_steps)}."
                        if applied_steps else ""
                    )
                yield start_event(f"{run_id}-no-patch-checkpoint", "output", _checkpoint_output(reason))
                yield {"op": "update", "id": stage_id, "patch": {"state": "failed", "phase": "implementation_no_patch_checkpoint"}}
                return
        elif round_applied_patch:
            no_patch_rounds = 0
        if tool_error_limit and consecutive_error_rounds >= tool_error_limit:
            yield start_event(f"{run_id}-tool-error-checkpoint", "output", _checkpoint_output(
                "Implementation hit repeated tool errors. The patch plan or file path likely needs correction."
            ))
            yield {"op": "update", "id": stage_id, "patch": {"state": "failed", "phase": "implementation_tool_error_checkpoint"}}
            return
    else:
        missing_steps = _missing_steps(required_steps, applied_steps)
        if missing_steps:
            yield start_event(f"{run_id}-checkpoint", "output", _checkpoint_output(
                "Implementation reached its checkpoint before applying every authorized patch step."
            ))
            yield {"op": "update", "id": stage_id, "patch": {"state": "failed", "phase": "implementation_checkpoint"}}
            return
        final_text = "Implementation patching reached its checkpoint; continuing with validation."

    missing_steps = _missing_steps(required_steps, applied_steps)
    if missing_steps:
        yield start_event(f"{run_id}-checkpoint", "output", _checkpoint_output(
            "Implementation stopped before applying every authorized patch step."
        ))
        yield {"op": "update", "id": stage_id, "patch": {"state": "failed", "phase": "implementation_checkpoint"}}
        return
    condition_issues = _completion_condition_issues(patch_plan, workspace_dir, applied_steps)
    if condition_issues:
        yield start_event(f"{run_id}-checkpoint", "output", _checkpoint_output(
            "Implementation stopped with unmet completion conditions:\n" + "\n".join(f"- {item}" for item in condition_issues)
        ))
        yield {"op": "update", "id": stage_id, "patch": {"state": "failed", "phase": "implementation_completion_checkpoint"}}
        return

    yield start_event(f"{run_id}-output", "output", {
        "content": final_text or "Implementation finished.",
        "streaming": False,
    })
    yield {"op": "update", "id": stage_id, "patch": {"state": "done", "phase": "implementation_done"}}
    yield {"op": "done", "implementation": {
        "status": "patch_applied" if applied_steps else "no_patch",
        "summary": final_text,
        "patch_applied": bool(applied_steps),
        "applied_steps": sorted(applied_steps),
        "missing_steps": missing_steps,
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


def _required_step_ids(patch_plan: dict) -> set[str]:
    return {
        step_id
        for item in patch_plan.get("implementation_steps") or []
        if isinstance(item, dict) and (step_id := str(item.get("id") or "").strip())
    }


def _missing_steps(required: set[str], applied: set[str]) -> list[str]:
    return sorted(required - applied)


def _completion_condition_issues(patch_plan: dict, workspace_dir: str, applied_steps: set[str]) -> list[str]:
    root = Path(workspace_dir or ".").resolve()
    issues = []
    for step in patch_plan.get("implementation_steps") or []:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "").strip()
        if step_id not in applied_steps:
            continue
        rel = str(step.get("file") or "").strip()
        path = (root / rel).resolve() if rel else root
        for condition in step.get("completion_conditions") or []:
            issue = _completion_condition_issue(step_id, rel, path, str(condition or ""))
            if issue:
                issues.append(issue)
    return issues


def _completion_condition_issue(step_id: str, rel: str, path: Path, condition: str) -> str:
    lowered = condition.casefold()
    exists = path.exists()
    if _means_file_absent(lowered) and exists:
        return f"{step_id}: expected {rel} to be absent ({condition})"
    if _means_file_present(lowered) and not exists:
        return f"{step_id}: expected {rel} to exist ({condition})"
    literals = _backtick_literals(condition)
    if not literals or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"{step_id}: could not read {rel} to verify {condition}"
    if _means_literal_absent(lowered):
        present = [literal for literal in literals if _literal_present(literal, text)]
        if present:
            return f"{step_id}: {rel} still contains {', '.join(present)}"
    elif _means_literal_present(lowered):
        missing = [literal for literal in literals if not _literal_present(literal, text)]
        if missing:
            return f"{step_id}: {rel} is missing {', '.join(missing)}"
    return ""


def _backtick_literals(text: str) -> list[str]:
    values = []
    parts = str(text or "").split("`")
    for index in range(1, len(parts), 2):
        literal = parts[index].strip()
        if literal:
            values.append(literal)
    return values


def _literal_present(literal: str, text: str) -> bool:
    if literal in text:
        return True
    return _from_import_present(literal, text)


def _from_import_present(literal: str, text: str) -> bool:
    literal = literal.strip()
    if not literal.startswith("from ") or " import " not in literal:
        return False
    module, names_text = literal[5:].split(" import ", 1)
    names = {item.strip().split(" as ", 1)[0] for item in names_text.split(",") if item.strip()}
    if not module.strip() or not names:
        return False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module.strip():
            imported = {alias.name for alias in node.names}
            if names <= imported:
                return True
    return False


def _means_file_absent(text: str) -> bool:
    return ("不存在" in text and "包含" not in text) or any(
        term in text
        for term in ("file does not exist", "file not exist", "file absent", "file deleted")
    )


def _means_file_present(text: str) -> bool:
    return any(term in text for term in ("存在", "exists", "present")) and not _means_file_absent(text)


def _means_literal_absent(text: str) -> bool:
    return any(term in text for term in ("不再包含", "不包含", "no longer contains", "does not contain", "not contain", "absent", "removed"))


def _means_literal_present(text: str) -> bool:
    return any(term in text for term in ("包含", "contains", "include", "present"))

def _round_indexes(limit: int, start: int = 1):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


def _assistant_replay(content: str, tool_calls: list[dict]) -> dict:
    message = {"role": "assistant", "content": content or ""}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


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


def _patch_already_applied(output: str) -> bool:
    try:
        data = json.loads(output or "{}")
    except json.JSONDecodeError:
        return False
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return metadata.get("code") == "PATCH_ATTEMPT_ALREADY_APPLIED"


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
        {"role": "system", "content": prompt.build_validation_runner_system(app_settings.get_output_language())},
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
    for round_index in _round_indexes(app_settings.get_round_limit("validation_rounds") or MAX_VALIDATION_ROUNDS):
        assistant = _call_model(provider, model, messages, tools=tools)
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{round_index}", "usage", {"delta": usage, "total": usage_total})
        text = _content_text(assistant.get("content"))
        tool_calls = assistant.get("tool_calls") or []
        messages.append(_assistant_replay(text, tool_calls))
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
        yield start_event(f"{run_id}-checkpoint", "output", _checkpoint_output(
            "Semantic validation reached its checkpoint before a clear pass/fail result."
        ))
        yield {"op": "update", "id": stage_id, "patch": {"state": "failed", "phase": "validation_checkpoint"}}
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
    question = str(arguments.get("question") or "").strip()
    options = arguments.get("options") if isinstance(arguments.get("options"), list) else []
    if verdict == "passed" and issues:
        raise ValueError("passed verdict cannot include issues")
    if verdict == "user_decision" and not question:
        raise ValueError("user_decision verdict requires question")
    return _validation_result(verdict, summary, changed_files, {}, issues, question, options)


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


def _checkpoint_output(reason: str) -> dict:
    return {
        "content": f"{reason}\n\nlegacy checkpoint is disabled; rerun with clearer instructions if needed.",
        "streaming": False,
    }
