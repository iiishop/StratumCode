from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import re
from collections.abc import Iterator
from itertools import count
from pathlib import Path
from uuid import uuid4

from . import app_settings, clearify_runtime, model_settings, prompt, providers, skill_runtime
from .agent.tools import openai_tool_schema
from .agent_runtime import (
    add_usage as _add_usage,
    call_model as _call_model,
    assistant_message as _assistant_message,
    assistant_visible_text as _assistant_visible_text,
    content_text as _content_text,
    empty_usage as _empty_usage,
    execute_skill_tool_call,
    start_event,
    tool_error_json,
    usage_delta as _usage_delta,
)
from .json2slots import JSONValue, json2slots
from .status.task_contract import (
    LEGACY_ASK_USER_STRATEGY,
    LEGACY_NEEDS_USER_STATUS,
    _unknowns as _contract_unknowns,
)
from .status.task_analysis import _analysis_requests_implementation, _json_candidates
from .status.task_updates import _unknown_task_status
from .tools import registry

INVESTIGATION_CAPABILITY = "investigation"
PROJECT_EVIDENCE_CAPABILITY = "investigation.project_evidence"
MAX_REPEATED_TOOL_ERRORS = 3
READ_ONLY_SUMMARY_MIN_RESOLUTION_RATIO = 0.35
DEBT_REPORT_TERMS = (
    "技术债", "技術債", "死代码", "死碼", "technical debt", "dead code",
)
ENTRYPOINT_TERMS = ("main.py", "entrypoint", "entry point", "入口")
PACKAGE_SHAPE_TERMS = (
    "used_in_project: false", "无入口", "沒有入口", "没有 cli", "沒有 cli",
    "no cli", "no script", "没有脚本", "沒有腳本", "pyproject.toml",
    "setup.py", "__init__.py",
)
ENTRYPOINT_OVERCLAIM_TERMS = (
    "项目不能运行", "项目无法运行", "不能运行", "无法运行",
    "project cannot run", "cannot run the project",
)
ENTRYPOINT_OVERCLAIM_PATTERN = re.compile(
    r"(?:项目)?(?:不能|无法|無法|不可).{0,12}运行"
    r"|(?:the\s+)?project cannot run|cannot run the project",
    re.IGNORECASE,
)
MAX_REPEATED_RECORD_NO_PROGRESS = 3
MAX_DUPLICATE_NO_PROGRESS = 2
REQUIRED_FINDING_SLOT_ATTEMPTS = 2
REQUIRED_AUDIT_ATTEMPTS = 2
OBSERVATION_EVIDENCE_CHARS = 8000
DISCOVERY_CONTRACT_FIELDS = (
    "hypothesis",
    "expected_observation",
    "decision_impact",
    "stop_condition",
)
FINDING_FIELDS = (
    "beliefs",
    "resolutions",
    "new_unknowns",
    "unknowns",
    "user_decisions_required",
    "task_updates",
)
RESOLUTION_KINDS = {"direct_fact", "derived_inference", "user_decision", "deferred"}
SEMANTIC_AUDIT_KINDS = {"derived_inference"}
# Compatibility hook for integrations that patched this set before tool capabilities existed.
PROJECT_EVIDENCE_TOOLS: set[str] = set()
CLEARIFY_RESOLUTION_REASON = "Answered by the user through clearify."
CLEARIFY_UNRESOLVED_REASON = "User could not answer through clearify; continue project investigation."
GROUNDING_LITERAL_REASON_PREFIX = "Cited observations do not contain the claimed code literal(s):"
STATE_WRITE_REASON_PREFIX = "Cited observations contain state writes omitted from the resolution:"
RECORD_RECOVERY_REASON = "Record pending observations and required resolutions."


def investigation_stream(
    *,
    message: str,
    analysis: dict,
    context: list[str],
    workspace_dir: str,
    max_rounds: int | None = None,
    findings: list[str] | None = None,
    previous_observations: list[dict] | None = None,
    previous_knowledge: list[dict] | None = None,
    previous_findings: dict | None = None,
    preserve_grounding_evidence: bool = False,
) -> Iterator[dict]:
    setting = (
        model_settings.resolve(model_settings.DEFAULT_STAGE)
        or model_settings.resolve(model_settings.EVIDENCE_STAGE)
    )
    if setting is None:
        raise ValueError(
            "No model configured for investigation. Configure a default or evidence model in Providers."
        )

    provider = setting["provider"]
    model = setting["model_id"]
    pricing_rules = providers.get_model_pricing(provider["id"], model)
    usage_total = _empty_usage(pricing_rules)
    max_rounds = app_settings.get_round_limit("investigation_rounds") if max_rounds is None else int(max_rounds or 0)
    run_id = uuid4().hex[:10]
    stage_id = f"{run_id}-stage"
    yield start_event(stage_id, "stage", {
        "name": "investigation",
        "label": "Investigate before patch planning",
        "state": "running",
        "phase": "understand",
        "model": model,
        "context_length": providers.model_context_length(provider["base_url"], provider["api_key"], model),
        "provider": provider["name"],
        "inherited": setting["inherited"],
    })

    messages = [
        {"role": "system", "content": prompt.build_investigation_static(app_settings.get_output_language())},
        {
            "role": "user",
            "content": prompt.build_investigation_context(
                analysis=analysis,
                message=message,
                directory=workspace_dir,
                platform=platform.system(),
                model=model,
                context=context,
                max_rounds=max_rounds,
            ),
        },
        {"role": "user", "content": message},
    ]
    if findings:
        messages.insert(2, {"role": "user", "content": "\n".join(findings)})
    prior_lines = _previous_context(previous_observations, previous_knowledge)
    if prior_lines:
        messages.insert(2, {"role": "user", "content": "\n".join(prior_lines)})
    tools = _investigation_tools()
    final = None
    observations = [
        dict(item)
        for item in previous_observations or []
        if isinstance(item, dict) and item.get("fresh", True)
    ]
    tool_cache = {}
    tool_cache_observation_ids: dict[str, str] = {}
    audit_cache: dict[str, dict] = {}
    recorded_findings = _continued_recorded_findings(previous_findings, observations)
    finalization_reason = "Investigation model stopped before finish_investigation; summarizing observed facts."
    pending_observation_ids: list[str] = []
    repeated_tool_error_name = ""
    repeated_tool_error_count = 0
    failed_tool_cache: dict[str, str] = {}
    stop_investigation = False
    verification_queue: list[dict] = []
    attempted_verifications: set[tuple[str, str]] = set()
    clearify_questions: dict[str, str] = {}
    semantic_repair_required_ids = _semantic_repair_resolution_ids(recorded_findings)
    last_quality_audit: dict = {}
    last_record_signature = _recorded_findings_signature(recorded_findings)
    repeated_record_no_progress = 0
    duplicate_no_progress_signature = ""
    duplicate_no_progress_count = 0
    force_synthesis_reason = ""

    for round_index in _round_indexes(max_rounds, start=0):
        thinking_id = f"{run_id}-thinking-{round_index}"
        yield start_event(thinking_id, "thinking", {"text": "", "done": False, "open": True})
        current_tools = tools
        current_tool_choice = "required"
        clearify_unknown = _pending_clearify_unknown(
            recorded_findings,
            analysis,
            clearify_questions,
        )
        verification_request = verification_queue[0] if verification_queue else None
        resolution_required_ids = _unknowns_needing_resolution(recorded_findings, observations, analysis)
        resolution_required_ids = _dedupe_strings([
            *resolution_required_ids,
            *sorted(semantic_repair_required_ids),
        ])
        if clearify_unknown:
            messages.append({"role": "user", "content": _clearify_required_prompt(clearify_unknown)})
            current_tools = [
                tool for tool in tools
                if ((tool.get("function") or {}).get("name") or "") == "clearify"
            ]
            current_tool_choice = {"type": "function", "function": {"name": "clearify"}}
        elif verification_request:
            messages.append({"role": "user", "content": (
                "The semantic quality gate requires independent verification before this "
                "resolution can reach Design. Call subagent with agent hypothesis-verifier "
                f"for unknown {verification_request['unknown_id']} and the exact atomic "
                f"hypothesis: {verification_request['hypothesis']}"
            )})
            current_tools = [
                tool for tool in tools
                if ((tool.get("function") or {}).get("name") or "") == "subagent"
            ]
            current_tool_choice = {"type": "function", "function": {"name": "subagent"}}
        elif semantic_repair_required_ids:
            reasons = {
                str(item.get("unknown_id") or ""): str(item.get("reason") or "")
                for item in recorded_findings.get("resolutions", [])
                if isinstance(item, dict)
                and str(item.get("unknown_id") or "") in semantic_repair_required_ids
            }
            messages.append({"role": "user", "content": (
                "The semantic quality gate rejected these recorded resolutions. "
                "Call resolve_unknowns now to replace only these resolutions "
                "with claims supported by the observations; finish_investigation is not "
                f"allowed until they pass: {json.dumps(reasons, ensure_ascii=False)}"
            )})
            current_tools = [_resolve_unknowns_tool_schema()]
            current_tool_choice = {
                "type": "function",
                "function": {"name": "resolve_unknowns"},
            }
        elif _recorded_resolves_initial_unknowns(recorded_findings, analysis):
            current_tools = [_finish_tool_schema()]
            current_tool_choice = {"type": "function", "function": {"name": "finish_investigation"}}
        elif force_synthesis_reason:
            messages.append({"role": "user", "content": force_synthesis_reason})
            current_tools = [_resolve_unknowns_tool_schema(), _record_findings_tool_schema(), _finish_tool_schema()]
        elif (
            analysis.get("execution_mode") == "read_only"
            and not analysis.get("unknowns")
        ):
            messages.append({"role": "user", "content": (
                "The task contract has no project facts to investigate. Answer the user's "
                "request directly in finish_investigation.summary. The summary must satisfy "
                "the acceptance criteria; do not merely classify, restate, or explain why "
                "the request does not require project inspection. Do not mention the current "
                "workspace, speculate about its code, or offer additional project work unless "
                "the user requested it."
            )})
            current_tools = [_finish_tool_schema()]
            current_tool_choice = {"type": "function", "function": {"name": "finish_investigation"}}
        elif resolution_required_ids:
            messages.append({"role": "user", "content": _resolution_required_prompt(
                resolution_required_ids,
                recorded_findings,
                observations,
                analysis,
            )})
            current_tools = [_resolve_unknowns_tool_schema(), _record_findings_tool_schema(), _finish_tool_schema()]
        allowed_tool_names = {
            str(((tool.get("function") or {}).get("name")) or "")
            for tool in current_tools
            if isinstance(tool, dict)
        }
        try:
            assistant = _call_model(provider, model, messages, tools=current_tools, tool_choice=current_tool_choice)
        except ValueError as exc:
            reason = str(exc)
            yield {"op": "update", "id": thinking_id, "patch": {
                "text": reason,
                "done": True,
                "open": False,
            }}
            yield start_event(f"{run_id}-provider-error", "output", {
                "content": f"Provider request failed: {reason}",
                "streaming": False,
            })
            yield {"op": "update", "id": stage_id, "patch": {"state": "failed", "phase": "provider_error"}}
            return
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{round_index}", "usage", {
                "delta": usage,
                "total": usage_total,
            })

        tool_calls = assistant.get("tool_calls") or []
        content = _assistant_visible_text(assistant) or _tool_call_summary(tool_calls)
        messages.append(_assistant_message(assistant))
        yield {"op": "update", "id": thinking_id, "patch": {
            "text": content,
            "done": True,
            "open": bool(tool_calls),
        }}

        if not tool_calls:
            messages.append({"role": "user", "content": (
                "You did not call a tool. Continue by making an actual tool call, "
                "or call finish_investigation if the investigation is complete. "
                "Do not describe intended tool use in prose."
            )})
            continue

        round_error_names: set[str] = set()
        asked_clearify_ids: set[str] = set()
        for raw_call in tool_calls:
            call_id = raw_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = raw_call.get("function") or {}
            name = function.get("name") or ""
            arguments = {}
            if name == "load_skill":
                _, output, _ = execute_skill_tool_call(raw_call)
                yield from skill_runtime.pop_events()
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": output,
                })
                continue
            try:
                arguments = _investigation_tool_arguments(
                    name,
                    function.get("arguments"),
                    pending_observation_ids=pending_observation_ids,
                    resolution_required_ids=resolution_required_ids,
                )
                if name == "record_investigation_findings":
                    arguments = _record_arguments(arguments)
                if verification_request and name == "subagent":
                    arguments = {
                        "agent": "hypothesis-verifier",
                        "task": json.dumps({
                            "hypothesis": verification_request["hypothesis"],
                            "context": [
                                verification_request.get("reason", ""),
                                f"Target contract unknown: {verification_request['unknown_id']}",
                            ],
                        }, ensure_ascii=False),
                        "target_unknown_ids": [verification_request["unknown_id"]],
                        "reason": verification_request.get("reason")
                        or "Independently verify the material investigation inference.",
                    }
                failed_key = _tool_cache_key(name, arguments)
                if failed_key in failed_tool_cache:
                    repeated_tool_error_name = name or "invalid"
                    repeated_tool_error_count += 1
                    output = json.dumps({
                        "error": {
                            "code": "duplicate_failed_tool_call",
                            "tool": name or "invalid",
                            "retryable": False,
                            "message": (
                                "The same tool arguments already failed. "
                                "Choose a different valid action; do not retry this call."
                            ),
                        },
                    }, ensure_ascii=False)
                    yield start_event(call_id, registry.event_type(name), {
                        "name": name or "invalid",
                        "description": "Investigation tool",
                        "status": "error",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                        "deduplicated": True,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                    if repeated_tool_error_count >= MAX_REPEATED_TOOL_ERRORS:
                        finalization_reason = (
                            "Runtime stopped an identical failed tool call loop: "
                            f"{name or 'invalid'}."
                        )
                        stop_investigation = True
                        break
                    continue
                if name not in allowed_tool_names:
                    if _recorded_resolves_initial_unknowns(recorded_findings, analysis):
                        output = json.dumps({
                            "error": "investigation_already_resolved",
                            "retryable": True,
                            "required_tool": "finish_investigation",
                            "message": (
                                "All initial unknowns are resolved. Call finish_investigation now; "
                                "do not repeat discovery or recording tools."
                            ),
                        }, ensure_ascii=False)
                        yield start_event(call_id, registry.event_type(name), {
                            "name": name or "invalid",
                            "description": "Investigation tool",
                            "status": "error",
                            "open": False,
                            "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                            "output": output,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        continue
                    if resolution_required_ids:
                        output = json.dumps({
                            "error": "resolution_required",
                            "retryable": True,
                            "required_tool": "resolve_unknowns",
                            "target_unknown_ids": resolution_required_ids,
                            "message": (
                                "Existing project evidence is already recorded for these unknowns. "
                                "Resolve explicit unknowns before calling more discovery tools."
                            ),
                        }, ensure_ascii=False)
                        yield start_event(call_id, registry.event_type(name), {
                            "name": name or "invalid",
                            "description": "Investigation tool",
                            "status": "error",
                            "open": False,
                            "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                            "output": output,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        continue
                    output = _tool_blocked_error_json(
                        name,
                        allowed_tools=sorted(allowed_tool_names),
                    )
                    yield start_event(call_id, registry.event_type(name), {
                        "name": name or "invalid",
                        "description": "Investigation tool",
                        "status": "error",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                    messages.append({"role": "user", "content": (
                        "The tool was blocked by the current investigation state. "
                        "Choose one of the allowed tools from the tool result; do not retry "
                        "the blocked discovery call with the same arguments."
                    )})
                    continue
                if name == "resolve_unknowns":
                    arguments = _resolve_unknown_arguments(arguments)
                    _require_control_reason(arguments, name)
                    resolutions = _resolutions(arguments.get("resolutions"))
                    if not resolutions:
                        raise ValueError("resolve_unknowns requires at least one valid resolution")
                    _validate_resolution_refs(
                        resolutions,
                        _beliefs(recorded_findings.get("beliefs")),
                        observations,
                    )
                    recorded_findings = _merge_recorded_findings(
                        recorded_findings,
                        {"resolutions": resolutions},
                    )
                    recorded_findings = _bind_grounding_evidence(
                        recorded_findings,
                        observations,
                    )
                    pending_observation_ids = [
                        item for item in pending_observation_ids
                        if item not in {
                            evidence_id
                            for resolution in resolutions
                            for evidence_id in resolution.get("evidence", [])
                        }
                    ]
                    semantic_repair_required_ids = _semantic_repair_resolution_ids(
                        recorded_findings,
                    )
                    task_updates = _investigation_task_updates(
                        None,
                        _initial_unknowns(_analysis_with_recorded_unknowns(analysis, recorded_findings)),
                        resolutions,
                    )
                    output = json.dumps({
                        "resolved": True,
                        "counts": {"resolutions": len(resolutions)},
                        "unknown_ids": [item["unknown_id"] for item in resolutions],
                    }, ensure_ascii=False)
                    yield start_event(call_id, "tool", {
                        "name": name,
                        "description": "Resolve investigation unknowns",
                        "status": "done",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                        "symbol": "R",
                    })
                    if task_updates:
                        yield start_event(f"{call_id}-task-update", "task_update", {
                            "analysis_id": analysis.get("id", ""),
                            "items": task_updates,
                        })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                    continue
                if name == "record_investigation_findings":
                    _require_control_reason(arguments, name)
                    if (
                        pending_observation_ids
                        and not resolution_required_ids
                        and not _has_finding_fields(arguments)
                    ):
                        output = json.dumps({
                            "recorded": False,
                            "code": "no_material_findings",
                            "pending_observation_ids": pending_observation_ids,
                            "next_action": "continue_discovery",
                        }, ensure_ascii=False)
                        yield start_event(call_id, "tool", {
                            "name": name,
                            "description": "Record investigation findings",
                            "status": "done",
                            "open": False,
                            "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                            "output": output,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        continue
                    if analysis.get("_canonicalized") or not _has_finding_fields(arguments):
                        if not pending_observation_ids and not resolution_required_ids:
                            output = json.dumps({
                                "recorded": False,
                                "code": "nothing_to_record",
                                "next_action": "continue_discovery",
                                "message": "No pending observations or unresolved evidence-backed resolutions are available to record.",
                            }, ensure_ascii=False)
                            yield start_event(call_id, "tool", {
                                "name": name,
                                "description": "Record investigation findings",
                                "status": "done",
                                "open": False,
                                "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                                "output": output,
                            })
                            messages.append({
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": output,
                            })
                            continue
                        arguments = yield from _record_findings_by_slots(
                            provider=provider,
                            model=model,
                            messages=messages[:-1],
                            pricing_rules=pricing_rules,
                            usage_total=usage_total,
                            run_id=run_id,
                            reason=str(arguments.get("reason") or "").strip(),
                            analysis=analysis,
                            observations=observations,
                            recorded_findings=recorded_findings,
                            pending_observation_ids=pending_observation_ids,
                            required_resolution_ids=resolution_required_ids,
                        )
                    if _empty_discovery_recording(
                        arguments,
                        pending_observation_ids,
                        resolution_required_ids,
                    ):
                        output = json.dumps({
                            "recorded": False,
                            "code": "no_material_findings",
                            "pending_observation_ids": pending_observation_ids,
                            "next_action": "continue_discovery",
                        }, ensure_ascii=False)
                        yield start_event(call_id, "tool", {
                            "name": name,
                            "description": "Record investigation findings",
                            "status": "done",
                            "open": False,
                            "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                            "output": output,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        continue
                    _require_finding_fields(arguments)
                    recorded_findings = _merge_recorded_findings(recorded_findings, arguments)
                    recorded_findings = _bind_grounding_evidence(
                        recorded_findings,
                        observations,
                    )
                    pending_observation_ids.clear()
                    record_signature = _recorded_findings_signature(recorded_findings)
                    if record_signature == last_record_signature:
                        repeated_record_no_progress += 1
                    else:
                        repeated_record_no_progress = 0
                    last_record_signature = record_signature
                    if repeated_record_no_progress >= MAX_REPEATED_RECORD_NO_PROGRESS:
                        finalization_reason = (
                            "Runtime stopped after repeated record_investigation_findings "
                            "calls produced no semantic progress."
                        )
                        stop_investigation = True
                    task_updates = _record_task_updates(recorded_findings)
                    output = json.dumps({
                        "recorded": True,
                        "counts": {field: len(recorded_findings.get(field, [])) for field in FINDING_FIELDS},
                        **({"stalled": True} if stop_investigation else {}),
                    }, ensure_ascii=False)
                    yield start_event(call_id, "tool", {
                        "name": name,
                        "description": "Record investigation findings",
                        "status": "done",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                    })
                    if task_updates:
                        yield start_event(f"{call_id}-task-update", "task_update", {
                            "analysis_id": analysis.get("id", ""),
                            "items": task_updates,
                        })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                    if stop_investigation:
                        yield start_event(f"{run_id}-safety-record-no-progress", "safety_stop", {
                            "reason": "record_no_progress",
                            "message": finalization_reason,
                            "tool": name,
                        })
                        break
                    continue
                if name == "finish_investigation":
                    _require_control_reason(arguments, name)
                    recorded_findings = _apply_direct_resolution_gate(
                        recorded_findings,
                        observations,
                        strict_grounding=_analysis_requests_implementation(analysis),
                    )
                    semantic_repair_required_ids = _semantic_repair_resolution_ids(
                        recorded_findings,
                    )
                    if (
                        analysis.get("_canonicalized")
                        and not _audit_covers_resolutions(
                            last_quality_audit,
                            recorded_findings,
                            analysis,
                        )
                    ):
                        last_quality_audit = yield from _audit_recorded_findings(
                            provider=provider,
                            model=model,
                            messages=[
                                item for item in messages[:-1]
                                if item.get("role") != "system"
                            ],
                            pricing_rules=pricing_rules,
                            usage_total=usage_total,
                            run_id=run_id,
                            analysis=analysis,
                            observations=observations,
                            recorded_findings=recorded_findings,
                            audit_cache=audit_cache,
                        )
                        recorded_findings, requests, questions = _apply_investigation_audit(
                            recorded_findings,
                            last_quality_audit,
                            observations=observations,
                            strict_grounding=_analysis_requests_implementation(analysis),
                            allow_verification=_analysis_requests_implementation(analysis),
                        )
                        semantic_repair_required_ids = _semantic_repair_resolution_ids(
                            recorded_findings,
                        )
                        attempted = attempted_verifications | {
                            (item.get("unknown_id"), item.get("hypothesis"))
                            for item in verification_queue
                        }
                        verification_queue.extend(
                            item for item in requests
                            if (item.get("unknown_id"), item.get("hypothesis")) not in attempted
                            and _unknown_blocks_finish(item.get("unknown_id"), analysis, recorded_findings)
                        )
                        clearify_questions.update({
                            unknown_id: question
                            for unknown_id, question in questions.items()
                            if _unknown_blocks_finish(unknown_id, analysis, recorded_findings)
                        })
                    pending_resolution_statuses = {
                        str(item.get("status") or "")
                        for item in recorded_findings.get("resolutions", [])
                        if isinstance(item, dict)
                        and _unknown_blocks_finish(item.get("unknown_id"), analysis, recorded_findings)
                    }
                    if (
                        verification_queue
                        or clearify_questions
                        or pending_resolution_statuses & {"partially_resolved", "needs_clearify"}
                    ):
                        output = json.dumps({
                            "finished": False,
                            "reason": "semantic_quality_gate",
                            "audit": last_quality_audit,
                            "next_action": (
                                "clearify"
                                if "needs_clearify" in pending_resolution_statuses or clearify_questions
                                else "verify_hypothesis"
                                if verification_queue
                                else "continue_investigation"
                            ),
                        }, ensure_ascii=False)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        messages.append({"role": "user", "content": (
                            "The semantic quality gate did not authorize every resolution. "
                            "Follow its next action; do not finish or reuse the rejected conclusion."
                        )})
                        break
                    final = _finish_payload(
                        _finish_arguments(
                            recorded_findings,
                            arguments,
                            prefer_finish_summary=not _analysis_requests_implementation(analysis),
                        ),
                        analysis=analysis,
                        observations=observations,
                        repair_conflicts=True,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(final, ensure_ascii=False),
                    })
                    if final.get("resolution_repair"):
                        finalization_reason = "Investigation findings need explicit resolutions before finalizing."
                        messages.append({
                            "role": "user",
                            "content": _resolution_repair_prompt(final["resolution_repair"]),
                        })
                        final = None
                        stop_investigation = True
                    break
                if name == "clearify":
                    if clearify_unknown:
                        arguments["target_unknown_ids"] = [clearify_unknown["id"]]
                        arguments.setdefault("reason", "Resolve the blocking product decision.")
                        arguments.setdefault("question", clearify_unknown["question"])
                    target_ids = _target_unknown_ids(arguments)
                    target_ids = [
                        item for item in target_ids
                        if not any(_same_unknown_id(item, asked_id) for asked_id in asked_clearify_ids)
                    ]
                    arguments["target_unknown_ids"] = target_ids
                    if not target_ids:
                        output = json.dumps({
                            "skipped": True,
                            "reason": "These clearify unknowns were already asked in the current round.",
                        }, ensure_ascii=False)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        continue
                    _validate_tool_contract(
                        name,
                        target_unknown_ids=target_ids,
                        reason=str(arguments.get("reason") or "").strip(),
                        orientation=bool(arguments.get("orientation", False)),
                        analysis=_analysis_with_recorded_unknowns(
                            analysis,
                            recorded_findings,
                        ),
                    )
                    answered_by_previous_round = {
                        str(item.get("unknown_id") or "").strip()
                        for item in recorded_findings.get("resolutions", [])
                        if isinstance(item, dict)
                        and item.get("reason") == CLEARIFY_RESOLUTION_REASON
                    }
                    target_ids = [
                        item for item in target_ids
                        if not any(
                            _same_unknown_id(item, answered_id)
                            for answered_id in answered_by_previous_round
                        )
                    ]
                    arguments["target_unknown_ids"] = target_ids
                    if not target_ids:
                        output = json.dumps({
                            "skipped": True,
                            "reason": "These clearify unknowns already have authoritative user answers.",
                        }, ensure_ascii=False)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        continue
                    question_id = clearify_runtime.create_pending()
                    yield start_event(question_id, "user_question", _clearify_question(
                        arguments,
                        question_id=question_id,
                        analysis=analysis,
                    ))
                    answer = clearify_runtime.wait(question_id)
                    output = _clearify_tool_result(answer)
                    resolution_records = _clearify_resolution_records(arguments, answer)
                    repeated_tool_error_name = ""
                    repeated_tool_error_count = 0
                    if resolution_records:
                        for resolution in resolution_records:
                            asked_clearify_ids.add(resolution["unknown_id"])
                            for question_id in list(clearify_questions):
                                if _same_unknown_id(question_id, resolution["unknown_id"]):
                                    clearify_questions.pop(question_id, None)
                        recorded_findings = _merge_recorded_findings(
                            recorded_findings,
                            {"resolutions": resolution_records},
                        )
                    yield start_event(call_id, "tool", {
                        "name": name,
                        "description": "Ask the user for clarification",
                        "status": "done",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                    })
                    if resolution_records:
                        yield start_event(f"{call_id}-task-update", "task_update", {
                            "analysis_id": analysis.get("id", ""),
                            "items": _investigation_task_updates(
                                None,
                                _initial_unknowns(_analysis_with_recorded_unknowns(analysis, recorded_findings)),
                                resolution_records,
                            ),
                        })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                    continue
                cache_key = _tool_cache_key(name, arguments)
                if cache_key in tool_cache:
                    cached_observation_id = tool_cache_observation_ids.get(cache_key, "")
                    if duplicate_no_progress_signature == cache_key:
                        duplicate_no_progress_count += 1
                    else:
                        duplicate_no_progress_signature = cache_key
                        duplicate_no_progress_count = 1
                    next_action = (
                        "resolve_unknowns"
                        if pending_observation_ids or cached_observation_id
                        else "choose_different_evidence"
                    )
                    output = _duplicate_no_progress_json(
                        name,
                        duplicate_count=duplicate_no_progress_count,
                        cached_observation_id=cached_observation_id,
                        required_next_action=next_action,
                    )
                    if duplicate_no_progress_count >= MAX_DUPLICATE_NO_PROGRESS:
                        force_synthesis_reason = _duplicate_no_progress_prompt(
                            name,
                            cached_observation_id,
                            pending_observation_ids,
                        )
                    yield start_event(call_id, registry.event_type(name), {
                        "name": name,
                        "description": "Investigation tool",
                        "status": "no_progress",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                        "deduplicated": True,
                        "cached_observation_id": cached_observation_id,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                    if duplicate_no_progress_count >= MAX_REPEATED_TOOL_ERRORS:
                        finalization_reason = (
                            "Runtime stopped after repeated duplicate no-progress tool calls: "
                            f"{name or 'invalid'}."
                        )
                        stop_investigation = True
                        yield start_event(f"{run_id}-safety-duplicate-no-progress", "safety_stop", {
                            "reason": "duplicate_no_progress",
                            "message": finalization_reason,
                            "tool": name or "invalid",
                            "cached_observation_id": cached_observation_id,
                        })
                        break
                    continue
                output = yield from _run_tool_stream(
                    name,
                    call_id,
                    arguments,
                    workspace_dir,
                    _analysis_with_recorded_unknowns(
                        analysis,
                        recorded_findings,
                    ),
                )
                repeated_tool_error_name = ""
                repeated_tool_error_count = 0
                tool_cache[cache_key] = output
                observation = _tool_observation(name, call_id, output)
                observations.append(observation)
                tool_cache_observation_ids[cache_key] = observation["id"]
                pending_observation_ids.append(call_id)
                duplicate_no_progress_signature = ""
                duplicate_no_progress_count = 0
                force_synthesis_reason = ""
                if verification_request and _is_hypothesis_verifier_call(name, arguments):
                    attempted_verifications.add((
                        verification_request["unknown_id"],
                        verification_request["hypothesis"],
                    ))
                    verification_queue.pop(0)
            except Exception as exc:
                raw_arguments = function.get("arguments") or "{}"
                partial_arguments = _partial_tool_arguments(raw_arguments)
                if name == "record_investigation_findings":
                    partial_arguments = _record_arguments(partial_arguments)
                if name == "record_investigation_findings" and _has_finding_fields(partial_arguments):
                    recorded_findings = _merge_recorded_findings(recorded_findings, partial_arguments)
                    pending_observation_ids.clear()
                    last_quality_audit = {}
                output = _tool_repair_error_json(exc, name, raw_arguments, partial_arguments)
                error_name = name or "invalid"
                failed_tool_cache[_tool_cache_key(
                    error_name,
                    arguments or partial_arguments,
                )] = output
                if error_name not in round_error_names:
                    if error_name == repeated_tool_error_name:
                        repeated_tool_error_count += 1
                    else:
                        repeated_tool_error_name = error_name
                        repeated_tool_error_count = 1
                    round_error_names.add(error_name)
                yield start_event(call_id, registry.event_type(name), {
                    "name": name or "invalid",
                    "description": "Investigation tool",
                    "status": "error",
                    "open": False,
                    "input": raw_arguments,
                    "output": output,
                })
                if repeated_tool_error_count >= MAX_REPEATED_TOOL_ERRORS:
                    finalization_reason = (
                        "Runtime recovered after repeated tool argument errors: "
                        f"{name or 'invalid'} failed with {exc}."
                    )
                    if name == "record_investigation_findings":
                        final = _runtime_recovered_investigation(
                            finalization_reason,
                            analysis,
                            observations,
                            recorded_findings,
                        )
                    stop_investigation = True
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": output,
            })
            if stop_investigation:
                yield start_event(f"{run_id}-safety-repeated-tool-error", "safety_stop", {
                    "reason": "repeated_tool_error",
                    "message": finalization_reason,
                    "tool": name or "invalid",
                })
                break
        if final is not None:
            break
        if stop_investigation:
            break
    else:
        finalization_reason = "Investigation step limit reached. Summarizing observed facts."

    if final is None and stop_investigation:
        final = _runtime_recovered_investigation(
            finalization_reason,
            analysis,
            observations,
            recorded_findings,
        )
    elif final is None:
        final = yield from _finalize_investigation(
            provider=provider,
            model=model,
            messages=messages,
            pricing_rules=pricing_rules,
            usage_total=usage_total,
            run_id=run_id,
            analysis=analysis,
            observations=observations,
            recorded_findings=recorded_findings,
            audit_cache=audit_cache,
            reason=finalization_reason,
        )
    if last_quality_audit:
        final["quality_audit"] = last_quality_audit
    final["observations"] = _final_observations(
        observations + [
            item for item in final.get("observations", [])
            if isinstance(item, dict)
        ],
        preserve_grounding_evidence=preserve_grounding_evidence,
    )

    implementation_intent = _analysis_requests_implementation(analysis)
    yield {"op": "update", "id": stage_id, "patch": {
        "state": "done",
        "phase": "patch_planning_ready" if final.get("ready_for_patch_planning") and implementation_intent else "done",
    }}
    step = _step_result(final, implementation_intent=implementation_intent)
    final["step_result"] = step
    yield start_event(f"{run_id}-step-result", "step_result", step)
    if final.get("task_updates"):
        yield start_event(f"{run_id}-task-update", "task_update", {
            "analysis_id": analysis.get("id", ""),
            "items": final["task_updates"],
        })
    yield start_event(f"{run_id}-output", "output", {
        "content": _summary(final),
        "streaming": False,
    })
    yield {"op": "done", "investigation": final}


def _investigation_tools() -> list[dict]:
    tools = [
        _investigation_tool_schema(tool.name, tool.description, tool.parameters)
        for tool in registry.list_for_capability(INVESTIGATION_CAPABILITY)
    ]
    tools.append(_resolve_unknowns_tool_schema())
    tools.append(_record_findings_tool_schema())
    tools.append(_finish_tool_schema())
    return tools


def _round_indexes(limit: int, start: int = 0):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


def _tool_call_summary(tool_calls: list[dict]) -> str:
    items = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        name = function.get("name") or "tool"
        try:
            arguments = _tool_arguments(function.get("arguments"))
        except ValueError:
            arguments = {}
        reason = str(arguments.get("reason") or arguments.get("operation_summary") or "").strip()
        targets = arguments.get("target_unknown_ids") if isinstance(arguments.get("target_unknown_ids"), list) else []
        subject = _tool_call_subject(name, arguments)
        line = f"{name}{subject}"
        if targets:
            line += f" for {', '.join(str(item) for item in targets if str(item).strip())}"
        if reason:
            line += f": {reason}"
        items.append(line)
    if not items:
        return ""
    return "Calling tools:\n" + "\n".join(f"- {item}" for item in items)


def _tool_call_subject(name: str, arguments: dict) -> str:
    if name in {"read", "glob", "grep", "code_nav", "webfetch"}:
        value = arguments.get("path") or arguments.get("pattern") or arguments.get("query") or arguments.get("url")
        if not value and name == "grep":
            patterns = arguments.get("patterns") if isinstance(arguments.get("patterns"), list) else []
            if patterns:
                value = f"{len(patterns)} patterns"
        return f"({value})" if value else ""
    if name == "subagent":
        value = arguments.get("agent") or arguments.get("name")
        return f"({value})" if value else ""
    if name == "resolve_unknowns":
        ids = [
            str(item.get("unknown_id") or item.get("id") or "").strip()
            for item in arguments.get("resolutions", [])
            if isinstance(item, dict)
        ]
        ids = [item for item in ids if item]
        return f"({', '.join(ids[:4])})" if ids else ""
    if name in {"record_investigation_findings", "finish_investigation"}:
        summary = str(arguments.get("summary") or "").strip()
        return f"({summary[:80]})" if summary else ""
    return ""


def _tool_cache_key(name: str, arguments: dict) -> str:
    ignored = (
        set()
        if name in {"record_investigation_findings", "resolve_unknowns", "finish_investigation"}
        else {"reason", *DISCOVERY_CONTRACT_FIELDS}
    )
    comparable = {
        key: value
        for key, value in arguments.items()
        if key not in ignored
    }
    return f"{name}:{json.dumps(comparable, ensure_ascii=False, sort_keys=True)}"


def _duplicate_no_progress_json(
    tool_name: str,
    *,
    duplicate_count: int,
    cached_observation_id: str,
    required_next_action: str,
) -> str:
    return json.dumps({
        "code": "duplicate_no_progress",
        "tool": tool_name or "invalid",
        "cached_observation_id": cached_observation_id,
        "duplicate_count": duplicate_count,
        "retryable": False,
        "required_next_action": required_next_action,
        "message": (
            "The same successful tool arguments were already observed. "
            "This call produced no new investigation progress."
        ),
    }, ensure_ascii=False)


def _duplicate_no_progress_prompt(
    tool_name: str,
    cached_observation_id: str,
    pending_observation_ids: list[str],
) -> str:
    ids = _dedupe_strings([cached_observation_id, *pending_observation_ids])
    return (
        "The investigation repeated the same successful discovery tool call without "
        f"new progress: {tool_name or 'invalid'}. Do not call that discovery action again. "
        "Use resolve_unknowns if the cached observation answers a blocking unknown, "
        "record_investigation_findings if it contains material findings, or "
        "finish_investigation if investigation is already sufficient. "
        f"Relevant observation ids: {', '.join(ids) if ids else 'none'}."
    )


def _tool_blocked_error_json(tool_name: str, *, allowed_tools: list[str]) -> str:
    required_action = allowed_tools[0] if len(allowed_tools) == 1 else "choose_allowed_tool"
    return json.dumps({
        "error": {
            "code": "tool_blocked_by_investigation_state",
            "tool": tool_name or "invalid",
            "retryable": False,
            "blocked_tool": tool_name or "invalid",
            "allowed_tools": allowed_tools,
            "required_action": required_action,
            "message": (
                "This tool is not available in the current investigation state. "
                "Use an allowed control tool or wait until discovery is available again."
            ),
        },
    }, ensure_ascii=False)


def _tool_repair_error_json(exc: Exception, tool_name: str, raw_arguments: str, partial_arguments: dict) -> str:
    try:
        payload = json.loads(tool_error_json(exc, tool_name))
    except json.JSONDecodeError:
        payload = {"error": {"message": str(exc), "tool": tool_name or "invalid"}}
    error = payload.setdefault("error", {})
    error["partial_arguments"] = partial_arguments
    error["missing_fields"] = _missing_fields_from_error(
        str(exc),
        partial_arguments,
    )
    if tool_name == "finish_investigation" and "bugfix_readiness" in str(exc):
        error["required_argument_shape"] = {
            "bugfix_readiness": {
                "failure_reproduced_or_observed": True,
                "root_cause_or_failing_boundary_identified": True,
                "patch_target_identified": True,
                "expected_behavior_change_defined": True,
                "validation_scenario_defined": True,
                "reason": "Evidence-backed reason for each readiness field.",
            }
        }
    if tool_name == "clearify" and "requires product_decision targets" in str(exc):
        error["retryable"] = False
        error["required_action"] = "resolve_or_investigate_contract_unknown"
        error["repair_instruction"] = (
            "Do not retry clearify for non-product-decision unknowns. "
            "Resolve the contract unknown from project evidence or continue discovery."
        )
    else:
        error["repair_instruction"] = (
            "Reuse partial_arguments. Return only the same tool call with missing/invalid fields corrected; "
            "do not restart discovery or repeat the identical arguments."
        )
    return json.dumps(payload, ensure_ascii=False)


def _missing_fields_from_error(
    message: str,
    partial_arguments: dict | None = None,
) -> list[str]:
    fields = []
    lowered = message.casefold()
    partial_arguments = partial_arguments or {}
    for field in (
        "reason",
        "target_unknown_ids",
        "summary",
        "recommended_next_step",
        "bugfix_readiness",
        *DISCOVERY_CONTRACT_FIELDS,
    ):
        if field in lowered and not partial_arguments.get(field):
            fields.append(field)
    return fields


def _partial_tool_arguments(raw: str | None) -> dict:
    text = (raw or "{}").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return _partial_json_object(text)


def _partial_json_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    result = {}
    index = _skip_ws(text, 0)
    if index >= len(text) or text[index] != "{":
        return result
    index += 1
    while True:
        index = _skip_ws(text, index)
        if index >= len(text) or text[index] == "}":
            return result
        try:
            key, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            return result
        if not isinstance(key, str):
            return result
        index = _skip_ws(text, index)
        if index >= len(text) or text[index] != ":":
            return result
        index = _skip_ws(text, index + 1)
        try:
            value, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            return result
        result[key] = value
        index = _skip_ws(text, index)
        if index >= len(text) or text[index] == "}":
            return result
        if text[index] != ",":
            return result
        index += 1


def _skip_ws(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _has_finding_fields(arguments: dict) -> bool:
    return any(isinstance(arguments.get(field), list) and arguments.get(field) for field in FINDING_FIELDS)


def _require_finding_fields(arguments: dict) -> None:
    if not _has_finding_fields(arguments):
        raise ValueError(
            "record_investigation_findings must include at least one non-empty findings array; canonical arrays are "
            "beliefs, resolutions, unknowns, new_unknowns, user_decisions_required, or task_updates"
        )


def _resolve_unknown_arguments(arguments: dict) -> dict:
    normalized = dict(arguments)
    normalized["resolutions"] = _resolutions(normalized.get("resolutions"))
    return normalized


def _resolution_kind(raw: dict, status: str) -> str:
    value = str(raw.get("kind") or "").strip()
    if value in RESOLUTION_KINDS:
        return value
    if status == "deferred":
        return "deferred"
    if status == "needs_clearify":
        return "user_decision"
    return "derived_inference"


def _record_arguments(arguments: dict) -> dict:
    normalized = dict(arguments)
    if not (isinstance(normalized.get("beliefs"), list) and normalized["beliefs"]):
        beliefs = _alias_beliefs(normalized.get("findings")) or _alias_beliefs(normalized.get("evidence_summaries"))
        if beliefs:
            normalized["beliefs"] = beliefs
    if isinstance(normalized.get("new_unknowns"), list):
        normalized["new_unknowns"] = [
            {
                **item,
                "question": item.get("question") or item.get("summary"),
                "resolution_strategy": item.get("resolution_strategy") or item.get("strategy"),
            }
            for item in normalized["new_unknowns"]
            if isinstance(item, dict)
        ]
    return normalized


def _alias_beliefs(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for raw in value:
        if isinstance(raw, str):
            statement = raw.strip()
            evidence = []
            status = "supported"
        elif isinstance(raw, dict):
            statement = _belief_text(raw) or _alias_statement(raw)
            evidence = _reference_list(raw.get("evidence") or raw.get("source") or raw.get("sources"))
            status = _belief_status(raw, default="supported")
        else:
            continue
        if statement:
            item = {"statement": statement, "status": status, "evidence": evidence}
            if isinstance(raw, dict) and str(raw.get("id") or "").strip():
                item["id"] = str(raw["id"]).strip()
            items.append(item)
    return items


def _alias_statement(raw: dict) -> str:
    label = str(raw.get("label") or raw.get("title") or "").strip()
    evidence = str(raw.get("evidence") or raw.get("source") or "").strip()
    if label and evidence:
        return f"{label}: {evidence}"
    return label or evidence


def _is_hypothesis_verifier_call(name: str, arguments: dict) -> bool:
    if name != "subagent":
        return False
    agent = str(arguments.get("agent") or arguments.get("name") or "")
    return agent.strip().removeprefix("@").casefold() == "hypothesis-verifier"


def _analysis_is_read_only(analysis: dict | None) -> bool:
    return str((analysis or {}).get("execution_mode") or "").strip().casefold() == "read_only"


def _recorded_resolves_initial_unknowns(recorded: dict, analysis: dict | None) -> bool:
    initial = [item for item in (analysis or {}).get("unknowns", []) if isinstance(item, dict) and item.get("id")]
    if not initial:
        return False
    required_ids = [str(item["id"]) for item in initial]
    if not _analysis_is_read_only(analysis):
        required_ids.extend(
            str(item["id"])
            for item in (
                _unknowns(recorded.get("unknowns"))
                + _unknowns(recorded.get("new_unknowns"))
            )
            if item.get("blocking")
        )
    resolved = [
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict) and str(item.get("status") or "") in {"resolved", "deferred"}
    ]
    return all(any(_same_unknown_id(required_id, item) for item in resolved) for required_id in required_ids)


def _pending_clearify_unknown(
    recorded: dict,
    analysis: dict | None,
    audit_questions: dict[str, str] | None = None,
) -> dict | None:
    completed = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict) and str(item.get("status") or "") in {"resolved", "deferred"}
    }
    needs_clearify = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict) and str(item.get("status") or "") == "needs_clearify"
    }
    candidates = (
        _initial_unknowns(analysis)
        + _unknowns(recorded.get("new_unknowns"))
    )
    for item in candidates:
        if (
            item.get("blocking")
            and item.get("type") == "product_decision"
            and not any(_same_unknown_id(item["id"], completed_id) for completed_id in completed)
            and (
                item.get("resolution_strategy") == "clearify"
                or any(_same_unknown_id(item["id"], pending_id) for pending_id in needs_clearify)
            )
        ):
            result = dict(item)
            question = next(
                (
                    question
                    for unknown_id, question in (audit_questions or {}).items()
                    if _same_unknown_id(unknown_id, item["id"])
                ),
                "",
            )
            if question:
                result["question"] = question
            return result
    return None


def _unknown_blocks_finish(unknown_id: str | None, analysis: dict | None, recorded: dict) -> bool:
    candidates = (
        _initial_unknowns(analysis)
        + _unknowns(recorded.get("unknowns"))
        + _unknowns(recorded.get("new_unknowns"))
    )
    source = _find_by_unknown_id(candidates, unknown_id, id_field="id")
    return bool(source.get("blocking", True)) if source else True


def _analysis_with_recorded_unknowns(analysis: dict, recorded: dict) -> dict:
    return {
        **analysis,
        "unknowns": _merge_unknowns(
            _initial_unknowns(analysis)
            + _unknowns(recorded.get("unknowns"))
            + _unknowns(recorded.get("new_unknowns"))
        ),
    }


def _clearify_required_prompt(unknown: dict) -> str:
    return "\n".join([
        "A blocking product decision requires the user's answer now.",
        f"Unknown id: {unknown['id']}",
        f"Question: {unknown['question']}",
        "Call clearify with this target_unknown_id and exactly three concrete candidate answers.",
        "Do not call discovery or finish tools before the user answers.",
    ])


def _unknowns_needing_resolution(recorded: dict, observations: list[dict], analysis: dict | None) -> list[str]:
    initial = [
        item for item in (analysis or {}).get("unknowns", [])
        if isinstance(item, dict)
        and item.get("blocking")
        and item.get("resolution_strategy") == "investigate_project"
        and str(item.get("id") or "").strip()
    ]
    if not initial:
        return []
    accounted = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict) and str(item.get("unknown_id") or "").strip()
    }
    supported = _supported_unknown_ids(recorded, observations)
    return [
        str(item["id"])
        for item in initial
        if not any(_same_unknown_id(item["id"], known_id) for known_id in accounted)
        and any(_same_unknown_id(item["id"], supported_id) for supported_id in supported)
    ]


def _supported_unknown_ids(recorded: dict, observations: list[dict]) -> set[str]:
    observations_by_id = {
        str(item.get("id") or "").strip(): item
        for item in observations
        if isinstance(item, dict) and _positive_project_observation(item)
    }
    by_unknown: dict[str, list[dict]] = {}
    for observation in observations_by_id.values():
        for unknown_id in observation.get("target_unknown_ids", []):
            by_unknown.setdefault(_normalize_unknown_id(unknown_id), []).append(observation)
    supported = set()
    for raw in recorded.get("beliefs", []):
        if not isinstance(raw, dict) or not _supporting_belief(raw):
            continue
        text = _belief_text(raw)
        evidence_refs = _reference_list(raw.get("evidence")) + _reference_list(raw.get("observation_ids"))
        evidence_ids = [item for item in evidence_refs if item in observations_by_id]
        for unknown_id, unknown_observations in by_unknown.items():
            unknown_evidence_ids = {
                str(item.get("id") or "").strip()
                for item in unknown_observations
            }
            if any(evidence_id in unknown_evidence_ids for evidence_id in evidence_ids):
                supported.add(unknown_id)
                continue
            if any(_observation_mentioned(text, observation) for observation in unknown_observations):
                supported.add(unknown_id)
    return supported


def _positive_project_observation(item: dict) -> bool:
    tool = registry.get(str(item.get("tool") or ""))
    if (
        item.get("tool") not in PROJECT_EVIDENCE_TOOLS
        and (tool is None or PROJECT_EVIDENCE_CAPABILITY not in tool.capabilities)
    ):
        return False
    if not item.get("target_unknown_ids"):
        return False
    return True


def _supporting_belief(item: dict) -> bool:
    if not _belief_text(item):
        return False
    status = str(item.get("status") or "").strip()
    return status in {"", "supported", "strongly_supported", "runtime_confirmed"}


def _belief_text(item: dict) -> str:
    return str(item.get("statement") or item.get("text") or item.get("summary") or item.get("content") or "").strip()


def _observation_mentioned(text: str, observation: dict) -> bool:
    if not text:
        return False
    haystack = text.casefold()
    path = str(observation.get("path") or "").replace("\\", "/")
    title = str(observation.get("title") or "")
    names = [path, Path(path).name if path else "", title]
    return any(name and name.casefold() in haystack for name in names)


def _resolution_required_prompt(
    unknown_ids: list[str],
    recorded: dict,
    observations: list[dict],
    analysis: dict | None,
) -> str:
    questions = {
        str(item.get("id") or ""): str(item.get("question") or "").strip()
        for item in (analysis or {}).get("unknowns", [])
        if isinstance(item, dict)
    }
    return "\n".join([
        "Existing project evidence is sufficient to write an explicit resolution.",
        "Do not call more discovery tools for these unknowns.",
        "Call resolve_unknowns with resolutions for: " + ", ".join(unknown_ids),
        "Each resolution must include unknown_id, status, answer, evidence or belief_ids, and reason.",
        "If an unknown is still not fully resolved, record a partially_resolved resolution naming the precise missing evidence.",
        "Questions:",
        *[f"- {unknown_id}: {questions.get(unknown_id, '')}" for unknown_id in unknown_ids],
        "Supported project evidence:",
        *_resolution_evidence_lines(unknown_ids, recorded, observations),
    ])


def _resolution_evidence_lines(unknown_ids: list[str], recorded: dict, observations: list[dict]) -> list[str]:
    wanted = set(unknown_ids)
    lines = []
    for observation in observations:
        targets = {_normalize_unknown_id(item) for item in observation.get("target_unknown_ids", [])}
        if targets & wanted and _positive_project_observation(observation):
            lines.append(f"- observation {observation.get('id', '')}: {observation.get('title') or observation.get('summary')}")
    for belief in recorded.get("beliefs", []):
        if isinstance(belief, dict) and _supporting_belief(belief):
            lines.append(f"- belief: {_belief_text(belief)}")
    return lines[-12:]


def _require_control_reason(arguments: dict, name: str) -> None:
    if not str(arguments.get("reason") or "").strip():
        raise ValueError(f"{name} requires reason")


def _investigation_tool_schema(name: str, description: str, parameters: dict) -> dict:
    schema = json.loads(json.dumps(parameters))
    properties = schema.setdefault("properties", {})
    properties["target_unknown_ids"] = {
        "type": "array",
        "items": {"type": "string"},
        "description": "Task contract unknown IDs this tool call is intended to resolve or reduce.",
    }
    properties["reason"] = {
        "type": "string",
        "description": "One short sentence explaining why this call helps those unknowns.",
    }
    properties["orientation"] = {
        "type": "boolean",
        "description": "True only for a broad first-pass orientation call that cannot yet target a specific unknown.",
    }
    if name != "clearify":
        properties["hypothesis"] = {
            "type": "string",
            "description": "One falsifiable claim this call will test. Do not use a generic exploration goal.",
        }
        properties["expected_observation"] = {
            "type": "string",
            "description": "The concrete result that would support, oppose, or narrow the hypothesis.",
        }
        properties["decision_impact"] = {
            "type": "string",
            "description": "How the result will update a target unknown, belief, or next investigation branch.",
        }
        properties["stop_condition"] = {
            "type": "string",
            "description": "When this line of investigation should stop or switch to recording/resolution.",
        }
    required = schema.setdefault("required", [])
    for field in ("target_unknown_ids", "reason"):
        if field not in required:
            required.append(field)
    if name != "clearify":
        for field in DISCOVERY_CONTRACT_FIELDS:
            if field not in required:
                required.append(field)
    return openai_tool_schema(name, description, schema)


def _previous_context(observations: list[dict] | None, knowledge: list[dict] | None) -> list[str]:
    lines = []
    fresh_knowledge = [item for item in knowledge or [] if item.get("fresh", True)]
    fresh_observations = [item for item in observations or [] if item.get("fresh")]
    if fresh_knowledge:
        lines.append("PREVIOUS SUPPORTED KNOWLEDGE:")
        lines.extend(f"- {item.get('id', '')}: {item.get('statement', '')}" for item in fresh_knowledge[:12])
    if fresh_observations:
        lines.append("PREVIOUS OBSERVATIONS:")
        lines.extend(f"- {item.get('id', '')}: {item.get('summary') or item.get('title') or item.get('tool', '')}" for item in fresh_observations[:20])
    return lines


def _record_findings_tool_schema() -> dict:
    return openai_tool_schema(
        "record_investigation_findings",
        "Start runtime slot-based recording of grounded findings before finishing.",
        {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "One short reason why current observations should be recorded now. Do not include findings JSON; runtime will request slots.",
                },
            },
            "required": ["reason"],
        },
    )


def _resolve_unknowns_tool_schema() -> dict:
    return openai_tool_schema(
        "resolve_unknowns",
        "Record explicit resolutions for investigation unknowns using existing observation or belief ids only.",
        {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "One short reason why these unknowns can be resolved now.",
                },
                "resolutions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "unknown_id": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["resolved", "partially_resolved", "needs_clearify", "deferred"],
                            },
                            "kind": {
                                "type": "string",
                                "enum": ["direct_fact", "derived_inference", "user_decision", "deferred"],
                                "description": "Use direct_fact only when the cited observation directly states the answer; use derived_inference for cross-file or causal reasoning.",
                            },
                            "answer": {"type": "string"},
                            "evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Existing observation ids that support the answer.",
                            },
                            "belief_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Existing belief ids that support the answer.",
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["unknown_id", "status", "answer", "reason"],
                    },
                },
            },
            "required": ["reason", "resolutions"],
        },
    )


def _record_findings_by_slots(
    *,
    provider: dict,
    model: str,
    messages: list[dict],
    pricing_rules: list[dict],
    usage_total: dict,
    run_id: str,
    reason: str,
    analysis: dict,
    observations: list[dict],
    recorded_findings: dict,
    pending_observation_ids: list[str],
    required_resolution_ids: list[str] | None = None,
) -> Iterator[dict]:
    required_resolution_ids = required_resolution_ids or []
    belief_observation_ids = list(pending_observation_ids)
    resolution_slot_ids = _record_resolution_slot_ids(
        analysis,
        observations,
        recorded_findings,
        pending_observation_ids,
        required_resolution_ids,
    )
    slot_messages = [
        {"role": "system", "content": prompt.build_investigation_static(
            app_settings.get_output_language()
        )},
        {"role": "user", "content": _record_slot_context(
            reason,
            analysis,
            observations,
            recorded_findings,
            pending_observation_ids,
            required_resolution_ids,
            resolution_slot_ids,
        )},
    ]
    usage_events: list[dict] = []

    def ask(path: str, prompt_text: str) -> JSONValue:
        required = _required_resolution_slot(
            path,
            resolution_slot_ids,
            required_resolution_ids,
        )
        slot_messages.append({"role": "user", "content": _record_slot_prompt(
            path,
            prompt_text,
            required=required,
            required_answer_literals=_required_state_write_literals(
                path,
                resolution_slot_ids,
                recorded_findings,
                observations,
            ),
        )})
        raw = ""
        attempts = (
            REQUIRED_FINDING_SLOT_ATTEMPTS
            if path.startswith(("beliefs[", "resolutions["))
            else 1
        )
        expected = (
            "a JSON array or null"
            if path == "new_unknowns"
            else "one JSON object" + ("." if required else " or null.")
        )
        for attempt in range(attempts):
            assistant = _call_model(
                provider,
                model,
                slot_messages,
                tools=[],
                use_skills=False,
            )
            if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
                _add_usage(usage_total, usage)
                usage_events.append(start_event(
                    f"{run_id}-usage-record-slot-{len(usage_events)}",
                    "usage",
                    {"delta": usage, "total": usage_total},
                ))
            raw = _normalize_record_slot_answer(
                _content_text(assistant.get("content")),
                path,
            )
            slot_messages.append({"role": "assistant", "content": raw})
            if _valid_record_slot_value(raw, path, required=required):
                break
            if attempt + 1 < attempts:
                slot_messages.append({"role": "user", "content": (
                    f"The {path} slot has the wrong shape. Return {expected}"
                )})
        if not _valid_record_slot_value(raw, path, required=required):
            if not required:
                return [] if path == "new_unknowns" else None
            raise ValueError(f"{path} must return {expected}")
        return raw

    filled = json2slots(_record_slot_template(
        belief_observation_ids,
        resolution_slot_ids,
    ), ask)
    yield from usage_events
    filled = filled if isinstance(filled, dict) else {}
    beliefs = _runtime_slot_beliefs(
        filled.get("beliefs"),
        belief_observation_ids,
        recorded_findings,
    )
    resolutions = _runtime_slot_resolutions(
        filled.get("resolutions"),
        resolution_slot_ids,
        observations,
        recorded_findings,
        beliefs,
        pending_observation_ids,
    )
    result: dict = {
        "reason": reason,
        **_empty_recorded_findings(),
        "beliefs": beliefs,
        "resolutions": resolutions,
        "new_unknowns": _runtime_new_unknowns(
            filled.get("new_unknowns"),
            analysis,
            recorded_findings,
            resolutions,
        ),
    }
    return result


def _empty_discovery_recording(
    arguments: dict,
    pending_observation_ids: list[str],
    required_resolution_ids: list[str],
) -> bool:
    return bool(
        pending_observation_ids
        and not required_resolution_ids
        and not _has_finding_fields(arguments)
    )


def _audit_recorded_findings(
    *,
    provider: dict,
    model: str,
    messages: list[dict],
    pricing_rules: list[dict],
    usage_total: dict,
    run_id: str,
    analysis: dict,
    observations: list[dict],
    recorded_findings: dict,
    audit_cache: dict[str, dict] | None = None,
) -> Iterator[dict]:
    initial_unknowns = _initial_unknowns(analysis)
    target_resolutions = [
        item
        for item in recorded_findings.get("resolutions", [])
        if isinstance(item, dict)
        and _resolution_requires_semantic_audit(item, initial_unknowns)
    ]
    resolved_ids = [
        str(item.get("unknown_id") or "").strip()
        for item in target_resolutions
    ]
    if not resolved_ids:
        return {"verdicts": []}
    usage_events: list[dict] = []
    verdicts: list[dict] = []
    partial_ids = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded_findings.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") == "partially_resolved"
    }
    independently_verified_ids = {
        str(resolution.get("unknown_id") or "").strip()
        for resolution in recorded_findings.get("resolutions", [])
        if isinstance(resolution, dict)
        and any(
            observation.get("verification")
            for observation in observations
            if isinstance(observation, dict)
            and observation.get("id") in resolution.get("evidence", [])
        )
    }
    contract = {
        "statements": analysis.get("statements", []),
        "acceptance_criteria": analysis.get("acceptance_criteria", []),
        "constraints": analysis.get("constraint_statements", []),
        "scope": analysis.get("scope_statements", {}),
        "reference_baselines": analysis.get("reference_baselines", []),
        "unknowns": analysis.get("unknowns", []),
    }
    all_beliefs = [
        item for item in recorded_findings.get("beliefs", [])
        if isinstance(item, dict)
    ]
    for resolution in target_resolutions:
        unknown_id = str(resolution.get("unknown_id") or "").strip()
        target_belief_ids = set(_reference_list(resolution.get("belief_ids")))
        target_beliefs = [
            item for item in all_beliefs
            if str(item.get("id") or "").strip() in target_belief_ids
        ]
        target_observation_ids = set(_reference_list(resolution.get("evidence"))) | {
            evidence_id
            for belief in target_beliefs
            for evidence_id in _reference_list(belief.get("evidence"))
        }
        context = json.dumps({
            "authoritative_task_contract": contract,
            "proposed_findings": {
                "beliefs": target_beliefs,
                "resolutions": [resolution],
            },
            "observation_index": [
                {
                    "id": item.get("id", ""),
                    "tool": item.get("tool", ""),
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "evidence_excerpt": item.get("evidence_excerpt", ""),
                    "path": item.get("path", ""),
                    "verification": item.get("verification", {}),
                    "target_unknown_ids": item.get("target_unknown_ids", []),
                    "reason": item.get("reason", ""),
                }
                for item in observations
                if isinstance(item, dict)
                and str(item.get("id") or "").strip() in target_observation_ids
            ],
            "required_unknown_ids": [unknown_id],
        }, ensure_ascii=False)
        cache_key = "audit:v2:" + hashlib.sha256(context.encode("utf-8")).hexdigest()
        if audit_cache is not None and cache_key in audit_cache:
            verdicts.extend(audit_cache[cache_key].get("verdicts", []))
            continue
        audit_messages = [{"role": "system", "content": prompt.build_investigation_auditor(
            app_settings.get_output_language()
        )}]
        audit = {"verdicts": []}
        for attempt in range(REQUIRED_AUDIT_ATTEMPTS):

            def ask(_path: str, slot_prompt: str) -> JSONValue:
                assistant = _call_model(provider, model, [
                    *audit_messages,
                    {"role": "user", "content": f"{slot_prompt}\ncontext: {context}"},
                ], tools=[])
                if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
                    _add_usage(usage_total, usage)
                    usage_events.append(start_event(
                        f"{run_id}-usage-investigation-audit-{len(usage_events)}",
                        "usage",
                        {"delta": usage, "total": usage_total},
                    ))
                return _content_text(assistant.get("content"))

            filled = json2slots({"verdicts": "____"}, ask)
            audit = _normalize_investigation_audit(filled, [unknown_id])
            covered_ids = {
                item["unknown_id"] for item in audit["verdicts"]
            }
            invalid_grounded_ids = {
                item["unknown_id"]
                for item in audit["verdicts"]
                if item["status"] == "grounded"
                and item["unknown_id"] in partial_ids - independently_verified_ids
            }
            if covered_ids == {unknown_id} and not invalid_grounded_ids:
                break
            problem = (
                "A partial resolution was incorrectly marked grounded without independent "
                "verification. Return verify with one atomic hypothesis, or investigate, for: "
                + ", ".join(sorted(invalid_grounded_ids))
                if invalid_grounded_ids
                else "The audit omitted conclusions. Return exactly one verdict for: "
                + unknown_id
            )
            audit_messages.append({"role": "user", "content": problem})
        invalid_grounded_ids = {
            item["unknown_id"]
            for item in audit["verdicts"]
            if item["status"] == "grounded"
            and item["unknown_id"] in partial_ids - independently_verified_ids
        }
        for item in audit["verdicts"]:
            if item["unknown_id"] in invalid_grounded_ids:
                item["status"] = "investigate"
                item["reason"] = (
                    "The partial conclusion was not independently verified and cannot be "
                    "promoted from the same evidence."
                )
        if audit_cache is not None:
            audit_cache[cache_key] = audit
        verdicts.extend(audit["verdicts"])
    for event in usage_events:
        yield event
    return {"verdicts": verdicts}


def _normalize_investigation_audit(value, resolved_ids: list[str]) -> dict:
    raw = value.get("verdicts") if isinstance(value, dict) else None
    raw = raw if isinstance(raw, list) else []
    allowed = {"grounded", "verify", "clearify", "investigate"}
    by_id = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        unknown_id = str(item.get("unknown_id") or "").strip()
        status = str(item.get("status") or "").strip()
        matched_id = next(
            (known_id for known_id in resolved_ids if _same_unknown_id(unknown_id, known_id)),
            "",
        )
        if not matched_id or status not in allowed:
            continue
        by_id[matched_id] = {
            "unknown_id": matched_id,
            "status": status,
            "reason": str(item.get("reason") or "").strip(),
            "hypothesis": str(item.get("hypothesis") or "").strip(),
            "question": str(item.get("question") or "").strip(),
        }
    return {"verdicts": [
        by_id.get(unknown_id, {
            "unknown_id": unknown_id,
            "status": "investigate",
            "reason": "The semantic audit did not return a usable verdict.",
            "hypothesis": "",
            "question": "",
        })
        for unknown_id in resolved_ids
    ]}


def _record_slot_template(
    belief_observation_ids: list[str] | None = None,
    resolution_ids: list[str] | None = None,
) -> dict[str, JSONValue]:
    return {
        "beliefs": ["____" for _ in belief_observation_ids or []],
        "resolutions": ["____" for _ in resolution_ids or []],
        "new_unknowns": "____",
    }


def _record_slot_context(
    reason: str,
    analysis: dict,
    observations: list[dict],
    recorded_findings: dict,
    pending_observation_ids: list[str],
    required_resolution_ids: list[str],
    resolution_slot_ids: list[str] | None = None,
) -> str:
    payload = {
        "mode": "record_investigation_findings_slots",
        "record_reason": reason,
        "cache_policy": "Fill one bound item per request. Runtime owns ids and evidence links.",
        "task": {
            "intent": analysis.get("intent", {}),
            "unknowns": analysis.get("unknowns", []),
            "acceptance_criteria": analysis.get("acceptance_criteria", []),
        },
        "pending_observation_ids": list(pending_observation_ids),
        "required_resolution_ids": list(required_resolution_ids),
        "observations": [
            {
                "id": item.get("id", ""),
                "tool": item.get("tool", ""),
                "title": item.get("title", ""),
                "summary": item.get("summary", ""),
                "evidence_excerpt": item.get("evidence_excerpt", ""),
                "verification": item.get("verification", {}),
                "target_unknown_ids": item.get("target_unknown_ids", []),
                "reason": item.get("reason", ""),
                "path": item.get("path", ""),
            }
            for item in observations[-12:]
        ],
        "already_recorded": {
            field: recorded_findings.get(field, [])
            for field in FINDING_FIELDS
        },
        "runtime_slot_bindings": {
            "beliefs": [
                {"index": index, "observation_id": observation_id}
                for index, observation_id in enumerate(pending_observation_ids)
            ],
            "resolutions": [
                {"index": index, "unknown_id": unknown_id}
                for index, unknown_id in enumerate(resolution_slot_ids or required_resolution_ids)
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _record_slot_prompt(
    path: str,
    prompt_text: str,
    *,
    required: bool = False,
    required_answer_literals: list[str] | None = None,
) -> str:
    payload = {
        "slot": path,
        "instruction": prompt_text,
        "contract": _record_slot_contract(path),
        "required_non_empty": required,
        "required_exact_answer_literals": required_answer_literals or [],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _record_slot_contract(path: str) -> str:
    if path.startswith("beliefs["):
        return (
            "Return null when this observation has no material finding. Otherwise return one "
            "JSON object with statement and status. Runtime supplies id and evidence."
        )
    if path.startswith("resolutions["):
        return (
            "Return null when the bound unknown is not reduced by available evidence. Otherwise "
            "return one JSON object with status, answer, and reason. Runtime supplies unknown_id, "
            "evidence, and belief_ids. status is resolved, partially_resolved, needs_clearify, or deferred."
        )
    contracts = {
        "beliefs": (
            "Return a JSON array of objects with statement, status, evidence. "
            "status is one of unverified, plausible, supported, strongly_supported, runtime_confirmed, contradicted, invalidated. "
            "evidence must use exact observation ids/tool_call_ids only."
        ),
        "resolutions": (
            "Return a JSON array of objects with unknown_id, status, answer, evidence, belief_ids, reason. "
            "status is resolved, partially_resolved, needs_clearify, or deferred."
        ),
        "new_unknowns": (
            "Return a JSON array of new unknown objects with id, question, blocking, resolution_strategy. "
            "resolution_strategy is investigate_project, clearify, or deferred. Add only material facts "
            "that must be resolved before design; do not add implementation-mechanism or design-choice questions."
        ),
        "user_decisions_required": "Return a JSON array of user decision question strings.",
    }
    return contracts.get(path, "Return the JSON value for this slot only.")


def _record_resolution_slot_ids(
    analysis: dict,
    observations: list[dict],
    recorded_findings: dict,
    pending_observation_ids: list[str],
    required_resolution_ids: list[str],
) -> list[str]:
    unknowns = _merge_unknowns(
        _initial_unknowns(analysis)
        + (
            []
            if _analysis_is_read_only(analysis)
            else _unknowns(recorded_findings.get("new_unknowns"))
        )
    )
    investigable = {
        item["id"]
        for item in unknowns
        if item.get("resolution_strategy") == "investigate_project"
    }
    pending = set(pending_observation_ids)
    candidates = list(required_resolution_ids)
    for observation in observations:
        if observation.get("id") not in pending:
            continue
        candidates.extend(
            _normalize_unknown_id(item)
            for item in observation.get("target_unknown_ids", [])
        )
    return [
        item
        for index, item in enumerate(candidates)
        if item in investigable and item not in candidates[:index]
    ]


def _required_resolution_slot(
    path: str,
    resolution_slot_ids: list[str],
    required_resolution_ids: list[str],
) -> bool:
    match = re.fullmatch(r"resolutions\[(\d+)\]", path)
    if not match:
        return False
    index = int(match.group(1))
    return (
        index < len(resolution_slot_ids)
        and resolution_slot_ids[index] in set(required_resolution_ids)
    )


def _required_state_write_literals(
    path: str,
    resolution_slot_ids: list[str],
    recorded_findings: dict,
    observations: list[dict],
) -> list[str]:
    match = re.fullmatch(r"resolutions\[(\d+)\]", path)
    if not match:
        return []
    index = int(match.group(1))
    if index >= len(resolution_slot_ids):
        return []
    unknown_id = resolution_slot_ids[index]
    resolution = next((
        item
        for item in recorded_findings.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("unknown_id") or "").strip() == unknown_id
        and str(item.get("reason") or "").startswith(STATE_WRITE_REASON_PREFIX)
    ), None)
    if resolution is None:
        return []
    return _missing_grounding_state_writes(
        resolution,
        recorded_findings,
        observations,
    )


def _valid_record_slot_value(value: str, path: str, *, required: bool) -> bool:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False
    if path == "new_unknowns":
        return parsed is None or parsed == {} or isinstance(parsed, list)
    if parsed is None:
        return not required
    if not isinstance(parsed, dict):
        return False
    if path.startswith("beliefs["):
        return bool(_belief_text(parsed))
    if path.startswith("resolutions["):
        status = str(parsed.get("status") or "").strip()
        return (
            status in {"resolved", "partially_resolved", "needs_clearify", "deferred"}
            and bool(str(parsed.get("answer") or parsed.get("reason") or "").strip())
        )
    return True


def _normalize_record_slot_answer(value: str, path: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    parsed: JSONValue = None
    for candidate in _json_candidates(text):
        try:
            parsed, _ = json.JSONDecoder().raw_decode(candidate)
            break
        except json.JSONDecodeError:
            continue
    else:
        return text
    field = path.split("[", 1)[0]
    if isinstance(parsed, dict) and field in parsed:
        parsed = parsed[field]
    if path.startswith(("beliefs[", "resolutions[")) and isinstance(parsed, list):
        if not parsed:
            parsed = None
        elif len(parsed) == 1:
            parsed = parsed[0]
    return json.dumps(parsed, ensure_ascii=False)


def _runtime_slot_beliefs(
    value,
    observation_ids: list[str],
    recorded_findings: dict,
) -> list[dict]:
    raw_items = value if isinstance(value, list) else []
    existing = _beliefs(recorded_findings.get("beliefs"))
    used_ids = {
        str(item.get("id") or "").strip()
        for item in existing
        if str(item.get("id") or "").strip()
    }
    next_id = max(
        (int(match.group(1)) for item in used_ids if (match := re.fullmatch(r"B(\d+)", item))),
        default=0,
    ) + 1
    beliefs = []
    for observation_id, raw in zip(observation_ids, raw_items):
        if not isinstance(raw, dict) or not _belief_text(raw):
            continue
        while f"B{next_id}" in used_ids:
            next_id += 1
        beliefs.append({
            **raw,
            "id": f"B{next_id}",
            "evidence": [observation_id],
        })
        used_ids.add(f"B{next_id}")
        next_id += 1
    return _beliefs(beliefs)


def _runtime_slot_resolutions(
    value,
    resolution_ids: list[str],
    observations: list[dict],
    recorded_findings: dict,
    new_beliefs: list[dict],
    pending_observation_ids: list[str] | None = None,
) -> list[dict]:
    raw_items = value if isinstance(value, list) else []
    beliefs = _beliefs(recorded_findings.get("beliefs")) + _beliefs(new_beliefs)
    pending = set(pending_observation_ids or [])
    resolutions = []
    for unknown_id, raw in zip(resolution_ids, raw_items):
        if not isinstance(raw, dict):
            continue
        matching_evidence_ids = [
            str(item.get("id") or "").strip()
            for item in observations
            if isinstance(item, dict)
            and str(item.get("id") or "").strip()
            and unknown_id in {
                _normalize_unknown_id(value)
                for value in item.get("target_unknown_ids", [])
            }
        ]
        pending_evidence_ids = [
            item for item in matching_evidence_ids
            if item in pending
        ]
        evidence_ids = pending_evidence_ids or matching_evidence_ids
        belief_ids = [
            item["id"]
            for item in beliefs
            if set(item.get("evidence", [])).intersection(evidence_ids)
        ]
        resolutions.append({
            **raw,
            "unknown_id": unknown_id,
            "evidence": _dedupe_strings(evidence_ids),
            "belief_ids": _dedupe_strings(belief_ids),
        })
    return resolutions


def _runtime_new_unknowns(
    value,
    analysis: dict,
    recorded_findings: dict,
    resolutions: list[dict] | None = None,
) -> list[dict]:
    if _analysis_is_read_only(analysis):
        return []
    if any(
        item.get("status") != "resolved"
        for item in resolutions or []
        if isinstance(item, dict)
    ):
        return []
    existing = _merge_unknowns(
        _initial_unknowns(analysis)
        + _unknowns(recorded_findings.get("unknowns"))
        + _unknowns(recorded_findings.get("new_unknowns"))
    )
    known_questions = {_question_key(item["question"]) for item in existing}
    used_ids = {str(item.get("id") or "").strip() for item in existing}
    next_id = max(
        (int(match.group(1)) for item in used_ids if (match := re.fullmatch(r"U(\d+)", item))),
        default=0,
    ) + 1
    result = []
    for item in _unknowns(value):
        question_key = _question_key(item.get("question", ""))
        if not question_key or question_key in known_questions:
            continue
        while f"U{next_id}" in used_ids:
            next_id += 1
        result.append({**item, "id": f"U{next_id}"})
        known_questions.add(question_key)
        used_ids.add(f"U{next_id}")
        next_id += 1
    return result


def _finish_tool_schema() -> dict:
    return openai_tool_schema(
        "finish_investigation",
        "Finish investigation using previously recorded findings.",
        {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Visible one-sentence reason why the investigation is complete or must hand off.",
                },
                "summary": {"type": "string"},
                "patch_planning_facts": {"type": "array", "items": {"type": "string"}},
                "patch_planning_context": {"type": "array", "items": {"type": "string"}},
                "recommended_next_step": {
                    "type": "string",
                    "enum": ["patch_planning", "continue_investigation", "done"],
                },
                "bugfix_readiness": {
                    "type": "object",
                    "description": "Required when finishing an implement bugfix for patch planning.",
                    "properties": {
                        "failure_reproduced_or_observed": {"type": "boolean"},
                        "root_cause_or_failing_boundary_identified": {"type": "boolean"},
                        "patch_target_identified": {"type": "boolean"},
                        "expected_behavior_change_defined": {"type": "boolean"},
                        "validation_scenario_defined": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "required": ["reason", "summary", "recommended_next_step"],
        },
    )


def _finalize_investigation(
    *,
    provider: dict,
    model: str,
    messages: list[dict],
    pricing_rules: list[dict],
    usage_total: dict,
    run_id: str,
    analysis: dict | None = None,
    observations: list[dict] | None = None,
    recorded_findings: dict | None = None,
    audit_cache: dict[str, dict] | None = None,
    reason: str = "Investigation needs a final structured summary.",
) -> Iterator[dict]:
    messages.append({"role": "user", "content": prompt.build_investigation_finalize(reason)})
    last_error = ""
    last_content = ""
    last_arguments: dict | None = None

    attempts = app_settings.get_round_limit("investigation_finalization_attempts")
    repeated_tool_error_name = ""
    repeated_tool_error_count = 0
    repeated_finalization_error_key = ""
    repeated_finalization_error_count = 0
    stop_finalization = False
    quality_audit: dict = {}
    best_progress = _finalization_progress_score(recorded_findings, observations or [])
    no_progress_attempts = 0
    for attempt in _round_indexes(attempts, start=0):
        thinking_id = f"{run_id}-thinking-final-{attempt}"
        yield start_event(thinking_id, "thinking", {
            "text": reason,
            "done": False,
            "open": True,
        })
        assistant = _call_model(
            provider,
            model,
            messages,
            tools=[_resolve_unknowns_tool_schema(), _record_findings_tool_schema(), _finish_tool_schema()],
            tool_choice="required",
        )
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-final-{attempt}", "usage", {
                "delta": usage,
                "total": usage_total,
            })

        tool_calls = assistant.get("tool_calls") or []
        content = _assistant_visible_text(assistant) or _tool_call_summary(tool_calls)
        last_content = content or last_content
        record_calls = [
            call for call in tool_calls
            if ((call.get("function") or {}).get("name") in {"resolve_unknowns", "record_investigation_findings"})
        ]
        finish_call = next(
            (call for call in tool_calls if ((call.get("function") or {}).get("name") == "finish_investigation")),
            None,
        )
        replay = {"role": "assistant", "content": assistant.get("content") or ""}
        for key in ("reasoning_content", "reasoning"):
            if assistant.get(key):
                replay[key] = assistant[key]
        if record_calls or finish_call:
            replay["tool_calls"] = record_calls + ([finish_call] if finish_call else [])
        messages.append(replay)
        yield {"op": "update", "id": thinking_id, "patch": {
            "text": content,
            "done": True,
            "open": bool(finish_call),
        }}

        for record_call in record_calls:
            call_id = record_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = record_call.get("function") or {}
            record_name = str(function.get("name") or "")
            try:
                record_arguments = _tool_arguments(function.get("arguments"))
                if record_name == "resolve_unknowns":
                    record_arguments = _resolve_unknown_arguments(record_arguments)
                    _require_control_reason(record_arguments, "resolve_unknowns")
                    if not record_arguments.get("resolutions"):
                        raise ValueError("resolve_unknowns requires at least one valid resolution")
                    _validate_resolution_refs(
                        record_arguments["resolutions"],
                        _beliefs((recorded_findings or {}).get("beliefs")),
                        observations or [],
                    )
                else:
                    record_arguments = _record_arguments(record_arguments)
                    _require_control_reason(record_arguments, "record_investigation_findings")
                if record_name != "resolve_unknowns" and ((analysis or {}).get("_canonicalized") or not _has_finding_fields(record_arguments)):
                    record_arguments = yield from _record_findings_by_slots(
                        provider=provider,
                        model=model,
                        messages=messages[:-1],
                        pricing_rules=pricing_rules,
                        usage_total=usage_total,
                        run_id=run_id,
                        reason=str(record_arguments.get("reason") or "").strip(),
                        analysis=analysis or {},
                        observations=observations or [],
                        recorded_findings=recorded_findings or _empty_recorded_findings(),
                        pending_observation_ids=[],
                        required_resolution_ids=_unknowns_needing_resolution(
                            recorded_findings or _empty_recorded_findings(),
                            observations or [],
                            analysis,
                        ),
                    )
                _require_finding_fields(record_arguments)
                recorded_findings = _merge_recorded_findings(
                    recorded_findings or _empty_recorded_findings(),
                    record_arguments,
                )
                recorded_findings = _bind_grounding_evidence(
                    recorded_findings,
                    observations or [],
                )
                recorded_findings = _apply_direct_resolution_gate(
                    recorded_findings,
                    observations or [],
                )
                if (analysis or {}).get("_canonicalized"):
                    quality_audit = yield from _audit_recorded_findings(
                        provider=provider,
                        model=model,
                        messages=[
                            item for item in messages[:-1]
                            if item.get("role") != "system"
                        ],
                        pricing_rules=pricing_rules,
                        usage_total=usage_total,
                        run_id=run_id,
                        analysis=analysis or {},
                        observations=observations or [],
                        recorded_findings=recorded_findings,
                        audit_cache=audit_cache,
                    )
                    recorded_findings, _, _ = _apply_investigation_audit(
                        recorded_findings,
                        quality_audit,
                        observations=observations or [],
                    )
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps({"recorded": True, "tool": record_name}, ensure_ascii=False),
                })
            except Exception as exc:
                last_error = f"{record_name or 'record_investigation_findings'} arguments were invalid: {exc}"
                raw_arguments = function.get("arguments") or "{}"
                partial_arguments = _partial_tool_arguments(raw_arguments)
                if record_name != "resolve_unknowns":
                    partial_arguments = _record_arguments(partial_arguments)
                if _has_finding_fields(partial_arguments):
                    recorded_findings = _merge_recorded_findings(
                        recorded_findings or _empty_recorded_findings(),
                        partial_arguments,
                    )
                if repeated_tool_error_name == record_name:
                    repeated_tool_error_count += 1
                else:
                    repeated_tool_error_name = record_name
                    repeated_tool_error_count = 1
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _tool_repair_error_json(
                        exc,
                        record_name or "record_investigation_findings",
                        raw_arguments,
                        partial_arguments,
                    ),
                })
                stop_finalization = repeated_tool_error_count >= MAX_REPEATED_TOOL_ERRORS

        if finish_call:
            call_id = finish_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = finish_call.get("function") or {}
            try:
                last_arguments = _tool_arguments(function.get("arguments"))
                _require_control_reason(last_arguments, "finish_investigation")
                final = _finish_payload(
                    _finish_arguments(
                        recorded_findings or _empty_recorded_findings(),
                        last_arguments,
                        prefer_finish_summary=not _analysis_requests_implementation(analysis),
                    ),
                    analysis=analysis,
                    observations=observations or [],
                )
            except Exception as exc:
                last_error = f"finish_investigation arguments were invalid: {exc}"
                raw_arguments = function.get("arguments") or "{}"
                partial_arguments = _partial_tool_arguments(raw_arguments)
                if partial_arguments:
                    last_arguments = partial_arguments
                if repeated_tool_error_name == "finish_investigation":
                    repeated_tool_error_count += 1
                else:
                    repeated_tool_error_name = "finish_investigation"
                    repeated_tool_error_count = 1
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _tool_repair_error_json(
                        ValueError(last_error),
                        "finish_investigation",
                        raw_arguments,
                        partial_arguments,
                    ),
                })
                stop_finalization = repeated_tool_error_count >= MAX_REPEATED_TOOL_ERRORS
            else:
                if quality_audit:
                    final["quality_audit"] = quality_audit
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(final, ensure_ascii=False),
                })
                if final.get("resolution_repair") and (attempts <= 0 or attempt < attempts - 1):
                    last_error = "Investigation findings need explicit resolutions before finalizing."
                    messages.append({
                        "role": "user",
                        "content": _resolution_repair_prompt(final["resolution_repair"]),
                    })
                else:
                    return final
        elif content:
            last_error = "Investigation finalization must use resolve_unknowns or record_investigation_findings, then finish_investigation."
        else:
            last_error = "Investigation finalization started before finish_investigation was called."

        failure_key = json.dumps({
            "error": last_error,
            "content": content,
            "tool_calls": bool(tool_calls),
        }, ensure_ascii=False, sort_keys=True)
        if failure_key == repeated_finalization_error_key:
            repeated_finalization_error_count += 1
        else:
            repeated_finalization_error_key = failure_key
            repeated_finalization_error_count = 1
        progress = _finalization_progress_score(recorded_findings, observations or [])
        if progress > best_progress:
            best_progress = progress
            no_progress_attempts = 0
        else:
            no_progress_attempts += 1

        if stop_finalization:
            return _runtime_recovered_investigation(
                last_error or "Investigation finalization repeated the same invalid tool call.",
                analysis,
                observations or [],
                recorded_findings or _empty_recorded_findings(),
            )
        if repeated_finalization_error_count >= MAX_REPEATED_TOOL_ERRORS:
            return _runtime_recovered_investigation(
                last_error or "Investigation finalization repeated the same invalid response.",
                analysis,
                observations or [],
                recorded_findings or _empty_recorded_findings(),
            )
        if no_progress_attempts >= MAX_REPEATED_TOOL_ERRORS:
            return _runtime_recovered_investigation(
                last_error or "Investigation finalization made no contract progress.",
                analysis,
                observations or [],
                recorded_findings or _empty_recorded_findings(),
            )

        if attempts <= 0 or attempt < attempts - 1:
            messages.append({
                "role": "user",
                "content": (
                    f"Previous finalization failed: {last_error}\n"
                    "Do not call discovery tools or repeat investigation. "
                    "Fix only the finalization arguments: create/cite belief ids like B1 in "
                    "record_investigation_findings, use those ids in resolution.belief_ids, "
                    "then call finish_investigation with summary and recommended_next_step."
                ),
            })

    if last_arguments:
        try:
            return _finish_payload(
                _finish_arguments(
                    recorded_findings or _empty_recorded_findings(),
                    last_arguments,
                    prefer_finish_summary=not _analysis_requests_implementation(analysis),
                ),
                analysis=analysis,
                observations=observations or [],
                repair_conflicts=True,
            )
        except Exception:
            pass

    return _runtime_recovered_investigation(
        last_error or "finish_investigation did not produce a usable result.",
        analysis,
        observations or [],
        recorded_findings or _empty_recorded_findings(),
    )


def _runtime_recovered_investigation(
    reason: str,
    analysis: dict | None,
    observations: list[dict],
    recorded_findings: dict,
) -> dict:
    facts = _runtime_patch_facts(observations, recorded_findings)
    initial_unknowns = _initial_unknowns(analysis)
    recorded_unknowns = _unknowns(recorded_findings.get("unknowns"))
    if not _analysis_is_read_only(analysis):
        recorded_unknowns += _unknowns(recorded_findings.get("new_unknowns"))
    known_unknowns = _merge_unknowns(initial_unknowns + recorded_unknowns)
    resolutions = _complete_resolutions(
        _resolutions(recorded_findings.get("resolutions")),
        known_unknowns,
        recorded_unknowns,
    )
    unknowns = _unresolved_from_resolutions(resolutions, known_unknowns)
    read_only_complete = _analysis_is_read_only(analysis) and not unknowns
    read_only_summary = "\n\n".join(
        str(item.get("answer") or "").strip()
        for item in resolutions
        if item.get("status") == "resolved" and str(item.get("answer") or "").strip()
    )
    return {
        "summary": (
            read_only_summary
            if read_only_complete and read_only_summary
            else "Runtime recovered from repeated invalid investigation tool arguments."
        ),
        "ready_for_patch_planning": False,
        "runtime_recovered": True,
        "runtime_failure": not read_only_complete,
        "recovery_reason": reason,
        "beliefs": recorded_findings.get("beliefs", []),
        "resolutions": resolutions,
        "unknowns": unknowns,
        "open_questions": [] if read_only_complete else [reason],
        "patch_planning_facts": facts,
        "patch_planning_context": facts,
    }


def _finalization_progress_score(
    recorded_findings: dict | None,
    observations: list[dict],
) -> tuple[int, int, int]:
    recorded = recorded_findings or {}
    observation_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    beliefs = _beliefs(recorded.get("beliefs"))
    resolutions = _resolutions(recorded.get("resolutions"))
    grounded_beliefs = sum(
        1 for item in beliefs
        if item.get("evidence")
        and set(item["evidence"]).issubset(observation_ids)
    )
    resolved = sum(1 for item in resolutions if item.get("status") == "resolved")
    evidence_links = sum(
        len(item.get("evidence", [])) + len(item.get("belief_ids", []))
        for item in resolutions
    )
    return grounded_beliefs, resolved, evidence_links


def _runtime_patch_facts(observations: list[dict], recorded_findings: dict) -> list[str]:
    facts = []
    for belief in recorded_findings.get("beliefs", []):
        if (
            isinstance(belief, dict)
            and belief.get("status") in {"supported", "strongly_supported", "runtime_confirmed"}
            and belief.get("evidence")
            and _belief_text(belief)
        ):
            facts.append(_belief_text(belief))
    for resolution in recorded_findings.get("resolutions", []):
        if (
            isinstance(resolution, dict)
            and resolution.get("status") == "resolved"
            and resolution.get("answer")
            and (
                resolution.get("evidence")
                or resolution.get("belief_ids")
                or resolution.get("reason") == CLEARIFY_RESOLUTION_REASON
            )
        ):
            facts.append(str(resolution["answer"]))
    for item in observations:
        if isinstance(item, dict) and item.get("summary"):
            facts.append(f"{item.get('tool') or 'tool'}: {item['summary']}")
    return _dedupe_strings(facts)[:20]


def _dedupe_strings(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = " ".join(str(value or "").split())
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _clearify_question(
    arguments: dict,
    *,
    question_id: str,
    analysis: dict | None,
) -> dict:
    target_ids = _target_unknown_ids(arguments)
    question = str(arguments.get("question") or "").strip()
    if not question:
        raise ValueError("clearify requires question")
    options = _clearify_options(arguments.get("options"))
    return {
        "id": question_id,
        "question_id": question_id,
        "analysis_id": (analysis or {}).get("id", ""),
        "unknown_id": target_ids[0] if target_ids else "",
        "question": question,
        "options": options[:3],
        "custom_allowed": True,
        "origin_message": (analysis or {}).get("origin_message", ""),
        "clearify_tool": True,
        "tool_name": "clearify",
    }


def _clearify_options(raw_options) -> list[dict]:
    if not isinstance(raw_options, list) or len(raw_options) != 3:
        raise ValueError("clearify requires exactly three options")
    options = []
    for index, raw in enumerate(raw_options, start=1):
        if not isinstance(raw, dict):
            raise ValueError("clearify options must be objects")
        label = str(raw.get("label") or raw.get("value") or "").strip()
        value = str(raw.get("value") or label).strip()
        if not label or not value:
            raise ValueError("clearify option label and value are required")
        option = dict(raw)
        option["id"] = str(option.get("id") or f"option_{index}").strip()
        option["label"] = label
        option["value"] = value
        options.append(option)
    return options


def _clearify_tool_result(answer: dict | None) -> str:
    answer = answer or {}
    response = str(answer.get("response") or answer.get("text") or "").strip()
    return json.dumps({
        "question": answer.get("question") or "",
        "selected_option_id": answer.get("selected_option_id") or "",
        "selected_option_label": answer.get("selected_option_label") or "",
        "answer": response,
    }, ensure_ascii=False)


def _clearify_resolution(arguments: dict, answer: dict | None) -> dict | None:
    resolutions = _clearify_resolutions(arguments, answer)
    return resolutions[0] if resolutions else None


def _clearify_resolutions(arguments: dict, answer: dict | None) -> list[dict]:
    return [
        item for item in _clearify_resolution_records(arguments, answer)
        if item.get("status") == "resolved"
    ]


def _clearify_resolution_records(arguments: dict, answer: dict | None) -> list[dict]:
    answer = answer or {}
    text = _clearify_answer_text(answer)
    if not text:
        raise ValueError("clearify answer is empty")
    target_ids = _target_unknown_ids(arguments)
    if not target_ids:
        if arguments.get("orientation"):
            return []
        raise ValueError("clearify answer has no target unknown")
    status = "partially_resolved" if _clearify_answer_is_non_answer(text) else "resolved"
    reason = CLEARIFY_UNRESOLVED_REASON if status == "partially_resolved" else CLEARIFY_RESOLUTION_REASON
    return [
        {
            "unknown_id": unknown_id,
            "status": status,
            "answer": text,
            "evidence": [],
            "belief_ids": [],
            "reason": reason,
        }
        for unknown_id in target_ids
    ]


def _clearify_answer_text(answer: dict) -> str:
    return str(
        answer.get("response")
        or answer.get("text")
        or answer.get("selected_option_label")
        or ""
    ).strip()


def _clearify_answer_is_non_answer(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    if not normalized:
        return True
    if any(marker in normalized for marker in ("but", "但是", "但")) and any(
        marker in normalized
        for marker in ("choose", "select", "use ", "option", "方案", "选择", "用")
    ):
        return False
    return any(
        marker in normalized
        for marker in (
            "don't know",
            "do not know",
            "not sure",
            "no idea",
            "cannot answer",
            "can't answer",
            "you check",
            "you investigate",
            "please investigate",
            "不知道",
            "不清楚",
            "不确定",
            "无法确定",
            "无法回答",
            "没法回答",
            "你再查",
            "你再检查",
            "你查一下",
            "你继续查",
        )
    )


def _run_tool_stream(name: str, call_id: str, arguments: dict, workspace_dir: str, analysis: dict | None = None) -> Iterator[dict]:
    registered_tool = registry.get(name)
    if (
        registered_tool is None
        or INVESTIGATION_CAPABILITY not in registered_tool.capabilities
    ):
        raise ValueError(f"unknown investigation tool: {name or 'tool'}")
    target_unknown_ids = _target_unknown_ids(arguments)
    reason = str(arguments.pop("reason", "") or "").strip()
    orientation = bool(arguments.pop("orientation", False))
    discovery_contract = {
        field: str(arguments.pop(field, "") or "").strip()
        for field in DISCOVERY_CONTRACT_FIELDS
    }
    arguments.pop("target_unknown_ids", None)
    _validate_tool_contract(
        name,
        target_unknown_ids=target_unknown_ids,
        reason=reason,
        orientation=orientation,
        discovery_contract=discovery_contract,
        analysis=analysis,
    )

    if name == "subagent" and (arguments.get("agent") or arguments.get("name")):
        agent = str(arguments.get("agent") or arguments.get("name"))
        if agent.strip().removeprefix("@").casefold() == "hypothesis-verifier":
            from . import subagents

            task = str(arguments.get("task") or "")
            _reject_batched_hypothesis(task)
            done = {}
            for packet in subagents.run_stream(agent, task, workspace_dir):
                if packet.get("op") == "done":
                    done = packet
                else:
                    yield packet
            if done.get("error"):
                raise ValueError(str(done["error"]))
            if not done:
                raise ValueError("hypothesis verifier returned no result")
            done["target_unknown_ids"] = target_unknown_ids
            done["reason"] = reason
            done["orientation"] = orientation
            done["investigation_contract"] = discovery_contract
            return json.dumps(done, ensure_ascii=False)

    tool = registered_tool
    yield start_event(call_id, registry.event_type(name), {
        "name": name,
        "description": tool.description,
        "status": "running",
        "open": False,
        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
        "output": "",
        "target_unknown_ids": target_unknown_ids,
        "reason": reason,
        "orientation": orientation,
        "investigation_contract": discovery_contract,
    })
    result = asyncio.run(tool.execute(arguments, {"directory": workspace_dir}))
    yield {"op": "update", "id": call_id, "patch": {
        "status": "error" if result.title.startswith("[error]") else "done",
        "title": result.title,
        "output": result.output,
        "metadata": {
            **result.metadata,
            "target_unknown_ids": target_unknown_ids,
            "reason": reason,
            "orientation": orientation,
            "investigation_contract": discovery_contract,
        },
    }}
    return json.dumps({
        "tool_call_id": call_id,
        "target_unknown_ids": target_unknown_ids,
        "reason": reason,
        "orientation": orientation,
        "investigation_contract": discovery_contract,
        "title": result.title,
        "output": result.output,
        "metadata": {
            **result.metadata,
            "target_unknown_ids": target_unknown_ids,
            "reason": reason,
            "orientation": orientation,
            "investigation_contract": discovery_contract,
        },
    }, ensure_ascii=False)


def _validate_tool_contract(
    name: str,
    *,
    target_unknown_ids: list[str],
    reason: str,
    orientation: bool,
    analysis: dict | None,
    discovery_contract: dict[str, str] | None = None,
) -> None:
    if not reason:
        raise ValueError(f"{name} requires reason")
    discovery_contract = discovery_contract or {}
    if name != "clearify":
        missing = [
            field for field in DISCOVERY_CONTRACT_FIELDS
            if not str(discovery_contract.get(field) or "").strip()
        ]
        if missing:
            raise ValueError(
                f"{name} requires discovery contract fields: " + ", ".join(missing)
            )
    known = {
        str(item.get("id") or "").strip(): item
        for item in (analysis or {}).get("unknowns", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    if not orientation and not target_unknown_ids:
        raise ValueError(f"{name} requires target_unknown_ids unless orientation is true")
    unknown = [item for item in target_unknown_ids if known and item not in known]
    if unknown:
        raise ValueError(f"{name} target_unknown_ids not in task contract: {', '.join(unknown)}")
    if name == "clearify":
        invalid = [
            item for item in target_unknown_ids
            if known.get(item, {}).get("type") != "product_decision"
        ]
        if invalid:
            raise ValueError(
                "clearify requires product_decision targets: " + ", ".join(invalid)
            )


def _target_unknown_ids(arguments: dict) -> list[str]:
    raw = arguments.get("target_unknown_ids")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [item for value in raw if (item := _normalize_unknown_id(value))]


def _normalize_unknown_id(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if ":" in text:
        text = text.rsplit(":", 1)[-1].strip()
    return text


def _tool_observation(name: str, call_id: str, output: str) -> dict:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    raw_metadata = data.get("metadata")
    metadata: dict = raw_metadata if isinstance(raw_metadata, dict) else {}
    evidence = str(data.get("output") or output)
    return {
        "id": call_id,
        "tool": name,
        "title": str(data.get("title") or name),
        "summary": _short_observation(evidence),
        "evidence_excerpt": _observation_evidence_excerpt(evidence),
        "_grounding_evidence": evidence,
        "verification": data.get("run") if isinstance(data.get("run"), dict) else {},
        "target_unknown_ids": data.get("target_unknown_ids") or metadata.get("target_unknown_ids") or [],
        "reason": data.get("reason") or metadata.get("reason") or "",
        "orientation": bool(data.get("orientation") or metadata.get("orientation")),
        "investigation_contract": (
            data.get("investigation_contract")
            if isinstance(data.get("investigation_contract"), dict)
            else metadata.get("investigation_contract")
            if isinstance(metadata.get("investigation_contract"), dict)
            else {}
        ),
        "path": metadata.get("path", ""),
        "mtime_ns": metadata.get("mtime_ns"),
        "size": metadata.get("size"),
    }


def _short_observation(value) -> str:
    text = " ".join(str(value or "").split())
    return text[:240]


def _observation_evidence_excerpt(value) -> str:
    text = str(value or "")
    if len(text) <= OBSERVATION_EVIDENCE_CHARS:
        return text
    half = OBSERVATION_EVIDENCE_CHARS // 2
    return f"{text[:half]}\n...\n{text[-half:]}"


def _reject_batched_hypothesis(task: str) -> None:
    text = " ".join((task or "").split())
    numbered = len(re.findall(r"(?:^|\s)[1-7]\.\s", text))
    if numbered >= 2:
        raise ValueError(
            "hypothesis-verifier accepts exactly one atomic belief; split this numbered list into separate calls"
        )
    lowered = text.casefold()
    if any(phrase in lowered for phrase in (
        "following clauses",
        "for each clause",
        "all of these",
        "the following hypotheses",
        "verify these",
    )):
        raise ValueError(
            "hypothesis-verifier accepts exactly one atomic belief; verify one belief at a time"
        )


def _tool_arguments(raw: str | None) -> dict:
    try:
        arguments = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid tool JSON: {exc}") from exc
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    return arguments


def _investigation_tool_arguments(
    name: str,
    raw: str | None,
    *,
    pending_observation_ids: list[str],
    resolution_required_ids: list[str],
) -> dict:
    try:
        return _tool_arguments(raw)
    except ValueError:
        if (
            name == "record_investigation_findings"
            and (pending_observation_ids or resolution_required_ids)
        ):
            return {"reason": RECORD_RECOVERY_REASON}
        raise


def _resolution_repair_request(
    arguments: dict,
    initial_unknowns: list[dict],
    explicit_resolutions: list[dict],
    beliefs: list[dict],
    patch_context: list[str],
) -> dict | None:
    initial_ids = [item["id"] for item in initial_unknowns if item.get("blocking")]
    if not initial_ids:
        return None
    resolution_ids = [item["unknown_id"] for item in explicit_resolutions]
    missing = [
        unknown_id
        for unknown_id in initial_ids
        if not any(_same_unknown_id(unknown_id, resolved_id) for resolved_id in resolution_ids)
    ]
    if not missing:
        return None
    misplaced = _misplaced_resolution_ids(arguments.get("unknowns"), initial_ids)
    if not _has_resolution_repair_signal(arguments, explicit_resolutions, beliefs, patch_context, misplaced):
        return None
    return {
        "next_step": "repair_findings",
        "missing_resolution_ids": missing,
        "misplaced_resolution_ids": misplaced,
        "instruction": (
            "Reuse existing evidence. Do not call discovery tools. "
            "Call resolve_unknowns with explicit resolutions for these ids."
        ),
    }


def _has_resolution_repair_signal(
    arguments: dict,
    explicit_resolutions: list[dict],
    beliefs: list[dict],
    patch_context: list[str],
    misplaced_resolution_ids: list[str],
) -> bool:
    if explicit_resolutions or beliefs or patch_context or misplaced_resolution_ids:
        return True
    if arguments.get("ready_for_patch_planning"):
        return True
    if str(arguments.get("recommended_next_step") or "").strip() == "patch_planning":
        return True
    if _string_list(arguments.get("user_decisions_required")):
        return True
    return any(
        isinstance(item, dict)
        and (
            str(item.get("status") or "").strip() in {"known", "deferred", "blocked", "resolved"}
            or bool(str(item.get("text") or "").strip())
        )
        for item in arguments.get("task_updates") or []
    )


def _misplaced_resolution_ids(value, initial_ids: list[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    misplaced = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        unknown_id = str(raw.get("unknown_id") or raw.get("id") or "").strip()
        status = str(raw.get("status") or "").strip()
        if (
            any(_same_unknown_id(unknown_id, initial_id) for initial_id in initial_ids)
            and status in {"resolved", "known", "done", "complete", "completed"}
        ):
            misplaced.add(unknown_id)
    return [
        unknown_id
        for unknown_id in initial_ids
        if any(_same_unknown_id(unknown_id, item) for item in misplaced)
    ]


def _resolution_repair_prompt(repair: dict) -> str:
    missing = ", ".join(str(item) for item in repair.get("missing_resolution_ids", []) if str(item).strip())
    misplaced = ", ".join(str(item) for item in repair.get("misplaced_resolution_ids", []) if str(item).strip())
    lines = [
        "Previous finalization needs findings repair, not more investigation.",
        f"Missing explicit resolution ids: {missing or 'none'}",
    ]
    if misplaced:
        lines.append(f"These ids look like resolved answers were written into unknowns instead: {misplaced}")
    lines.extend([
        "Do not call discovery tools or read more files.",
        "Call resolve_unknowns with explicit resolutions for every missing id.",
        "Each resolution must include unknown_id, status, answer, evidence or belief_ids, and reason.",
        "Then call finish_investigation again with the same final summary and next step.",
    ])
    return "\n".join(lines)


def _finish_payload(
    arguments: dict,
    *,
    analysis: dict | None = None,
    observations: list[dict] | None = None,
    expected_step: dict | None = None,
    required_items: list[dict] | None = None,
    repair_conflicts: bool = False,
    strict_readiness: bool = False,
) -> dict:
    repairs: list[str] = []
    explicit_unknowns = _unknowns(arguments.get("unknowns"))
    unknowns = list(explicit_unknowns)
    new_unknowns = [] if _analysis_is_read_only(analysis) else _unknowns(arguments.get("new_unknowns"))
    initial_unknowns = _initial_unknowns(analysis)
    user_decisions = _string_list(arguments.get("user_decisions_required"))
    decision_questions = [_decision_question(item) for item in user_decisions]
    known_unknowns = initial_unknowns + unknowns + new_unknowns
    decision_unknowns = [
        {
            "id": _unknown_id_for_question(question, known_unknowns) or f"D{index}",
            "question": question,
            "blocking": True,
            "resolution_strategy": "clearify",
        }
        for index, question in enumerate(decision_questions, start=1)
    ]
    open_questions = arguments.get("open_questions") if isinstance(arguments.get("open_questions"), list) else []
    open_questions = [
        _decision_question(question) for question in _clean_questions(open_questions)
        if _decision_question(question) not in set(decision_questions)
    ]
    if open_questions and not unknowns and not decision_unknowns:
        unknowns = [
            {
                "id": f"Q{index}",
                "question": question,
                "blocking": True,
                "resolution_strategy": "clearify",
            }
            for index, question in enumerate(_clean_questions(open_questions), start=1)
        ]
    beliefs = _beliefs(arguments.get("beliefs"))
    if repair_conflicts:
        beliefs = _drop_invalid_belief_refs(beliefs, observations or [], repairs)
    else:
        _validate_belief_refs(beliefs, observations or [])
    explicit_resolutions = _resolutions(arguments.get("resolutions"))
    resolutions = list(explicit_resolutions)
    resolutions = _complete_resolutions(resolutions, initial_unknowns, unknowns)
    if repair_conflicts:
        resolutions = _drop_invalid_resolution_refs(resolutions, beliefs, observations or [], repairs)
    else:
        _validate_resolution_refs(resolutions, beliefs, observations or [])
    resolutions = _enforce_resolution_evidence(resolutions, initial_unknowns, strict=not repair_conflicts)
    unknowns = _drop_resolved_unknowns(unknowns, resolutions, repairs)
    unresolved = _unresolved_from_resolutions(resolutions, initial_unknowns)
    unknowns = _merge_unknowns(unresolved + unknowns + decision_unknowns + new_unknowns)
    patch_context = _patch_context(arguments.get("patch_planning_facts"), repairs, repair_conflicts)
    if not patch_context:
        patch_context = _patch_context(arguments.get("patch_planning_context"), repairs, repair_conflicts)
    model_ready = bool(arguments.get("ready_for_patch_planning"))
    ready = model_ready
    if expected_step and expected_step.get("next_step") == "write_code" and not ready:
        if not repair_conflicts:
            raise ValueError("finish_investigation conflicts with accepted write_code checkpoint")
        ready = True
        for item in unknowns:
            item["blocking"] = False
            item["resolution_strategy"] = "deferred"
        repairs.append("Deferred blockers that conflicted with accepted write_code checkpoint")
    if any(item["blocking"] for item in unknowns):
        if model_ready and any(item["blocking"] for item in explicit_unknowns) and not repair_conflicts:
            raise ValueError("ready_for_patch_planning conflicts with blocking unknowns")
        ready = False
    _require_items_accounted(required_items, arguments.get("task_updates"), resolutions, repair_conflicts)
    unknowns = _resolve_task_update_conflicts(unknowns, arguments.get("task_updates"), repairs, repair_conflicts)
    if not unknowns and repair_conflicts:
        ready = True
    readiness = _runtime_readiness(
        model_ready=ready,
        analysis=analysis,
        initial_unknowns=initial_unknowns,
        resolutions=resolutions,
        unknowns=unknowns,
        patch_context=patch_context,
        finish_arguments=arguments,
    )
    if strict_readiness and model_ready:
        strict_reasons = [
            str(reason)
            for reason in readiness.get("reasons", [])
            if str(reason).startswith("bugfix_readiness:")
        ]
        if strict_reasons:
            raise ValueError(
                "bugfix_readiness is required when ready_for_patch_planning is true: "
                + ", ".join(strict_reasons)
            )
    bugfix_reasons = [
        str(reason)
        for reason in readiness.get("reasons", [])
        if str(reason).startswith("bugfix_readiness:")
    ]
    if bugfix_reasons and not unknowns:
        unknowns = _merge_unknowns([{
            "id": "bugfix_readiness",
            "question": (
                "Which project evidence confirms the observed failure or boundary, root cause, "
                "patch target, expected behavior change, and validation scenario for this bugfix?"
            ),
            "blocking": True,
            "resolution_strategy": "investigate_project",
        }])
    ready = readiness["ready"]
    hard_readiness_reasons = [
        reason for reason in readiness.get("reasons", [])
        if (
            reason.endswith(":not_resolved")
            or reason.endswith(":missing_evidence")
            or reason.startswith("bugfix_readiness:")
        )
    ]
    if not ready and model_ready and patch_context and not any(
        item.get("blocking") and item.get("resolution_strategy") == "clearify"
        for item in unknowns
    ) and not hard_readiness_reasons:
        ready = True
        readiness = {**readiness, "ready": True, "runtime_override": "patch_facts_present"}
        for item in unknowns:
            if item.get("blocking"):
                item["blocking"] = False
                item["resolution_strategy"] = "deferred"
        repairs.append("Allowed patch planning from grounded patch facts and deferred remaining investigate_project unknowns")
    repair_request = _resolution_repair_request(
        arguments,
        initial_unknowns,
        explicit_resolutions,
        beliefs,
        patch_context,
    )
    final = {
        "summary": str(arguments.get("summary") or "").strip(),
        "ready_for_patch_planning": ready,
        "beliefs": beliefs,
        "open_questions": open_questions,
        "resolutions": resolutions,
        "new_unknowns": new_unknowns,
        "user_decisions_required": user_decisions,
        "unknowns": unknowns,
        "task_updates": _investigation_task_updates(
            arguments.get("task_updates"),
            initial_unknowns + unknowns,
            resolutions,
        ),
        "patch_planning_context": patch_context,
        "patch_planning_facts": patch_context,
        "recommended_next_step": str(arguments.get("recommended_next_step") or "").strip(),
        "readiness": readiness,
        "protocol_repairs": repairs,
    }
    final["summary"] = _downgrade_unproven_entrypoint_overclaims(final["summary"], analysis)
    final["project_facts"] = _investigation_project_facts(
        beliefs,
        resolutions,
        analysis,
    )
    if repair_request:
        final["resolution_repair"] = repair_request
    return final


def _downgrade_unproven_entrypoint_overclaims(summary: str, analysis: dict | None) -> str:
    if not summary or not _analysis_is_read_only(analysis) or not _is_debt_report_request(analysis):
        return summary
    return re.sub(
        r"(?ms)(^#{2,4}[^\n]*(?:High|高严重度|高嚴重度)[^\n]*\n.*?)(?=^#{2,4}\s|\Z)",
        lambda match: _downgrade_entrypoint_overclaim_block(match.group(1)),
        summary,
    )


def _downgrade_entrypoint_overclaim_block(block: str) -> str:
    if not _is_entrypoint_overclaim(block):
        return block
    downgraded = re.sub(r"^##\s+High\b", "## Medium", block)
    downgraded = downgraded.replace("高严重度", "中严重度").replace("高嚴重度", "中嚴重度")
    downgraded = ENTRYPOINT_OVERCLAIM_PATTERN.sub(
        "入口/包形态不明确，不能仅凭入口缺失证明项目不能运行",
        downgraded,
    )
    return downgraded


def _is_debt_report_request(analysis: dict | None) -> bool:
    texts = [
        str((analysis or {}).get("summary") or ""),
        *[
            str(item.get("text") or "")
            for item in (analysis or {}).get("requirements", [])
            if isinstance(item, dict)
        ],
    ]
    joined = " ".join(texts).casefold()
    return any(term in joined for term in DEBT_REPORT_TERMS)


def _is_entrypoint_overclaim(value: str) -> bool:
    lowered = value.casefold()
    return (
        any(term in lowered for term in ENTRYPOINT_TERMS + PACKAGE_SHAPE_TERMS)
        and (
            any(term in lowered for term in ENTRYPOINT_OVERCLAIM_TERMS)
            or bool(ENTRYPOINT_OVERCLAIM_PATTERN.search(value))
        )
    )


def _investigation_project_facts(
    beliefs: list[dict],
    resolutions: list[dict],
    analysis: dict | None,
) -> list[dict]:
    unknowns = {
        str(item.get("id") or "").strip(): item
        for item in (analysis or {}).get("unknowns", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    belief_by_id = {
        str(item.get("id") or "").strip(): item
        for item in beliefs
        if str(item.get("id") or "").strip()
    }
    facts = []
    seen = set()
    for resolution in resolutions:
        if resolution.get("status") != "resolved" or not resolution.get("answer"):
            continue
        unknown_id = str(resolution.get("unknown_id") or "").strip()
        evidence_ids = list(resolution.get("evidence", []))
        for belief_id in resolution.get("belief_ids", []):
            evidence_ids.extend(belief_by_id.get(belief_id, {}).get("evidence", []))
        text = str(resolution["answer"]).strip()
        user_decision = _is_user_product_decision(resolution, list(unknowns.values()))
        facts.append({
            "id": f"PF{len(facts) + 1}",
            "text": text,
            "authority": (
                "user_explicit"
                if user_decision
                else "verified_fact"
            ),
            "unknown_ids": [unknown_id] if unknown_id else [],
            "acceptance_criteria_ids": list(
                unknowns.get(unknown_id, {}).get("acceptance_criteria_ids", [])
            ),
            "evidence_ids": _dedupe_strings(evidence_ids),
            "belief_ids": list(resolution.get("belief_ids", [])),
        })
        seen.add(" ".join(text.split()).casefold())
    for belief in beliefs:
        text = str(belief.get("statement") or "").strip()
        normalized = " ".join(text.split()).casefold()
        if (
            not text
            or normalized in seen
            or belief.get("status") not in {"supported", "strongly_supported", "runtime_confirmed"}
            or not belief.get("evidence")
        ):
            continue
        facts.append({
            "id": f"PF{len(facts) + 1}",
            "text": text,
            "authority": "verified_fact",
            "unknown_ids": [],
            "acceptance_criteria_ids": [],
            "evidence_ids": list(belief.get("evidence", [])),
            "belief_ids": [belief.get("id", "")],
        })
        seen.add(normalized)
    return facts


def _empty_recorded_findings() -> dict:
    return {field: [] for field in FINDING_FIELDS}


def _continued_recorded_findings(previous: dict | None, observations: list[dict]) -> dict:
    recorded = _merge_recorded_findings(_empty_recorded_findings(), previous or {})
    observation_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    by_tail = {
        item.rsplit(":", 1)[-1]: item
        for item in observation_ids
    }
    for field in ("beliefs", "resolutions"):
        recorded[field] = [
            dict(item) if isinstance(item, dict) else item
            for item in recorded[field]
        ]
        for item in recorded[field]:
            if not isinstance(item, dict):
                continue
            evidence = item.get("evidence")
            if isinstance(evidence, list):
                item["evidence"] = [
                    value if value in observation_ids else by_tail.get(value, value)
                    for value in (str(raw).strip() for raw in evidence)
                    if value
                ]
    return recorded


def _apply_investigation_audit(
    recorded: dict,
    audit: dict,
    *,
    observations: list[dict] | None = None,
    strict_grounding: bool = True,
    allow_verification: bool = True,
) -> tuple[dict, list[dict], dict[str, str]]:
    result = {field: list(recorded.get(field, [])) for field in FINDING_FIELDS}
    beliefs = [dict(item) for item in result["beliefs"] if isinstance(item, dict)]
    result["beliefs"] = beliefs
    for belief in beliefs:
        if belief.get("status") not in {"supported", "strongly_supported", "runtime_confirmed"}:
            continue
        unsupported = (
            _unsupported_grounding_literals(
                {
                    "answer": belief.get("statement", ""),
                    "evidence": belief.get("evidence", []),
                },
                _empty_recorded_findings(),
                observations or [],
            )
            if strict_grounding
            else []
        )
        supporting_ids = _supporting_observation_ids(unsupported, observations or [])
        if supporting_ids:
            belief["evidence"] = _dedupe_strings([
                *_reference_list(belief.get("evidence")),
                *supporting_ids,
            ])
            unsupported = _unsupported_grounding_literals(
                {
                    "answer": belief.get("statement", ""),
                    "evidence": belief.get("evidence", []),
                },
                _empty_recorded_findings(),
                observations or [],
            )
        if unsupported:
            belief["status"] = "unverified"
    resolutions = [dict(item) for item in result["resolutions"] if isinstance(item, dict)]
    verification_requests = []
    clearify_questions: dict[str, str] = {}
    for verdict in audit.get("verdicts", []):
        if not isinstance(verdict, dict):
            continue
        unknown_id = str(verdict.get("unknown_id") or "").strip()
        resolution = _find_by_unknown_id(resolutions, unknown_id)
        if (
            resolution is None
            or resolution.get("reason") == CLEARIFY_RESOLUTION_REASON
        ):
            continue
        status = str(verdict.get("status") or "").strip()
        reason = str(verdict.get("reason") or "").strip()
        if status == "grounded":
            unsupported = (
                _unsupported_grounding_literals(
                    resolution,
                    result,
                    observations or [],
                )
                if strict_grounding
                else []
            )
            supporting_ids = _supporting_observation_ids(unsupported, observations or [])
            if supporting_ids:
                resolution["evidence"] = _dedupe_strings([
                    *_reference_list(resolution.get("evidence")),
                    *supporting_ids,
                ])
                unsupported = _unsupported_grounding_literals(
                    resolution,
                    result,
                    observations or [],
                )
            missing_state_writes = (
                _missing_grounding_state_writes(
                    resolution,
                    result,
                    observations or [],
                )
                if strict_grounding
                else []
            )
            if not unsupported and not missing_state_writes:
                resolution["status"] = "resolved"
                if reason:
                    resolution["reason"] = reason
                continue
            status = "investigate"
            if unsupported:
                reason = GROUNDING_LITERAL_REASON_PREFIX + " " + ", ".join(unsupported)
            else:
                reason = STATE_WRITE_REASON_PREFIX + " " + ", ".join(missing_state_writes)
        if status == "verify":
            hypothesis = str(verdict.get("hypothesis") or "").strip()
            if hypothesis and allow_verification:
                resolution["status"] = "partially_resolved"
                resolution["reason"] = reason
                verification_requests.append({
                    "unknown_id": unknown_id,
                    "hypothesis": hypothesis,
                    "reason": reason,
                })
                continue
            status = "investigate"
        if status == "clearify":
            question = str(verdict.get("question") or "").strip()
            if question:
                resolution["status"] = "needs_clearify"
                resolution["reason"] = reason
                clearify_questions[unknown_id] = question
                continue
            status = "investigate"
        resolution["status"] = "partially_resolved"
        resolution["reason"] = reason or "The semantic audit found insufficient evidence."
    result["resolutions"] = resolutions
    return result, verification_requests, clearify_questions


def _resolution_requires_semantic_audit(resolution: dict, initial_unknowns: list[dict]) -> bool:
    if _is_user_product_decision(resolution, initial_unknowns):
        return False
    if str(resolution.get("kind") or "derived_inference") not in SEMANTIC_AUDIT_KINDS:
        return False
    return (
        str(resolution.get("status") or "") in {"resolved", "partially_resolved"}
        and str(resolution.get("answer") or "").strip()
        and str(resolution.get("unknown_id") or "").strip()
    )


def _apply_direct_resolution_gate(
    recorded: dict,
    observations: list[dict],
    *,
    strict_grounding: bool = True,
) -> dict:
    direct_ids = [
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("kind") or "") == "direct_fact"
        and str(item.get("status") or "") == "resolved"
        and str(item.get("unknown_id") or "").strip()
    ]
    if not direct_ids:
        return recorded
    gated, _, _ = _apply_investigation_audit(
        recorded,
        {"verdicts": [
            {
                "unknown_id": unknown_id,
                "status": "grounded",
                "reason": "Direct fact passed deterministic grounding checks.",
            }
            for unknown_id in direct_ids
        ]},
        observations=observations,
        strict_grounding=strict_grounding,
        allow_verification=False,
    )
    return gated


def _unsupported_grounding_literals(
    resolution: dict,
    recorded: dict,
    observations: list[dict],
) -> list[str]:
    evidence_ids = set(_reference_list(resolution.get("evidence")))
    belief_ids = set(_reference_list(resolution.get("belief_ids")))
    for belief in recorded.get("beliefs", []):
        if isinstance(belief, dict) and str(belief.get("id") or "") in belief_ids:
            evidence_ids.update(_reference_list(belief.get("evidence")))
    evidence = "\n".join(
        _grounding_observation_text(item)
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "") in evidence_ids
    )
    normalized_evidence = re.sub(r"\s+", "", evidence)
    literals = _grounding_code_literals(str(resolution.get("answer") or ""))
    return _dedupe_strings([
        literal
        for literal in literals
        if _is_grounding_code_literal(literal)
        and re.sub(r"\s+", "", literal) not in normalized_evidence
    ])


def _semantic_repair_resolution_ids(recorded: dict) -> set[str]:
    return {
        str(item.get("unknown_id") or "")
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and item.get("status") == "partially_resolved"
        and str(item.get("reason") or "").startswith((
            GROUNDING_LITERAL_REASON_PREFIX,
            STATE_WRITE_REASON_PREFIX,
        ))
        and str(item.get("unknown_id") or "")
    }


def _grounding_code_literals(value: str) -> list[str]:
    quoted = [
        literal
        for literal in re.findall(r"`([^`\r\n]+)`", value)
        if len(literal) <= 80
        and "..." not in literal
        and not literal.lstrip().startswith("<")
    ]
    return [
        literal
        for literal in _dedupe_strings([
            *quoted,
            *re.findall(
                r"""\bv-(?:if|show|for|else-if|else|bind|on|model|html|text|slot|pre|cloak|once|memo)"""
                r"""(?:\s*=\s*(?:"[^"\r\n]*"|'[^'\r\n]*'))?""",
                value,
            ),
            *re.findall(
                r"(?<![-A-Za-z0-9_$])[a-z_$][A-Za-z0-9_$]*"
                r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+"
                r"(?![-A-Za-z0-9_$])",
                value,
            ),
        ])
        if not _code_literal_is_negated(value, literal)
    ]


def _code_literal_is_negated(value: str, literal: str) -> bool:
    positions = [match.start() for match in re.finditer(re.escape(literal), value)]
    return bool(positions) and all(
        re.search(
            r"(?:非|无|没有|不存在|\bnot|\bno)\s*[\(（]?\s*$",
            value[max(0, index - 12):index].casefold(),
        )
        for index in positions
    )


def _bind_grounding_evidence(recorded: dict, observations: list[dict]) -> dict:
    for field, text_field in (("beliefs", "statement"), ("resolutions", "answer")):
        for item in recorded.get(field, []):
            if not isinstance(item, dict):
                continue
            supporting_ids = _supporting_observation_ids(
                _grounding_code_literals(str(item.get(text_field) or "")),
                observations,
            )
            if supporting_ids:
                item["evidence"] = _dedupe_strings([
                    *_reference_list(item.get("evidence")),
                    *supporting_ids,
                ])
    return recorded


def _supporting_observation_ids(
    literals: list[str],
    observations: list[dict],
) -> list[str]:
    if not literals:
        return []
    result = []
    for literal in literals:
        normalized = re.sub(r"\s+", "", literal)
        matches = [
            str(item.get("id") or "")
            for item in observations
            if isinstance(item, dict)
            and str(item.get("id") or "")
            and normalized in re.sub(
                r"\s+",
                "",
                _grounding_observation_text(item),
            )
        ]
        if not matches:
            return []
        result.extend(matches)
    return _dedupe_strings(result)


def _grounding_observation_text(observation: dict) -> str:
    return "\n".join(
        text for field in ("path", "_grounding_evidence", "evidence_excerpt", "summary")
        if (text := str(observation.get(field) or "").strip())
    )


def _missing_grounding_state_writes(
    resolution: dict,
    recorded: dict,
    observations: list[dict],
) -> list[str]:
    answer = str(resolution.get("answer") or "")
    state_ids = _assigned_state_ids(answer)
    if not state_ids:
        return []
    evidence_ids = set(_reference_list(resolution.get("evidence")))
    belief_ids = set(_reference_list(resolution.get("belief_ids")))
    for belief in recorded.get("beliefs", []):
        if isinstance(belief, dict) and str(belief.get("id") or "") in belief_ids:
            evidence_ids.update(_reference_list(belief.get("evidence")))
    evidence = "\n".join(
        _grounding_observation_text(item)
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "") in evidence_ids
    )
    normalized_answer = re.sub(r"\s+", "", answer)
    writes = []
    for state_id in state_ids:
        writes.extend(re.findall(
            re.escape(state_id)
            + r"\s*(?:(?:\?\?=|&&=|\|\|=|\+=|-=|\*=|/=|%=|=)"
            + r"\s*[^;\r\n}]+|\+\+|--)",
            evidence,
        ))
    return _dedupe_strings([
        write.strip()
        for write in writes
        if re.sub(r"\s+", "", write) not in normalized_answer
    ])


def _assigned_state_ids(value: str) -> list[str]:
    return _dedupe_strings([
        match.group(1)
        for match in re.finditer(
            r"(?<![@:A-Za-z0-9_$-])"
            r"([a-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+)"
            r"\s*(?=\?\?=|&&=|\|\|=|\+=|-=|\*=|/=|%=|=(?!=)|\+\+|--)",
            value,
        )
    ])


def _recorded_findings_signature(recorded: dict) -> str:
    return json.dumps(
        {
            field: recorded.get(field, [])
            for field in FINDING_FIELDS
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _final_observations(
    observations: list[dict],
    *,
    preserve_grounding_evidence: bool,
) -> list[dict]:
    if preserve_grounding_evidence:
        return [dict(item) for item in observations]
    return [
        {key: value for key, value in item.items() if key != "_grounding_evidence"}
        for item in observations
    ]


def _is_grounding_code_literal(value: str) -> bool:
    if value.casefold().endswith((
        ".cfg", ".css", ".html", ".ini", ".js", ".json", ".jsx", ".lock",
        ".md", ".py", ".toml", ".ts", ".tsx", ".txt", ".vue", ".yaml", ".yml",
    )):
        return False
    return value.startswith(("v-if", "v-show", "@")) or any(
        marker in value
        for marker in ("=", "<", ">", "(", ")", "{", "}", "[", "]", "/", "\\")
    ) or bool(
        re.fullmatch(
            r"[a-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+",
            value,
        )
    )


def _audit_covers_resolutions(
    audit: dict,
    recorded: dict,
    analysis: dict | None = None,
) -> bool:
    initial_unknowns = _initial_unknowns(analysis)
    resolved_ids = [
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and _resolution_requires_semantic_audit(item, initial_unknowns)
    ]
    audited_ids = [
        str(item.get("unknown_id") or "").strip()
        for item in audit.get("verdicts", [])
        if isinstance(item, dict) and str(item.get("unknown_id") or "").strip()
    ]
    return all(any(_same_unknown_id(resolved_id, audited_id) for audited_id in audited_ids) for resolved_id in resolved_ids)


def _merge_recorded_findings(current: dict, update: dict) -> dict:
    merged = {field: list(current.get(field, [])) for field in FINDING_FIELDS}
    for field in FINDING_FIELDS:
        value = update.get(field)
        if isinstance(value, list):
            merged[field] = _merge_list_by_identity(merged[field], value)
    return merged


def _merge_list_by_identity(left: list, right: list) -> list:
    result = list(left)
    positions: dict[str, int] = {}
    for index, item in enumerate(result):
        key = _identity_key(item)
        if key:
            positions[key] = index
    for item in right:
        key = _identity_key(item)
        if key and key in positions:
            existing = result[positions[key]]
            if isinstance(existing, dict) and isinstance(item, dict):
                if existing.get("reason") == CLEARIFY_RESOLUTION_REASON:
                    result[positions[key]] = {**item, **existing}
                else:
                    result[positions[key]] = {**existing, **item}
            else:
                result[positions[key]] = item
        else:
            if key:
                positions[key] = len(result)
            result.append(item)
    return result


def _identity_key(item) -> str:
    if not isinstance(item, dict):
        return str(item)
    for field in ("unknown_id", "id", "text", "statement", "question"):
        value = str(item.get(field) or "").strip()
        if field == "unknown_id":
            value = _normalize_unknown_id(value)
        if value:
            return f"{field}:{value}"
    return ""


def _record_task_updates(arguments: dict) -> list[dict]:
    return _investigation_task_updates(
        arguments.get("task_updates"),
        _unknowns(arguments.get("unknowns")) + _unknowns(arguments.get("new_unknowns")),
        _resolutions(arguments.get("resolutions")),
    )


def _finish_arguments(
    recorded: dict,
    finish: dict,
    *,
    prefer_finish_summary: bool = False,
) -> dict:
    facts = _runtime_patch_facts([], recorded)
    resolution_summary = "\n\n".join(
        str(item.get("answer") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and item.get("status") == "resolved"
        and str(item.get("answer") or "").strip()
    )
    finish_summary = str(finish.get("summary") or "").strip()
    if (
        prefer_finish_summary
        and finish_summary
        and resolution_summary
        and len(finish_summary)
        < len(resolution_summary) * READ_ONLY_SUMMARY_MIN_RESOLUTION_RATIO
    ):
        raise ValueError(
            "read_only finish summary drops too much audited resolution detail; "
            "rewrite the final deliverable to satisfy every acceptance criterion"
        )
    summary = (
        finish_summary
        if prefer_finish_summary and finish_summary
        else resolution_summary or finish_summary
    )
    combined: dict = {
        field: list(recorded.get(field, []))
        for field in FINDING_FIELDS
    }
    combined.update({
        "summary": summary,
        "ready_for_patch_planning": _recommended_next_step(finish) == "patch_planning",
        "recommended_next_step": _recommended_next_step(finish),
        "patch_planning_facts": facts,
        "patch_planning_context": facts,
    })
    return combined


def _recommended_next_step(finish: dict) -> str:
    value = str(finish.get("recommended_next_step") or "").strip()
    if value in {LEGACY_ASK_USER_STRATEGY, "clearify"}:
        return "continue_investigation"
    return value if value in {"patch_planning", "continue_investigation", "done"} else "done"


def _clean_questions(value: list) -> list[str]:
    return [text for item in value if (text := str(item).strip())]


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for raw in value if (item := str(raw).strip())]


def _reference_list(value) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return _string_list(value)


def _belief_status(raw: dict, default: str = "unverified") -> str:
    allowed = {
        "unverified",
        "plausible",
        "supported",
        "strongly_supported",
        "runtime_confirmed",
        "contradicted",
        "invalidated",
    }
    status = str(raw.get("status") or "").strip()
    if status in allowed:
        return status
    confidence = str(raw.get("confidence") or raw.get("certainty") or "").strip().casefold()
    mapped = {
        "certain": "strongly_supported",
        "high": "strongly_supported",
        "likely": "supported",
        "medium": "supported",
        "uncertain": "plausible",
        "low": "plausible",
    }.get(confidence)
    if mapped:
        return mapped
    return default if default in allowed else "unverified"


def _beliefs(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            continue
        statement = _belief_text(raw)
        evidence = _reference_list(raw.get("evidence"))
        status = _belief_status(raw, default="supported" if evidence else "unverified")
        if not statement:
            continue
        items.append({
            "id": str(raw.get("id") or f"B{index}").strip(),
            "statement": statement,
            "status": status,
            "evidence": evidence,
        })
    return items


def _resolutions(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        unknown_id = str(raw.get("unknown_id") or raw.get("id") or "").strip()
        status = str(raw.get("status") or "").strip()
        if status == LEGACY_NEEDS_USER_STATUS:
            status = "needs_clearify"
        if status not in {"resolved", "partially_resolved", "needs_clearify", "deferred"}:
            continue
        if not unknown_id:
            continue
        items.append({
            "unknown_id": unknown_id,
            "status": status,
            "kind": _resolution_kind(raw, status),
            "answer": str(raw.get("answer") or "").strip(),
            "evidence": _string_list(raw.get("evidence")),
            "belief_ids": _string_list(raw.get("belief_ids")),
            "reason": str(raw.get("reason") or "").strip(),
        })
    return items


def _validate_resolution_refs(resolutions: list[dict], beliefs: list[dict], observations: list[dict]) -> None:
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    belief_by_id = {
        str(item.get("id") or "").strip(): item
        for item in beliefs
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    usable_belief_status = {"plausible", "supported", "strongly_supported", "runtime_confirmed"}
    for resolution in resolutions:
        missing_evidence = [item for item in resolution.get("evidence", []) if item not in evidence_ids]
        if missing_evidence:
            raise ValueError(
                f"resolution {resolution['unknown_id']} references unknown evidence ids: "
                + ", ".join(missing_evidence)
            )
        missing_beliefs = [item for item in resolution.get("belief_ids", []) if item not in belief_by_id]
        if missing_beliefs:
            raise ValueError(
                f"resolution {resolution['unknown_id']} references unknown belief ids: "
                + ", ".join(missing_beliefs)
            )
        weak_beliefs = [
            item for item in resolution.get("belief_ids", [])
            if belief_by_id[item].get("status") not in usable_belief_status
        ]
        if weak_beliefs:
            raise ValueError(
                f"resolution {resolution['unknown_id']} references unsupported belief ids: "
                + ", ".join(weak_beliefs)
            )


def _validate_belief_refs(beliefs: list[dict], observations: list[dict]) -> None:
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    for belief in beliefs:
        missing = [item for item in belief.get("evidence", []) if item not in evidence_ids]
        if missing:
            raise ValueError(
                f"belief {belief['id']} references unknown evidence ids: "
                + ", ".join(missing)
            )


def _drop_invalid_belief_refs(
    beliefs: list[dict],
    observations: list[dict],
    repairs: list[str],
) -> list[dict]:
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    changed = False
    for belief in beliefs:
        evidence = [item for item in belief.get("evidence", []) if item in evidence_ids]
        if evidence != belief.get("evidence", []):
            changed = True
            belief["evidence"] = evidence
            if not evidence:
                belief["status"] = "unverified"
    if changed:
        repairs.append("Dropped invalid belief evidence references during finalization repair")
    return beliefs


def _drop_invalid_resolution_refs(
    resolutions: list[dict],
    beliefs: list[dict],
    observations: list[dict],
    repairs: list[str],
) -> list[dict]:
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    usable_beliefs = {
        str(item.get("id") or "").strip()
        for item in beliefs
        if isinstance(item, dict)
        and str(item.get("id") or "").strip()
        and item.get("status") in {"plausible", "supported", "strongly_supported", "runtime_confirmed"}
    }
    changed = False
    for resolution in resolutions:
        evidence = [item for item in resolution.get("evidence", []) if item in evidence_ids]
        beliefs = [item for item in resolution.get("belief_ids", []) if item in usable_beliefs]
        changed = changed or evidence != resolution.get("evidence", []) or beliefs != resolution.get("belief_ids", [])
        resolution["evidence"] = evidence
        resolution["belief_ids"] = beliefs
    if changed:
        repairs.append("Dropped invalid resolution references during finalization repair")
    return resolutions


def _runtime_readiness(
    *,
    model_ready: bool,
    analysis: dict | None,
    initial_unknowns: list[dict],
    resolutions: list[dict],
    unknowns: list[dict],
    patch_context: list[str],
    finish_arguments: dict | None = None,
) -> dict:
    blockers = [item for item in unknowns if item.get("blocking")]
    reasons = []
    if blockers:
        reasons.append("blocking_unknowns_remain")
    for item in initial_unknowns:
        if not item.get("blocking"):
            continue
        resolution = _find_by_unknown_id(resolutions, item["id"])
        if not resolution or resolution.get("status") != "resolved":
            if (
                resolution
                and item.get("resolution_strategy") == "investigate_project"
                and resolution.get("answer")
                and not (resolution.get("evidence") or resolution.get("belief_ids"))
            ):
                reasons.append(f"{item['id']}:missing_evidence")
            else:
                reasons.append(f"{item['id']}:not_resolved")
            continue
        if item.get("resolution_strategy") == "investigate_project" and not (
            resolution.get("evidence") or resolution.get("belief_ids")
        ) and not _is_user_product_decision(resolution, initial_unknowns):
            reasons.append(f"{item['id']}:missing_evidence")
    if isinstance(analysis, dict) and analysis.get("acceptance_criteria") and not patch_context:
        reasons.append("missing_patch_planning_facts")
    if _requires_bugfix_readiness(analysis, model_ready):
        reasons.extend(_bugfix_readiness_reasons(finish_arguments or {}))
    ready = not reasons
    return {
        "ready": ready,
        "model_ready": model_ready,
        "reasons": reasons,
    }


def _requires_bugfix_readiness(analysis: dict | None, model_ready: bool) -> bool:
    if not model_ready or not isinstance(analysis, dict):
        return False
    intent = analysis.get("intent") if isinstance(analysis.get("intent"), dict) else {}
    return (
        str(intent.get("type") or "").strip() == "bugfix"
        and analysis.get("execution_mode") == "implement"
    )


def _bugfix_readiness_reasons(arguments: dict) -> list[str]:
    readiness = arguments.get("bugfix_readiness")
    if not isinstance(readiness, dict):
        return ["bugfix_readiness:missing"]
    fields = [
        "failure_reproduced_or_observed",
        "root_cause_or_failing_boundary_identified",
        "patch_target_identified",
        "expected_behavior_change_defined",
        "validation_scenario_defined",
    ]
    return [
        f"bugfix_readiness:{field}"
        for field in fields
        if readiness.get(field) is not True
    ]


def _initial_unknowns(analysis: dict | None) -> list[dict]:
    if not isinstance(analysis, dict):
        return []
    value = analysis.get("unknowns")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        return []
    return _unknowns(value)


def _complete_resolutions(resolutions: list[dict], initial_unknowns: list[dict], unknowns: list[dict]) -> list[dict]:
    for resolution in resolutions:
        source = _find_by_unknown_id(initial_unknowns, resolution["unknown_id"], id_field="id")
        if (
            resolution.get("status") == "needs_clearify"
            and source
            and source.get("type") != "product_decision"
        ):
            grounded = bool(
                resolution.get("answer")
                and (resolution.get("evidence") or resolution.get("belief_ids"))
            )
            resolution["status"] = "resolved" if grounded else "partially_resolved"
            resolution["reason"] = (
                "Only product decisions may require user clarification; "
                "this project fact is grounded." if grounded else
                "Only product decisions may require user clarification."
            )
        if (
            resolution.get("status") == "deferred"
            and source
            and source.get("blocking")
            and source.get("resolution_strategy") != "deferred"
        ):
            resolution["status"] = "partially_resolved"
            resolution["reason"] = (
                "A blocking task-contract unknown cannot be deferred without resolving it."
            )
    unresolved_ids = [item["id"] for item in unknowns]
    for item in initial_unknowns:
        if _find_by_unknown_id(resolutions, item["id"]):
            continue
        status = "partially_resolved"
        if item.get("resolution_strategy") == "clearify":
            status = "needs_clearify"
        elif item.get("resolution_strategy") == "deferred" or not item.get("blocking"):
            status = "deferred"
        if any(_same_unknown_id(item["id"], unknown_id) for unknown_id in unresolved_ids) or item.get("blocking"):
            resolutions.append({
                "unknown_id": item["id"],
                "status": status,
                "answer": "",
                "evidence": [],
                "belief_ids": [],
                "reason": "No explicit resolution was supplied for this task-contract unknown.",
            })
    return resolutions


def _enforce_resolution_evidence(resolutions: list[dict], initial_unknowns: list[dict], *, strict: bool = True) -> list[dict]:
    if not strict:
        return resolutions
    for resolution in resolutions:
        source = _find_by_unknown_id(initial_unknowns, resolution["unknown_id"], id_field="id")
        if not source:
            continue
        if (
            source.get("blocking")
            and source.get("resolution_strategy") == "investigate_project"
            and resolution.get("status") == "resolved"
            and not (resolution.get("evidence") or resolution.get("belief_ids"))
            and not _is_user_product_decision(resolution, initial_unknowns)
        ):
            resolution["status"] = "partially_resolved"
            resolution["reason"] = resolution.get("reason") or "Resolved codebase facts require evidence or belief references."
    return resolutions


def _is_user_product_decision(
    resolution: dict,
    unknowns: list[dict],
) -> bool:
    if resolution.get("reason") != CLEARIFY_RESOLUTION_REASON:
        return False
    unknown_id = str(resolution.get("unknown_id") or "").strip()
    return any(
        _same_unknown_id(item.get("id"), unknown_id)
        and item.get("type") == "product_decision"
        for item in unknowns
        if isinstance(item, dict)
    )


def _unresolved_from_resolutions(resolutions: list[dict], initial_unknowns: list[dict]) -> list[dict]:
    unresolved = []
    for resolution in resolutions:
        if resolution["status"] == "resolved":
            continue
        source = _find_by_unknown_id(initial_unknowns, resolution["unknown_id"], id_field="id") or {}
        strategy = "investigate_project"
        if resolution["status"] == "needs_clearify":
            strategy = "clearify"
        elif resolution["status"] == "deferred":
            strategy = "deferred"
        question = source.get("question") or _question_from_resolution(resolution)
        unresolved.append({
            "id": resolution["unknown_id"],
            "question": question,
            "blocking": resolution["status"] in {"partially_resolved", "needs_clearify"} and bool(source.get("blocking", True)),
            "resolution_strategy": strategy,
        })
    return unresolved


def _drop_resolved_unknowns(unknowns: list[dict], resolutions: list[dict], repairs: list[str]) -> list[dict]:
    resolved_ids = [
        str(item.get("unknown_id") or "").strip()
        for item in resolutions
        if isinstance(item, dict) and item.get("status") == "resolved"
    ]
    if not resolved_ids:
        return unknowns
    filtered = [
        item for item in unknowns
        if not any(_same_unknown_id(item.get("id"), resolved_id) for resolved_id in resolved_ids)
    ]
    if len(filtered) != len(unknowns):
        repairs.append("Removed unknowns already resolved by resolutions")
    return filtered


def _question_from_resolution(resolution: dict) -> str:
    text = str(resolution.get("answer") or resolution.get("reason") or "").strip()
    if _looks_like_question(text):
        return text
    return "请明确这个实现决策？"


def _decision_question(value: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if _looks_like_question(text):
        return text
    return f"请明确：{text}？"


def _looks_like_question(value: str) -> bool:
    text = str(value or "")
    return "?" in text or "？" in text

def _merge_unknowns(items: list[dict]) -> list[dict]:
    merged = {}
    by_question = {}
    for item in items:
        if not item.get("id") or not item.get("question"):
            continue
        key = _question_key(item["question"])
        existing_id = by_question.get(key)
        if existing_id:
            current = merged[existing_id]
            merged[existing_id] = {
                **current,
                **item,
                "id": existing_id,
                "blocking": bool(current.get("blocking") or item.get("blocking")),
            }
            continue
        merged[item["id"]] = item
        by_question[key] = item["id"]
    return list(merged.values())


def _unknown_id_for_question(question: str, unknowns: list[dict]) -> str:
    key = _question_key(question)
    for item in unknowns:
        if item.get("id") and _question_key(item.get("question", "")) == key:
            return item["id"]
    return ""


def _question_key(value: str) -> str:
    return re.sub(r"\W+", "", str(value or "")).casefold()


def _unknown_id_tail(value: str | None) -> str:
    text = str(value or "").strip()
    return text.rsplit(":", 1)[-1] if ":" in text else text


def _same_unknown_id(left: str | None, right: str | None) -> bool:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text or not right_text:
        return False
    return left_text == right_text or _unknown_id_tail(left_text) == _unknown_id_tail(right_text)


def _find_by_unknown_id(items: list[dict], unknown_id: str | None, *, id_field: str = "unknown_id") -> dict | None:
    return next(
        (
            item for item in items
            if isinstance(item, dict) and _same_unknown_id(item.get(id_field), unknown_id)
        ),
        None,
    )


def _is_placeholder_question(value: str | None, unknown_id: str | None = "") -> bool:
    text = " ".join(str(value or "").split())
    if not text:
        return True
    if unknown_id and _same_unknown_id(text, unknown_id):
        return True
    placeholder = "\u8bf7\u660e\u786e\u8fd9\u4e2a\u5b9e\u73b0\u51b3\u7b56"
    return text.startswith(placeholder)


def _task_update_question(final: dict, unknown_id: str | None) -> str:
    if not unknown_id:
        return ""
    for item in final.get("task_updates") or []:
        if not isinstance(item, dict) or not _same_unknown_id(item.get("id"), unknown_id):
            continue
        text = str(item.get("text") or "").strip()
        if not _is_placeholder_question(text, unknown_id):
            return text
    return ""


def _display_question_for_unknown(item: dict | None, final: dict) -> str:
    if not item:
        return ""
    unknown_id = str(item.get("id") or "").strip()
    question = str(item.get("question") or "").strip()
    if not _is_placeholder_question(question, unknown_id):
        return question
    task_question = _task_update_question(final, unknown_id)
    return task_question or question


def _best_clearify_unknown(final: dict) -> dict | None:
    candidates = [
        item for item in final.get("unknowns", [])
        if isinstance(item, dict)
        and item.get("blocking")
        and item.get("resolution_strategy") == "clearify"
    ]
    if not candidates:
        return None
    specific = [
        item for item in candidates
        if not _is_placeholder_question(_display_question_for_unknown(item, final), item.get("id"))
    ]
    return specific[0] if specific else candidates[0]


def _patch_context(value, repairs: list[str], repair_conflicts: bool) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        if repair_conflicts:
            repairs.append("Dropped non-array patch planning facts")
            return []
        raise ValueError("patch_planning_context must be an array of strings")
    items = []
    normalized_object = False
    for raw in value:
        if isinstance(raw, dict):
            if not repair_conflicts:
                raise ValueError("patch_planning_context must be an array of strings")
            fact = str(raw.get("fact") or raw.get("text") or raw.get("statement") or "").strip()
            source = str(raw.get("source") or raw.get("evidence") or "").strip()
            if fact:
                items.append(f"{fact} ({source})" if source else fact)
                normalized_object = True
        elif text := str(raw).strip():
            items.append(text)
    if normalized_object:
        repairs.append("Normalized patch_planning_context objects to strings")
    return items


def _require_items_accounted(required_items, task_updates, resolutions, repair_conflicts: bool) -> None:
    if not required_items:
        return
    update_ids = {
        str(item.get("id") or "").strip()
        for item in task_updates or []
        if isinstance(item, dict) and str(item.get("status") or "") in {"known", "deferred", "blocked"}
    }
    resolution_ids = [item["unknown_id"] for item in resolutions]
    missing = [
        str(item.get("id") or "").strip()
        for item in required_items
        if isinstance(item, dict)
        and not any(_same_unknown_id(item.get("id"), known_id) for known_id in [*update_ids, *resolution_ids])
    ]
    if missing and not repair_conflicts:
        raise ValueError("finish_investigation must account for every initial hypothesis/unknown")


def _resolve_task_update_conflicts(
    unknowns: list[dict],
    task_updates,
    repairs: list[str],
    repair_conflicts: bool,
) -> list[dict]:
    known_ids = {
        str(item.get("id") or "").strip()
        for item in task_updates or []
        if isinstance(item, dict) and str(item.get("status") or "").strip() == "known"
    }
    conflicts = [
        item for item in unknowns
        if any(_same_unknown_id(item.get("id"), known_id) for known_id in known_ids)
    ]
    if not conflicts:
        return unknowns
    if not repair_conflicts:
        raise ValueError("unknowns should contain only unresolved items")
    repairs.append("Removed unknowns already marked known by task_updates")
    return [
        item for item in unknowns
        if not any(_same_unknown_id(item.get("id"), known_id) for known_id in known_ids)
    ]


def _investigation_task_updates(value, unknowns: list[dict], resolutions: list[dict] | None = None) -> list[dict]:
    updates = []
    resolved_ids = [
        str(item.get("unknown_id") or "").strip()
        for item in resolutions or []
        if isinstance(item, dict) and item.get("status") == "resolved"
    ]
    if isinstance(value, list):
        for raw in value:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or "").strip()
            status = str(raw.get("status") or "").strip()
            if not text or status not in {"unknown", "known", "deferred", "blocked", "added", "updated"}:
                continue
            item_id = str(raw.get("id") or "").strip()
            if status == "known" and not any(
                _same_unknown_id(item_id, resolved_id)
                for resolved_id in resolved_ids
            ):
                status = "unknown"
            trace = raw.get("trace") if isinstance(raw.get("trace"), list) else []
            updates.append({
                "id": item_id,
                "kind": str(raw.get("kind") or "unknown").strip() or "unknown",
                "text": text,
                "status": status,
                "reason": str(raw.get("reason") or "").strip(),
                "trace": [str(item).strip() for item in trace if str(item).strip()][:6],
            })
    known_ids = {item["id"] for item in updates if item["status"] == "known" and item.get("id")}
    for resolution in resolutions or []:
        unknown_id = resolution.get("unknown_id", "")
        if not unknown_id or any(_same_unknown_id(unknown_id, known_id) for known_id in known_ids):
            continue
        source = _find_by_unknown_id(unknowns, unknown_id, id_field="id")
        text = (source or {}).get("question") or resolution.get("answer") or unknown_id
        resolution_status = resolution.get("status")
        status = {
            "resolved": "known",
            "partially_resolved": "unknown",
            "needs_clearify": "blocked",
            "deferred": "deferred",
        }.get(resolution_status, "unknown")
        evidence = resolution.get("evidence") if isinstance(resolution.get("evidence"), list) else []
        trace = evidence or resolution.get("belief_ids", [])
        updates.append({
            "id": unknown_id,
            "target_id": unknown_id,
            "kind": "unknown",
            "text": text,
            "status": status,
            "reason": resolution.get("reason", ""),
            "trace": trace[:6],
            "answers": [{
                "source": "investigation",
                "text": resolution.get("answer") or unknown_id,
                "reason": resolution.get("reason", ""),
                "trace": trace[:6],
            }] if resolution.get("answer") else [],
        })
        if status == "known":
            known_ids.add(unknown_id)
    for item in unknowns:
        existing_ids = {update.get("id") for update in updates if update.get("id")}
        if any(_same_unknown_id(item.get("id"), existing_id) for existing_id in existing_ids):
            continue
        updates.append({
            "id": item.get("id", ""),
            "kind": "unknown",
            "text": item["question"],
            "status": _unknown_task_status(item),
            "reason": item.get("resolution_strategy", ""),
            "trace": [],
        })
    return updates[:8]


def _step_result(final: dict, *, implementation_intent: bool = True) -> dict:
    blockers = [item for item in final.get("unknowns", []) if item.get("blocking")]
    investigate = [item for item in blockers if item.get("resolution_strategy") == "investigate_project"]
    clearify = [item for item in blockers if item.get("resolution_strategy") == "clearify"]
    unresolved_ids = [item["id"] for item in blockers if item.get("id")]
    if final.get("runtime_failure") and not blockers:
        return {
            "next_step": "failed",
            "continue_reason": final.get("summary") or "Investigation failed before producing a valid final result.",
            "target_unknown_ids": unresolved_ids,
            "unresolved_unknown_ids": unresolved_ids,
            "summary": "",
            "beliefs": [],
            "ready_for_patch_planning": False,
            "patch_planning_context": [],
            "resolutions": [],
            "unknowns": final.get("unknowns", []),
        }
    if investigate:
        return {
            "next_step": "continue_investigation",
            "continue_reason": "; ".join(item["question"] for item in investigate[:3]),
            "target_unknown_ids": [item["id"] for item in investigate],
            "unresolved_unknown_ids": unresolved_ids,
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": False,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": final.get("unknowns", []),
        }
    if clearify:
        item = _best_clearify_unknown(final) or clearify[0]
        question = _display_question_for_unknown(item, final)
        if _is_placeholder_question(question, item.get("id")):
            question = next(
                (
                    str(candidate.get("question") or "").strip()
                    for candidate in clearify
                    if not _is_placeholder_question(candidate.get("question"), candidate.get("id"))
                ),
                question,
            )
        return {
            "next_step": "continue_investigation",
            "continue_reason": question,
            "target_unknown_ids": [item["id"]],
            "unresolved_unknown_ids": unresolved_ids,
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": False,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": final.get("unknowns", []),
        }
    if final.get("ready_for_patch_planning") and implementation_intent:
        return {
            "next_step": "write_code",
            "continue_reason": final.get("summary") or app_settings.text("ready_patch"),
            "target_unknown_ids": [],
            "unresolved_unknown_ids": [],
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": True,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": [],
        }
    if final.get("ready_for_patch_planning") and not implementation_intent:
        return {
            "next_step": "done",
            "continue_reason": final.get("summary") or "Investigation complete.",
            "target_unknown_ids": [],
            "unresolved_unknown_ids": [],
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": False,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": [],
        }
    readiness = final.get("readiness")
    readiness_reasons = [
        str(reason)
        for reason in (readiness.get("reasons", []) if isinstance(readiness, dict) else [])
        if str(reason).strip()
    ]
    if implementation_intent and (
        readiness_reasons
        or str(final.get("recommended_next_step") or "").strip() == "patch_planning"
    ):
        unresolved = readiness_reasons or ["patch_planning_not_ready"]
        return {
            "next_step": "continue_investigation",
            "continue_reason": "Patch planning readiness is incomplete: " + "; ".join(unresolved[:3]),
            "target_unknown_ids": unresolved,
            "unresolved_unknown_ids": unresolved,
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": False,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": final.get("unknowns", []),
        }
    open_questions = final.get("open_questions") or []
    question = str(open_questions[0]) if open_questions else ""
    return {
        "next_step": "continue_investigation" if open_questions else "done",
        "continue_reason": question
        or final.get("summary")
        or "Investigation complete.",
        "target_unknown_ids": [],
        "unresolved_unknown_ids": unresolved_ids,
        "summary": final.get("summary", "") if open_questions else "",
        "beliefs": final.get("beliefs", []),
        "ready_for_patch_planning": False,
        "patch_planning_context": final.get("patch_planning_context", []),
        "resolutions": final.get("resolutions", []),
        "unknowns": final.get("unknowns", []),
    }


def _unknowns(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    try:
        return _contract_unknowns(value)
    except ValueError:
        return []


def _summary(final: dict) -> str:
    lines = [final.get("summary") or "Investigation complete."]
    if final.get("open_questions"):
        lines.append(f"\n{app_settings.text('summary_open_questions')}")
        lines.extend(f"- {item}" for item in final["open_questions"][:5])
    return "\n".join(lines)
