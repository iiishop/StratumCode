from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
from collections.abc import Iterator
from enum import Enum, StrEnum
from pathlib import Path
from uuid import uuid4

from .. import app_settings, clearify_runtime, model_settings, prompt, providers, skill_runtime
from ..agent_runtime import (
    add_usage as _add_usage,
    call_model as _call_model,
    assistant_message as _assistant_message,
    assistant_visible_text as _assistant_visible_text,
    content_text as _content_text,
    empty_usage as _empty_usage,
    execute_skill_tool_call,
    start_event,
    usage_delta as _usage_delta,
)
from ..json2slots import JSONValue, json2slots
from ..status.task_contract import LEGACY_ASK_USER_STRATEGY
from ..status.task_analysis import _analysis_requests_implementation
from ..status.task_updates import _unknown_task_status
from ..tools import registry
from .constants import (
    CLEARIFY_RESOLUTION_REASON,
    CLEARIFY_UNRESOLVED_REASON,
    DISCOVERY_CONTRACT_FIELDS,
    FINDING_FIELDS,
    GROUNDING_LITERAL_REASON_PREFIX,
    INVESTIGATION_CAPABILITY,
    MAX_DUPLICATE_NO_PROGRESS,
    MAX_PENDING_DISCOVERY_OBSERVATIONS,
    MAX_REPEATED_RECORD_NO_PROGRESS,
    MAX_REPEATED_TOOL_ERRORS,
    READ_ONLY_SUMMARY_MIN_RESOLUTION_RATIO,
    RECORD_RECOVERY_REASON,
    REQUIRED_AUDIT_ATTEMPTS,
    SEMANTIC_AUDIT_KINDS,
    STATE_WRITE_REASON_PREFIX,
    _REPAIR_ALLOWED_TOOL_NAMES,
    _RUNTIME_EVIDENCE_RE,
)
from .domain import (
    _analysis_is_read_only,
    _belief_status,
    _belief_text,
    _beliefs,
    _clearify_resolution_lacks_evidence,
    _observation_context_view,
    _observation_reference_map,
    _observation_refs,
    _recorded_resolves_initial_unknowns,
    _reference_list,
    _semantic_missing_items,
    _semantic_repair_payload,
)
from .directive import _investigation_directive
from .evidence import (
    PROJECT_EVIDENCE_TOOLS,
    _alias_beliefs,
    _alias_statement,
    _bind_grounding_evidence,
    _canonical_evidence_id,
    _code_literal_is_negated,
    _drop_invalid_belief_refs,
    _enforce_resolution_evidence,
    _file_ref_matches,
    _grounding_code_literals,
    _grounding_unsupported_for_resolution,
    _infer_workspace_root,
    _is_framework_module,
    _is_grounding_code_literal,
    _is_python_stdlib_module,
    _is_user_product_decision,
    _normalize_evidence_refs,
    _observation_covers_module,
    _observation_ref_by_id,
    _positive_project_observation,
    _ref_is_existential,
    _require_file_reads,
    _require_lsp_definition_reads,
    _resolution_evidence_lines,
    _resolution_grounding_evidence_spans,
    _resolution_is_absence_claim,
    _supporting_belief,
    _supporting_observation_ids,
    _unsupported_grounding_literals,
    _validate_belief_refs,
)
from .findings import (
    _append_resolution_repair,
    _assigned_state_ids,
    _belief_identity_key,
    _continued_recorded_findings,
    _empty_recorded_findings,
    _grounding_observation_text,
    _has_finding_fields,
    _identity_key,
    _merge_belief,
    _merge_beliefs_by_identity,
    _merge_list_by_identity,
    _merge_recorded_findings,
    _missing_grounding_state_writes,
    _normalize_record_slot_answer,
    _normalize_statement,
    _nothing_to_record_result,
    _record_findings_by_slots,
    _record_resolution_slot_ids,
    _record_slot_context,
    _record_slot_contract,
    _record_slot_prompt,
    _record_slot_relevant_findings,
    _record_slot_relevant_observations,
    _record_slot_template,
    _recorded_findings_signature,
    _reject_empty_repair,
    _remap_resolution_belief_ids,
    _require_finding_fields,
    _required_resolution_slot,
    _required_state_write_literals,
    _resolution_kind,
    _resolutions,
    _runtime_new_unknowns,
    _runtime_slot_beliefs,
    _runtime_slot_resolutions,
    _semantic_repair_observation_ids,
    _supersede_resolutions_with_clearify,
    _valid_record_slot_value,
)
from .util import (
    _dedupe_strings,
    _read_path_norm,
    _round_indexes,
    _skip_ws,
    _string_list,
)
from .ids import (
    _canonicalize_resolution_unknown_ids,
    _find_by_unknown_id,
    _initial_unknowns,
    _merge_unknowns,
    _normalize_unknown_id,
    _question_key,
    _same_unknown_id,
    _target_unknown_ids,
    _unknown_id_tail,
    _unknowns,
)
from .tools import (
    _duplicate_no_progress_json,
    _extract_discovery_contract,  # noqa: F401
    _finish_tool_schema,
    _investigation_tool_schema,
    _missing_fields_from_error,  # noqa: F401
    _named,  # noqa: F401
    _phase_tool_choice,
    _phase_tools,
    _record_findings_tool_schema,
    _reject_batched_hypothesis,  # noqa: F401
    _resolve_unknowns_tool_schema,
    _runtime_failure_blocks_continue,  # noqa: F401
    _run_tool_stream,
    _short_observation,  # noqa: F401
    _step_result,
    _tool_arguments,
    _tool_blocked_error_json,
    _tool_cache_key,
    _tool_call_subject,
    _tool_event,
    _tool_message,
    _tool_observation,
    _tool_repair_error_json,
    _validate_tool_contract,
)
from .state import InvestigationPhase, InvestigationRuntime, InvestigationState, UsageState


class DispatchAction(StrEnum):
    """Local control signal for the tool dispatch layer; not cross-round state."""
    CONTINUE_TOOLS = "continue_tools"
    BREAK_TOOLS = "break_tools"
    NEXT_ROUND = "next_round"
    TERMINATE = "terminate"


class FinishDecision(Enum):
    """Finish gate decision before applying the corresponding transition."""
    ACCEPT = "accept"
    REPAIR = "repair"
    VERIFY = "verify"
    CLEARIFY = "clearify"


def _get_investigation_phase(state: InvestigationState) -> InvestigationPhase:
    """Derive the current phase from state without persisting a field."""
    if _semantic_repair_resolution_ids(state.findings.recorded):
        return InvestigationPhase.REPAIR
    return InvestigationPhase.DISCOVER


def _bump_hardlock(state: InvestigationState) -> None:
    """Increment the already-resolved error counter; >=2 hard-locks finish_investigation."""
    state.progress.already_resolved_error_count += 1
    if state.progress.already_resolved_error_count >= 2:
        # Hard-lock: force the model to call finish_investigation
        state.control.current_tool_choice = {"type": "function", "function": {"name": "finish_investigation"}}


def _prepare_round(
    state: InvestigationState,
    runtime: InvestigationRuntime,
    *,
    current_round_index: int,
    previous_rounds_usage: dict,
) -> tuple[str | None, InvestigationPhase, list[dict], set[str], list[str], dict | None, dict | None, set[str]]:
    del previous_rounds_usage

    current_tools = runtime.tools
    state.control.current_tool_choice = "required"
    clearify_unknown = _pending_clearify_unknown(
        state.findings.recorded,
        runtime.analysis,
        state.verification.clearify_questions,
    )
    verification_request = state.verification.queue[0] if state.verification.queue else None
    semantic_repair_required_ids = (
        _semantic_repair_resolution_ids(state.findings.recorded)
        if runtime.semantic_gate_enabled
        else set()
    )
    resolution_required_ids = _unknowns_needing_resolution(state.findings.recorded, state.observations.items, runtime.analysis)
    resolution_required_ids = _dedupe_strings([
        *resolution_required_ids,
        *sorted(semantic_repair_required_ids),
        *(
            _pending_observation_unknown_ids(
                state.observations.items,
                state.observations.pending_ids,
                runtime.analysis,
                state.findings.recorded,
            )
            if len(state.observations.pending_ids) >= MAX_PENDING_DISCOVERY_OBSERVATIONS
            else []
        ),
    ])
    discovery_required_ids = list(state.control.force_discovery_ids)
    if state.control.force_synthesis_reason and not discovery_required_ids:
        discovery_required_ids = _unknowns_missing_project_evidence(
            state.findings.recorded,
            state.observations.items,
            runtime.analysis,
        )
    current_phase, current_tools, state.control.current_tool_choice, directive_prompt = _investigation_directive(
        recorded_findings=state.findings.recorded,
        analysis=runtime.analysis,
        tools=runtime.tools,
        observations=state.observations.items,
        semantic_repair_required_ids=semantic_repair_required_ids,
        resolution_required_ids=resolution_required_ids,
        discovery_required_ids=discovery_required_ids,
        clearify_unknown=clearify_unknown,
        verification_request=verification_request,
        finish_evidence_blocked=state.control.finish_evidence_blocked,
        force_synthesis_reason=state.control.force_synthesis_reason,
        semantic_gate_enabled=runtime.semantic_gate_enabled,
        read_only_no_unknowns=(
            runtime.analysis.get("execution_mode") == "read_only"
            and not runtime.analysis.get("unknowns")
        ),
    )
    # 方案 A：最少调查轮数未达到时，禁止提前结束（fast 也要跑
    # rounds_per_unknown × blocking_unknown 轮）。结束类 phase
    # （FINISH/READ_ONLY_FINISH/SYNTHESIZE）强制降级为 DISCOVERY_REQUIRED，
    # 且原 directive（如"调用 finish_investigation"）不再下发。
    # 例外：所有 unknowns 已 resolved 时不再降级——调查内容已完成，
    # 凑轮数没有可查的东西，降级只会让模型陷入
    # "finish 被 blocked（不在 allowed）+ resolve 被 blocked（already_resolved）"
    # 的死循环（实测 6 轮空转）。
    budget_floor_active = (
        current_round_index < runtime.min_rounds
        and current_phase in (
            InvestigationPhase.FINISH,
            InvestigationPhase.READ_ONLY_FINISH,
            InvestigationPhase.SYNTHESIZE,
        )
        and not _recorded_resolves_initial_unknowns(
            state.findings.recorded,
            runtime.analysis,
            repair_ids=semantic_repair_required_ids,
        )
    )
    if directive_prompt and not budget_floor_active:
        state.messages.append({"role": "user", "content": directive_prompt})
    if budget_floor_active:
        current_phase = InvestigationPhase.DISCOVERY_REQUIRED
        current_tools = _phase_tools(current_phase, tools=runtime.tools)
        state.control.current_tool_choice = "required"
        state.messages.append({
            "role": "user",
            "content": _minimum_rounds_prompt(runtime.min_rounds - current_round_index),
        })
    state.control.current_tools = current_tools
    allowed_tool_names = {
        str(((tool.get("function") or {}).get("name")) or "")
        for tool in current_tools
        if isinstance(tool, dict)
    }
    return (
        directive_prompt,
        current_phase,
        current_tools,
        allowed_tool_names,
        resolution_required_ids,
        clearify_unknown,
        verification_request,
        semantic_repair_required_ids,
    )


def _collect_tool_calls(
    state: InvestigationState,
    directive: str | None,
    runtime: InvestigationRuntime,
    *,
    round_index: int,
) -> Iterator[list[dict] | None]:
    del directive

    thinking_id = f"{runtime.run_id}-thinking-{round_index}"
    try:
        assistant = _call_model(
            runtime.provider,
            runtime.model,
            state.messages,
            tools=state.control.current_tools,
            tool_choice=state.control.current_tool_choice,
        )
    except ValueError as exc:
        reason = str(exc)
        yield {"op": "update", "id": thinking_id, "patch": {
            "text": reason,
            "done": True,
            "open": False,
        }}
        yield start_event(f"{runtime.run_id}-provider-error", "output", {
            "content": f"Provider request failed: {reason}",
            "streaming": False,
        })
        yield {"op": "update", "id": f"{runtime.run_id}-stage", "patch": {"state": "failed", "phase": "provider_error"}}
        return None
    if usage := _usage_delta(runtime.pricing_rules, assistant.pop("_usage", {})):
        _add_usage(state.usage.total, usage)
        yield start_event(f"{runtime.run_id}-usage-{round_index}", "usage", {
            "delta": usage,
            "total": state.usage.total,
        })

    tool_calls = assistant.get("tool_calls") or []
    content = _assistant_visible_text(assistant) or _tool_call_summary(tool_calls)
    state.messages.append(_assistant_message(assistant))
    yield {"op": "update", "id": thinking_id, "patch": {
        "text": content,
        "done": True,
        "open": bool(tool_calls),
    }}

    if not tool_calls:
        state.messages.append({"role": "user", "content": (
            "You did not call a tool. Continue by making an actual tool call, "
            "or call finish_investigation if the investigation is complete. "
            "Do not describe intended tool use in prose."
        )})
    return tool_calls


def _handle_resolve(
    state: InvestigationState,
    call_id: str,
    name: str,
    arguments: dict,
    runtime: InvestigationRuntime,
    *,
    resolution_required_ids: list[str],
    semantic_repair_required_ids: set[str],
) -> Iterator[DispatchAction]:
    del resolution_required_ids, semantic_repair_required_ids

    arguments = _resolve_unknown_arguments(arguments)
    _require_control_reason(arguments, name)
    resolutions = _resolutions(arguments.get("resolutions"))
    if not resolutions:
        raise ValueError("resolve_unknowns requires at least one valid resolution")
    resolutions = _canonicalize_resolution_unknown_ids(
        resolutions,
        _analysis_with_recorded_unknowns(
            runtime.analysis,
            state.findings.recorded,
        ),
    )
    _validate_resolution_refs(
        resolutions,
        _beliefs(state.findings.recorded.get("beliefs")),
        state.observations.items,
    )
    state.findings.recorded = _merge_recorded_findings(
        state.findings.recorded,
        {"resolutions": resolutions},
    )
    state.findings.recorded = _bind_grounding_evidence(
        state.findings.recorded,
        state.observations.items,
    )
    resolved_observation_ids = {
        evidence_id
        for resolution in resolutions
        for evidence_id in resolution.get("evidence", [])
    }
    state.observations.pending_ids = [
        item for item in state.observations.pending_ids
        if item not in resolved_observation_ids
    ]
    state.progress.duplicate_no_progress_signature = ""
    state.progress.duplicate_no_progress_count = 0
    state.progress.duplicate_no_progress_total = 0
    state.control.force_synthesis_reason = ""
    _semantic_repair_resolution_ids(
        state.findings.recorded,
    )
    task_updates = _investigation_task_updates(
        None,
        _initial_unknowns(_analysis_with_recorded_unknowns(runtime.analysis, state.findings.recorded)),
        resolutions,
    )
    output = json.dumps({
        "resolved": True,
        "counts": {"resolutions": len(resolutions)},
        "unknown_ids": [item["unknown_id"] for item in resolutions],
    }, ensure_ascii=False)
    yield start_event(call_id, "tool", _tool_event(
        name,
        arguments,
        output,
        description="Resolve investigation unknowns",
        symbol="R",
    ))
    if task_updates:
        yield start_event(f"{call_id}-task-update", "task_update", {
            "analysis_id": runtime.analysis.get("id", ""),
            "items": task_updates,
        })
    state.messages.append(_tool_message(call_id, output))
    return DispatchAction.CONTINUE_TOOLS


def _handle_record(
    state: InvestigationState,
    call_id: str,
    name: str,
    arguments: dict,
    runtime: InvestigationRuntime,
    *,
    resolution_required_ids: list[str],
    semantic_repair_required_ids: set[str],
    verification_request: dict | None,
    clearify_unknown: dict | None,
) -> Iterator[DispatchAction]:
    del verification_request, clearify_unknown

    _require_control_reason(arguments, name)
    if semantic_repair_required_ids and isinstance(arguments.get("resolutions"), list) and arguments["resolutions"]:
        _require_repair_resolutions(arguments, semantic_repair_required_ids)
    if (
        not state.observations.pending_ids
        and not resolution_required_ids
        and not _has_finding_fields(arguments)
    ):
        output = json.dumps({
            "recorded": False,
            "code": "nothing_to_record",
            "next_action": "finish_investigation",
            "message": "No pending observations or unresolved evidence-backed resolutions are available to record.",
        }, ensure_ascii=False)
        yield start_event(call_id, "tool", _tool_event(
            name,
            arguments,
            output,
            description="Record investigation findings",
        ))
        state.messages.append(_tool_message(call_id, output))
        _bump_hardlock(state)
        return DispatchAction.CONTINUE_TOOLS
    if (
        not _has_finding_fields(arguments)
        or (
            runtime.analysis.get("_canonicalized")
            and state.observations.pending_ids
            and not _record_consumes_observations(arguments, state.observations.pending_ids)
        )
    ):
        arguments = yield from _record_findings_by_slots(
            state,
            runtime,
            reason=str(arguments.get("reason") or "").strip(),
            required_resolution_ids=resolution_required_ids,
        )
    if _empty_discovery_recording(
        arguments,
        state.observations.pending_ids,
        resolution_required_ids,
    ):
        output = json.dumps({
            "recorded": False,
            "code": "no_material_findings",
            "pending_observation_ids": state.observations.pending_ids,
            "next_action": "continue_discovery",
        }, ensure_ascii=False)
        yield start_event(call_id, "tool", _tool_event(
            name,
            arguments,
            output,
            description="Record investigation findings",
        ))
        state.messages.append(_tool_message(call_id, output))
        _bump_hardlock(state)
        return DispatchAction.CONTINUE_TOOLS
    _require_finding_fields(arguments)
    _reject_empty_repair(arguments, state.findings.recorded)
    # 剥离模型提交的 repair 诊断字段：repair_mode/semantic_missing
    # 只能由 audit 质量门打标。REPAIR 阶段模型会从上下文把上一轮
    # missing 原样抄进提交的 resolution，不清理则 merge 后
    # _semantic_repair_resolution_ids 永远判该 unknown 待修，
    # 即使证据已补齐也无限 REPAIR（U4 类死循环根因）。
    arguments = _strip_submitted_repair_diagnostics(arguments)
    state.findings.recorded = _merge_recorded_findings(state.findings.recorded, arguments)
    state.findings.recorded = _bind_grounding_evidence(
        state.findings.recorded,
        state.observations.items,
    )
    state.observations.pending_ids.clear()
    record_signature = _recorded_findings_signature(state.findings.recorded)
    if record_signature == state.findings.last_record_signature:
        state.progress.repeated_record_no_progress += 1
    else:
        state.progress.repeated_record_no_progress = 0
        state.progress.duplicate_no_progress_signature = ""
        state.progress.duplicate_no_progress_count = 0
        state.progress.duplicate_no_progress_total = 0
        state.control.force_synthesis_reason = ""
        state.control.force_discovery_ids = []
    state.findings.last_record_signature = record_signature
    if state.progress.repeated_record_no_progress:
        state.control.force_discovery_ids = _unknowns_missing_project_evidence(
            state.findings.recorded,
            state.observations.items,
            runtime.analysis,
        )
    if state.progress.repeated_record_no_progress >= MAX_REPEATED_RECORD_NO_PROGRESS:
        state.control.finalization_reason = (
            "Runtime stopped after repeated record_investigation_findings "
            "calls produced no semantic progress."
        )
        state.control.stop_investigation = True
    task_updates = _record_task_updates(state.findings.recorded)
    output = json.dumps({
        "recorded": True,
        "counts": {field: len(state.findings.recorded.get(field, [])) for field in FINDING_FIELDS},
        **({"stalled": True} if state.control.stop_investigation else {}),
    }, ensure_ascii=False)
    yield start_event(call_id, "tool", _tool_event(
        name,
        arguments,
        output,
        description="Record investigation findings",
    ))
    if task_updates:
        yield start_event(f"{call_id}-task-update", "task_update", {
            "analysis_id": runtime.analysis.get("id", ""),
            "items": task_updates,
        })
    state.messages.append(_tool_message(call_id, output))
    if state.control.stop_investigation:
        yield start_event(f"{runtime.run_id}-safety-record-no-progress", "safety_stop", {
            "reason": "record_no_progress",
            "message": state.control.finalization_reason,
            "tool": name,
        })
        return DispatchAction.BREAK_TOOLS
    return DispatchAction.CONTINUE_TOOLS


def _handle_finish(
    state: InvestigationState, call_id: str, name: str, arguments: dict,
    runtime: InvestigationRuntime,
) -> Iterator[DispatchAction]:
    _require_control_reason(arguments, name)
    state.findings.recorded = _apply_direct_resolution_gate(
        state.findings.recorded,
        state.observations.items,
        strict_grounding=runtime.semantic_gate_enabled and _analysis_requests_implementation(runtime.analysis),
    )
    decision = yield from _decide_finish_transition(
        state,
        runtime,
    )
    if decision is FinishDecision.ACCEPT:
        return _accept_finish(state, call_id, arguments, runtime)
    return _enter_finish_block(state, call_id, decision, runtime)


def _decide_finish_transition(
    state: InvestigationState,
    runtime: InvestigationRuntime,
) -> Iterator[FinishDecision]:
    if runtime.semantic_gate_enabled:
        _semantic_repair_resolution_ids(state.findings.recorded)
    if (
        runtime.semantic_gate_enabled
        and
        runtime.analysis.get("_canonicalized")
        and not _audit_covers_resolutions(
            state.findings.last_quality_audit,
            state.findings.recorded,
            runtime.analysis,
        )
    ):
        state.findings.last_quality_audit = yield from _audit_recorded_findings(
            state,
            runtime,
        )
        state.findings.recorded, requests, questions = _apply_investigation_audit(
            state.findings.recorded,
            state.findings.last_quality_audit,
            observations=state.observations.items,
            strict_grounding=_analysis_requests_implementation(runtime.analysis),
            allow_verification=runtime.subagent_enabled and _analysis_requests_implementation(runtime.analysis),
            analysis=runtime.analysis,
        )
        _semantic_repair_resolution_ids(
            state.findings.recorded,
        )
        attempted = state.verification.attempted | {
            (item.get("unknown_id"), item.get("hypothesis"))
            for item in state.verification.queue
        }
        state.verification.queue.extend(
            item for item in requests
            if (item.get("unknown_id"), item.get("hypothesis")) not in attempted
            and _unknown_blocks_finish(item.get("unknown_id"), runtime.analysis, state.findings.recorded)
        )
        state.verification.clearify_questions.update({
            unknown_id: question
            for unknown_id, question in questions.items()
            if _unknown_blocks_finish(unknown_id, runtime.analysis, state.findings.recorded)
        })
    pending_resolution_statuses = {
        str(item.get("status") or "")
        for item in state.findings.recorded.get("resolutions", [])
        if isinstance(item, dict)
        and _unknown_blocks_finish(item.get("unknown_id"), runtime.analysis, state.findings.recorded)
    }
    if "needs_clearify" in pending_resolution_statuses or state.verification.clearify_questions:
        return FinishDecision.CLEARIFY
    if state.verification.queue:
        return FinishDecision.VERIFY
    if (
        state.verification.queue
        or state.verification.clearify_questions
        or pending_resolution_statuses & {"partially_resolved", "needs_clearify"}
    ):
        return FinishDecision.REPAIR
    return FinishDecision.ACCEPT


def _accept_finish(
    state: InvestigationState,
    call_id: str,
    arguments: dict,
    runtime: InvestigationRuntime,
) -> DispatchAction:
    state.control.final = _finish_payload(
        _finish_arguments(
            state.findings.recorded,
            arguments,
            prefer_finish_summary=not _analysis_requests_implementation(runtime.analysis),
        ),
        analysis=runtime.analysis,
        observations=state.observations.items,
        repair_conflicts=True,
        workspace_dir=runtime.workspace_dir,
    )
    state.messages.append({
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(state.control.final, ensure_ascii=False),
    })
    if state.control.final.get("resolution_repair"):
        state.control.finalization_reason = "Investigation findings need explicit resolutions before finalizing."
        state.messages.append({
            "role": "user",
            "content": _resolution_repair_prompt(state.control.final["resolution_repair"]),
        })
        state.control.final = None
        state.control.stop_investigation = True
    return DispatchAction.BREAK_TOOLS


def _enter_finish_block(
    state: InvestigationState,
    call_id: str,
    decision: FinishDecision,
    runtime: InvestigationRuntime,
) -> DispatchAction:
    del runtime

    next_action = {
        FinishDecision.CLEARIFY: "clearify",
        FinishDecision.VERIFY: "verify_hypothesis",
        FinishDecision.REPAIR: "continue_investigation",
    }[decision]
    repair_payload = _semantic_repair_payload(
        state.findings.recorded,
        _semantic_repair_resolution_ids(state.findings.recorded),
    )
    output = json.dumps({
        "finished": False,
        "reason": "semantic_quality_gate",
        "repair": repair_payload,
        "next_action": next_action,
    }, ensure_ascii=False)
    state.messages.append({
        "role": "tool",
        "tool_call_id": call_id,
        "content": output,
    })
    state.messages.append({"role": "user", "content": (
        "The semantic quality gate did not authorize every resolution. "
        "Follow its next action; do not finish or reuse the rejected conclusion."
    )})
    return DispatchAction.BREAK_TOOLS


def _handle_clearify(
    state: InvestigationState,
    call_id: str,
    name: str,
    arguments: dict,
    runtime: InvestigationRuntime,
    *,
    clearify_unknown: dict | None,
    resolution_required_ids: list[str],
    semantic_repair_required_ids: set[str],
    asked_clearify_ids: set[str],
) -> Iterator[DispatchAction]:
    del resolution_required_ids, semantic_repair_required_ids

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
        state.messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": output,
        })
        _bump_hardlock(state)
        return DispatchAction.CONTINUE_TOOLS
    _validate_tool_contract(
        name,
        target_unknown_ids=target_ids,
        reason=str(arguments.get("reason") or "").strip(),
        orientation=bool(arguments.get("orientation", False)),
        analysis=_analysis_with_recorded_unknowns(
            runtime.analysis,
            state.findings.recorded,
        ),
    )
    answered_by_previous_round = {
        str(item.get("unknown_id") or "").strip()
        for item in state.findings.recorded.get("resolutions", [])
        if isinstance(item, dict)
        and (
            item.get("reason") in (CLEARIFY_RESOLUTION_REASON, CLEARIFY_UNRESOLVED_REASON)
            or (
                str(item.get("status") or "") in ("resolved", "partially_resolved")
                and str(item.get("answer") or "").strip()
            )
        )
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
        state.messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": output,
        })
        _bump_hardlock(state)
        return DispatchAction.CONTINUE_TOOLS
    question_id = runtime.clearify_runtime.create_pending()
    yield start_event(question_id, "user_question", _clearify_question(
        arguments,
        question_id=question_id,
        analysis=runtime.analysis,
    ))
    answer = runtime.clearify_runtime.wait(question_id)
    output = _clearify_tool_result(answer)
    resolution_records = _clearify_resolution_records(arguments, answer, state, runtime)
    state.progress.repeated_tool_error_name = ""
    state.progress.repeated_tool_error_count = 0
    if resolution_records:
        for resolution in resolution_records:
            asked_clearify_ids.add(resolution["unknown_id"])
            for question_id in list(state.verification.clearify_questions):
                if _same_unknown_id(question_id, resolution["unknown_id"]):
                    state.verification.clearify_questions.pop(question_id, None)
        state.findings.recorded = _merge_recorded_findings(
            state.findings.recorded,
            {"resolutions": resolution_records},
        )
    yield start_event(call_id, "tool", _tool_event(
        name,
        arguments,
        output,
        description="Ask the user for clarification",
    ))
    if resolution_records:
        yield start_event(f"{call_id}-task-update", "task_update", {
            "analysis_id": runtime.analysis.get("id", ""),
            "items": _investigation_task_updates(
                None,
                _initial_unknowns(_analysis_with_recorded_unknowns(runtime.analysis, state.findings.recorded)),
                resolution_records,
            ),
        })
    state.messages.append(_tool_message(call_id, output))
    return DispatchAction.CONTINUE_TOOLS


def _handle_discovery(
    state: InvestigationState,
    call_id: str,
    name: str,
    arguments: dict,
    runtime: InvestigationRuntime,
    *,
    semantic_repair_required_ids: set[str],
    verification_request: dict | None,
    resolution_required_ids: list[str],
    round_index: int,
) -> Iterator[DispatchAction]:
    del semantic_repair_required_ids, resolution_required_ids, round_index

    cache_key = _tool_cache_key(name, arguments)
    if name == "read":
        cached_output = _read_from_file_cache(arguments, state.caches.read_file)
        if cached_output is not None:
            output = cached_output
            yield start_event(call_id, registry.event_type(name), _tool_event(
                name,
                arguments,
                output,
                description="Investigation tool",
                status="cached",
            ))
            state.messages.append(_tool_message(call_id, output))
            return DispatchAction.CONTINUE_TOOLS
    if cache_key in state.caches.tool:
        cached_observation_id = state.caches.tool_observation_ids.get(cache_key, "")
        state.progress.duplicate_no_progress_total += 1
        if state.progress.duplicate_no_progress_signature == cache_key:
            state.progress.duplicate_no_progress_count += 1
        else:
            state.progress.duplicate_no_progress_signature = cache_key
            state.progress.duplicate_no_progress_count = 1
        next_action = (
            "resolve_unknowns"
            if state.observations.pending_ids or cached_observation_id
            else "choose_different_evidence"
        )
        output = _duplicate_no_progress_json(
            name,
            duplicate_count=state.progress.duplicate_no_progress_total,
            cached_observation_id=cached_observation_id,
            required_next_action=next_action,
        )
        if state.progress.duplicate_no_progress_count >= MAX_DUPLICATE_NO_PROGRESS:
            state.control.force_synthesis_reason = _duplicate_no_progress_prompt(
                name,
                cached_observation_id,
                state.observations.pending_ids,
            )
        yield start_event(call_id, registry.event_type(name), _tool_event(
            name,
            arguments,
            output,
            description="Investigation tool",
            status="no_progress",
            deduplicated=True,
            cached_observation_id=cached_observation_id,
        ))
        state.messages.append(_tool_message(call_id, output))
        if state.progress.duplicate_no_progress_total >= MAX_REPEATED_TOOL_ERRORS:
            state.control.finalization_reason = (
                "Runtime stopped after repeated duplicate no-progress tool calls: "
                f"{name or 'invalid'}."
            )
            state.control.stop_investigation = True
            yield start_event(f"{runtime.run_id}-safety-duplicate-no-progress", "safety_stop", {
                "reason": "duplicate_no_progress",
                "message": state.control.finalization_reason,
                "tool": name or "invalid",
                "cached_observation_id": cached_observation_id,
            })
            return DispatchAction.BREAK_TOOLS
        return DispatchAction.CONTINUE_TOOLS
    output = yield from _run_tool_stream(
        name,
        call_id,
        arguments,
        runtime.workspace_dir,
        _analysis_with_recorded_unknowns(
            runtime.analysis,
            state.findings.recorded,
        ),
        relax_discovery_contract=(
            runtime.semantic_gate_enabled
            and _get_investigation_phase(state) == InvestigationPhase.REPAIR
            and name in _REPAIR_ALLOWED_TOOL_NAMES - {"record_investigation_findings"}
        ),
    )
    if name == "read":
        _cache_read_full_text(arguments, output, state.caches.read_file)
    state.progress.repeated_tool_error_name = ""
    state.progress.repeated_tool_error_count = 0
    state.caches.tool[cache_key] = output
    observation = _tool_observation(name, call_id, output)
    state.observations.items.append(observation)
    state.caches.tool_observation_ids[cache_key] = observation["id"]
    state.observations.pending_ids.append(observation["id"])
    state.progress.duplicate_no_progress_signature = ""
    state.progress.duplicate_no_progress_count = 0
    state.progress.duplicate_no_progress_total = 0
    state.control.force_synthesis_reason = ""
    state.control.force_discovery_ids = []
    if verification_request and _is_hypothesis_verifier_call(name, arguments):
        state.verification.attempted.add((
            verification_request["unknown_id"],
            verification_request["hypothesis"],
        ))
        state.verification.queue.pop(0)
    state.messages.append(_tool_message(call_id, output))
    return DispatchAction.CONTINUE_TOOLS


def _dispatch_tool_calls(
    state: InvestigationState,
    tool_calls: list[dict],
    runtime: InvestigationRuntime,
    *,
    resolution_required_ids: list[str],
    semantic_repair_required_ids: set[str],
    verification_request: dict | None,
    clearify_unknown: dict | None,
    allowed_tool_names: set[str],
    round_index: int,
) -> Iterator[DispatchAction]:
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
            state.messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": output,
            })
            continue
        try:
            arguments = _investigation_tool_arguments(
                name,
                function.get("arguments"),
                pending_observation_ids=state.observations.pending_ids,
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
                    "hypothesis": verification_request["hypothesis"],
                    "expected_observation": (
                        "The verifier returns a supported, opposed, or inconclusive "
                        "verdict with evidence for the atomic hypothesis."
                    ),
                    "decision_impact": (
                        "The target resolution can be accepted, rejected, or kept "
                        "partial without repeating the same investigation."
                    ),
                    "stop_condition": (
                        "Stop after one independent verdict for this atomic hypothesis."
                    ),
                }
            failed_key = _tool_cache_key(name, arguments)
            if failed_key in state.caches.failed_tool:
                error_name = name or "invalid"
                if error_name not in round_error_names:
                    state.progress.repeated_tool_error_name = error_name
                    state.progress.repeated_tool_error_count += 1
                    round_error_names.add(error_name)
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
                yield start_event(call_id, registry.event_type(name), _tool_event(
                    name or "invalid",
                    arguments,
                    output,
                    description="Investigation tool",
                    status="error",
                    deduplicated=True,
                ))
                state.messages.append(_tool_message(call_id, output))
                if state.progress.repeated_tool_error_count >= MAX_REPEATED_TOOL_ERRORS:
                    state.control.finalization_reason = (
                        "Runtime stopped an identical failed tool call loop: "
                        f"{name or 'invalid'}."
                    )
                    state.control.stop_investigation = True
                    break
                continue
            if name not in allowed_tool_names:
                if _recorded_resolves_initial_unknowns(
                    state.findings.recorded,
                    runtime.analysis,
                    repair_ids=semantic_repair_required_ids,
                ) and name != "finish_investigation":
                    output = json.dumps({
                        "error": "investigation_already_resolved",
                        "retryable": True,
                        "required_tool": "finish_investigation",
                        "message": (
                            "All initial unknowns are resolved. Call finish_investigation now; "
                            "do not repeat discovery or recording tools."
                        ),
                    }, ensure_ascii=False)
                    yield start_event(call_id, registry.event_type(name), _tool_event(
                        name or "invalid",
                        arguments,
                        output,
                        description="Investigation tool",
                        status="error",
                    ))
                    state.messages.append(_tool_message(call_id, output))
                    _bump_hardlock(state)
                    continue
                if resolution_required_ids and not semantic_repair_required_ids:
                    # 非 REPAIR 场景：初始 unknowns 已有证据记录，先 resolve
                    # 再发现。REPAIR 阶段（semantic_repair_required_ids 非空）
                    # 必须允许 read/grep 补证据——repair 提示词明确要求先取证
                    # 再 record，拦截 discovery 会让模型空转重写结论而死循环。
                    required_tool = (
                        "resolve_unknowns"
                        if "resolve_unknowns" in allowed_tool_names
                        else "record_investigation_findings"
                        if "record_investigation_findings" in allowed_tool_names
                        else ""
                    )
                    output = json.dumps({
                        "error": "resolution_required",
                        "retryable": True,
                        **({"required_tool": required_tool} if required_tool else {}),
                        "target_unknown_ids": resolution_required_ids,
                        "message": (
                            "Existing project evidence is already recorded for these unknowns. "
                            "Resolve explicit unknowns before calling more discovery tools."
                        ),
                    }, ensure_ascii=False)
                    yield start_event(call_id, registry.event_type(name), _tool_event(
                        name or "invalid",
                        arguments,
                        output,
                        description="Investigation tool",
                        status="error",
                    ))
                    state.messages.append(_tool_message(call_id, output))
                    _bump_hardlock(state)
                    continue
                output = _tool_blocked_error_json(
                    name,
                    allowed_tools=sorted(allowed_tool_names),
                )
                yield start_event(call_id, registry.event_type(name), _tool_event(
                    name or "invalid",
                    arguments,
                    output,
                    description="Investigation tool",
                    status="error",
                ))
                state.messages.append(_tool_message(call_id, output))
                state.messages.append({"role": "user", "content": (
                    "The semantic quality gate rejected some resolutions; you are in "
                    "resolution-repair state. resolve_unknowns is unavailable here — "
                    "submit corrected, evidence-backed conclusions with "
                    "record_investigation_findings instead, then call "
                    "finish_investigation when every unknown is resolved."
                ) if semantic_repair_required_ids and name == "resolve_unknowns" else (
                    "The tool was blocked by the current investigation state. "
                    "Choose one of the allowed tools from the tool result; do not retry "
                    "the blocked discovery call with the same arguments."
                )})
                continue
            if name == "resolve_unknowns":
                yield from _handle_resolve(
                    state,
                    call_id,
                    name,
                    arguments,
                    runtime,
                    resolution_required_ids=resolution_required_ids,
                    semantic_repair_required_ids=semantic_repair_required_ids,
                )
                continue
            if name == "record_investigation_findings":
                action = yield from _handle_record(
                    state,
                    call_id,
                    name,
                    arguments,
                    runtime,
                    resolution_required_ids=resolution_required_ids,
                    semantic_repair_required_ids=semantic_repair_required_ids,
                    verification_request=verification_request,
                    clearify_unknown=clearify_unknown,
                )
                if action == DispatchAction.BREAK_TOOLS:
                    break
                continue
            if name == "finish_investigation":
                action = yield from _handle_finish(
                    state,
                    call_id,
                    name,
                    arguments,
                    runtime,
                )
                if action == DispatchAction.BREAK_TOOLS:
                    break
                break
            if name == "clearify":
                yield from _handle_clearify(
                    state,
                    call_id,
                    name,
                    arguments,
                    runtime,
                    clearify_unknown=clearify_unknown,
                    resolution_required_ids=resolution_required_ids,
                    semantic_repair_required_ids=semantic_repair_required_ids,
                    asked_clearify_ids=asked_clearify_ids,
                )
                continue
            action = yield from _handle_discovery(
                state,
                call_id,
                name,
                arguments,
                runtime,
                semantic_repair_required_ids=semantic_repair_required_ids,
                verification_request=verification_request,
                resolution_required_ids=resolution_required_ids,
                round_index=round_index,
            )
            if action == DispatchAction.BREAK_TOOLS:
                break
            continue
        except Exception as exc:
            state.control.finish_evidence_blocked = False
            if (
                name == "finish_investigation"
                and isinstance(exc, ValueError)
                and ("references file" in str(exc) or "claims behavior" in str(exc))
            ):
                state.control.finish_evidence_blocked = True
            raw_arguments = function.get("arguments") or "{}"
            partial_arguments = _partial_tool_arguments(raw_arguments)
            if name == "record_investigation_findings":
                partial_arguments = _record_arguments(partial_arguments)
            if name == "resolve_unknowns":
                partial_arguments = _resolve_unknown_arguments(partial_arguments)
                salvaged_resolutions = _salvage_resolution_candidates(
                    partial_arguments,
                    state.observations.items,
                )
                if salvaged_resolutions:
                    state.findings.recorded = _merge_recorded_findings(
                        state.findings.recorded,
                        {"resolutions": salvaged_resolutions},
                    )
                    state.findings.recorded = _bind_grounding_evidence(
                        state.findings.recorded,
                        state.observations.items,
                    )
            if name == "record_investigation_findings" and _has_finding_fields(partial_arguments):
                state.findings.recorded = _merge_recorded_findings(state.findings.recorded, partial_arguments)
                state.observations.pending_ids.clear()
                state.findings.last_quality_audit = {}
            output = _tool_repair_error_json(
                exc,
                name,
                raw_arguments,
                partial_arguments,
                observations=state.observations.items,
            )
            error_name = name or "invalid"
            state.caches.failed_tool[_tool_cache_key(
                error_name,
                arguments or partial_arguments,
            )] = output
            if error_name not in round_error_names:
                if error_name == state.progress.repeated_tool_error_name:
                    state.progress.repeated_tool_error_count += 1
                else:
                    state.progress.repeated_tool_error_name = error_name
                    state.progress.repeated_tool_error_count = 1
                round_error_names.add(error_name)
            yield start_event(call_id, registry.event_type(name), {
                "name": name or "invalid",
                "description": "Investigation tool",
                "status": "error",
                "open": False,
                "input": raw_arguments,
                "output": output,
            })
            if error_name == "finish_investigation" and not state.control.finish_evidence_blocked:
                # finish 参数错误（requires reason 等）后模型倾向停止，而停止会走自动
                # finalize 吞掉失败（不完整的 investigation 流向下游导致
                # patch_planning_failed）。hard-lock：参数类失败第一次就强制模型
                # 补参数重试 finish。evidence 类失败（references file/claims behavior）
                # 不强制——模型需要 read 补证据，锁定 finish 会死锁。
                state.control.current_tool_choice = {"type": "function", "function": {"name": "finish_investigation"}}
            if state.progress.repeated_tool_error_count >= MAX_REPEATED_TOOL_ERRORS:
                state.control.finalization_reason = (
                    "Runtime recovered after repeated tool argument errors: "
                    f"{name or 'invalid'} failed with {exc}."
                )
                if name == "record_investigation_findings":
                    state.control.final = _runtime_recovered_investigation(
                        state.control.finalization_reason,
                        runtime.analysis,
                        state.observations.items,
                        state.findings.recorded,
                    )
                state.control.stop_investigation = True
        state.messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": output,
        })
        if state.control.stop_investigation:
            yield start_event(f"{runtime.run_id}-safety-repeated-tool-error", "safety_stop", {
                "reason": "repeated_tool_error",
                "message": state.control.finalization_reason,
                "tool": name or "invalid",
                "visibility": "diagnostic",
            })
            break
    if state.control.final is not None:
        return DispatchAction.TERMINATE
    if state.control.stop_investigation:
        return DispatchAction.TERMINATE
    return DispatchAction.NEXT_ROUND


def _should_terminate_investigation(
    state: InvestigationState,
    round_index: int,
    runtime: InvestigationRuntime,
) -> bool:
    del round_index, runtime

    return state.control.final is not None or state.control.stop_investigation


def _initialize_investigation_stream(
    *,
    message: str,
    analysis: dict,
    context: list[str],
    workspace_dir: str,
    max_rounds: int | None,
    findings: list[str] | None,
    previous_observations: list[dict] | None,
    previous_knowledge: list[dict] | None,
    previous_findings: dict | None,
    preserve_grounding_evidence: bool,
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
    effort_profile = app_settings.get_effort_profile(analysis.get("effort"))
    quality_gate = str(analysis.get("quality_gate") or effort_profile["quality_gate"]).strip().casefold()
    semantic_gate_enabled = quality_gate != "basic"
    subagent_enabled = bool(effort_profile["subagent_enabled"])
    rounds_per_unknown = (
        int(effort_profile["investigation_rounds"] or 0)
        if analysis.get("effort") and effort_profile["investigation_rounds"]
        else int(app_settings.get_round_limit("investigation_rounds") or 0)
    )
    if rounds_per_unknown <= 0:
        rounds_per_unknown = 2
    min_rounds = _minimum_investigation_rounds(analysis, rounds_per_unknown)
    # 方案 A：不设总轮数上限——调查由收敛条件（blocking unknown 全部解决 +
    # ready for patch planning）驱动结束；min_rounds 只是"至少跑 N×unknowns 轮"
    # 的深度底线。防死循环由 pass 级保护（_MAX_INVESTIGATION_PASSES）、
    # record 无进展检测与状态机 phase 强制推进承担。
    max_rounds = int(max_rounds or 0) if max_rounds is not None else 0
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
    runtime = InvestigationRuntime(
        provider=provider,
        model=model,
        pricing_rules=pricing_rules,
        run_id=run_id,
        stage_id=stage_id,
        tools=tools,
        analysis=analysis,
        context=context,
        workspace_dir=workspace_dir,
        max_rounds=max_rounds,
        min_rounds=min_rounds,
        effort_profile=effort_profile,
        quality_gate=quality_gate,
        rounds_per_unknown=rounds_per_unknown,
        semantic_gate_enabled=semantic_gate_enabled,
        subagent_enabled=subagent_enabled,
        preserve_grounding_evidence=preserve_grounding_evidence,
        previous_observations=previous_observations,
        previous_knowledge=previous_knowledge,
        clearify_runtime=clearify_runtime,
    )
    state = InvestigationState(messages=messages, usage=UsageState(total=usage_total))
    state.observations.items.extend([
        dict(item)
        for item in previous_observations or []
        if isinstance(item, dict) and item.get("fresh", True)
    ])
    state.findings.recorded = _continued_recorded_findings(previous_findings, state.observations.items)
    state.control.finalization_reason = (
        "Investigation model stopped before finish_investigation; summarizing observed facts."
    )
    state.findings.last_record_signature = _recorded_findings_signature(state.findings.recorded)
    return {
        "provider": provider,
        "model": model,
        "pricing_rules": pricing_rules,
        "effort_profile": effort_profile,
        "quality_gate": quality_gate,
        "semantic_gate_enabled": semantic_gate_enabled,
        "subagent_enabled": subagent_enabled,
        "rounds_per_unknown": rounds_per_unknown,
        "min_rounds": min_rounds,
        "max_rounds": max_rounds,
        "run_id": run_id,
        "stage_id": stage_id,
        "tools": tools,
        "runtime": runtime,
        "state": state,
    }


def _finish_investigation_stream(
    state: InvestigationState,
    runtime: InvestigationRuntime,
) -> Iterator[dict]:
    if state.control.final is None and state.control.stop_investigation:
        state.control.final = _runtime_recovered_investigation(
            state.control.finalization_reason,
            runtime.analysis,
            state.observations.items,
            state.findings.recorded,
        )
    elif state.control.final is None:
        state.control.final = yield from _finalize_investigation(
            state,
            runtime,
            reason=state.control.finalization_reason,
        )
    if state.findings.last_quality_audit:
        state.control.final["quality_audit"] = state.findings.last_quality_audit
    state.control.final["observations"] = _final_observations(
        state.observations.items + [
            item for item in state.control.final.get("observations", [])
            if isinstance(item, dict)
        ],
        preserve_grounding_evidence=runtime.preserve_grounding_evidence,
    )

    implementation_intent = _analysis_requests_implementation(runtime.analysis)
    yield {"op": "update", "id": runtime.stage_id, "patch": {
        "state": "done",
        "phase": "patch_planning_ready" if state.control.final.get("ready_for_patch_planning") and implementation_intent else "done",
    }}
    step = _step_result(state.control.final, implementation_intent=implementation_intent)
    state.control.final["step_result"] = step
    yield start_event(f"{runtime.run_id}-step-result", "step_result", step)
    if state.control.final.get("task_updates"):
        yield start_event(f"{runtime.run_id}-task-update", "task_update", {
            "analysis_id": runtime.analysis.get("id", ""),
            "items": state.control.final["task_updates"],
        })
    yield start_event(f"{runtime.run_id}-output", "output", {
        "content": _summary(state.control.final),
        "streaming": False,
        "visibility": "diagnostic" if state.control.final.get("runtime_recovered") else "default",
    })
    yield {"op": "done", "investigation": state.control.final}


def _run_investigation_round(
    state: InvestigationState,
    runtime: InvestigationRuntime,
    *,
    round_index: int,
) -> Iterator[DispatchAction | None]:
    thinking_id = f"{runtime.run_id}-thinking-{round_index}"
    yield start_event(thinking_id, "thinking", {"text": "", "done": False, "open": True})
    (
        directive_prompt,
        _current_phase,
        current_tools,
        allowed_tool_names,
        resolution_required_ids,
        clearify_unknown,
        verification_request,
        semantic_repair_required_ids,
    ) = _prepare_round(
        state,
        runtime,
        current_round_index=round_index,
        previous_rounds_usage=state.usage.total,
    )
    tool_calls = yield from _collect_tool_calls(
        state,
        directive_prompt,
        runtime,
        round_index=round_index,
    )
    if tool_calls is None:
        return None
    if not tool_calls:
        return DispatchAction.NEXT_ROUND
    return (yield from _dispatch_tool_calls(
        state,
        tool_calls,
        runtime,
        resolution_required_ids=resolution_required_ids,
        semantic_repair_required_ids=semantic_repair_required_ids,
        verification_request=verification_request,
        clearify_unknown=clearify_unknown,
        allowed_tool_names=allowed_tool_names,
        round_index=round_index,
    ))


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
    initialized = yield from _initialize_investigation_stream(
        message=message,
        analysis=analysis,
        context=context,
        workspace_dir=workspace_dir,
        max_rounds=max_rounds,
        findings=findings,
        previous_observations=previous_observations,
        previous_knowledge=previous_knowledge,
        previous_findings=previous_findings,
        preserve_grounding_evidence=preserve_grounding_evidence,
    )
    state = initialized["state"]
    runtime = initialized["runtime"]

    for round_index in _round_indexes(runtime.max_rounds, start=0):
        outcome = yield from _run_investigation_round(
            state,
            runtime,
            round_index=round_index,
        )
        if outcome is None:
            return
        if outcome == DispatchAction.TERMINATE or _should_terminate_investigation(state, round_index, runtime):
            break
    else:
        state.control.finalization_reason = "Investigation step limit reached. Summarizing observed facts."

    yield from _finish_investigation_stream(
        state,
        runtime,
    )

def _investigation_tools() -> list[dict]:
    tools = [
        _investigation_tool_schema(tool.name, tool.description, tool.parameters)
        for tool in registry.list_for_capability(INVESTIGATION_CAPABILITY)
    ]
    tools.append(_resolve_unknowns_tool_schema())
    tools.append(_record_findings_tool_schema())
    tools.append(_finish_tool_schema())
    return tools


def _read_from_file_cache(arguments: dict, file_cache: dict[str, str]) -> str | None:
    """read 文件级缓存命中：同文件不同行范围直接切行返回，跳过读盘与 LSP。

    注意：完全相同参数的重复调用仍走 cache_key 的 no_progress 分支（防死循环），
    这里只处理"文件已读过、但请求的行范围不同"的场景——这是真正省执行的地方。
    """
    if not arguments.get("paths"):
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            return None
        full = file_cache.get(_read_path_norm(path))
        if full is None:
            return None
        lines = full.splitlines()
        start = max(0, int(arguments.get("start_line") or 1) - 1)
        end = int(arguments.get("end_line") or len(lines))
        selection = lines[start:end]
        return json.dumps({
            "title": f"read {path} L{start+1}-{min(end, len(lines))} (cached)",
            "output": "\n".join(selection),
            "metadata": {"cached": True},
        }, ensure_ascii=False)
    return None


def _cache_read_full_text(arguments: dict, output: str, file_cache: dict[str, str]) -> None:
    """执行成功后缓存 read 的完整文件内容（文件级缓存，供后续不同行范围命中）。"""
    if arguments.get("paths"):
        return
    try:
        data = json.loads(output or "{}")
        full = (data.get("metadata") or {}).get("full_text")
    except Exception:
        return
    path = arguments.get("path")
    if isinstance(path, str) and path.strip() and isinstance(full, str):
        file_cache[_read_path_norm(path)] = full


def _blocking_investigable_count(analysis: dict | None) -> int:
    """初始任务契约中需调查的 blocking unknown 数（调查深度基准）。"""
    if not isinstance(analysis, dict):
        return 0
    return sum(
        1
        for item in _initial_unknowns(analysis)
        if item.get("resolution_strategy") == "investigate_project" and item.get("blocking")
    )


def _minimum_investigation_rounds(analysis: dict | None, rounds_per_unknown: int) -> int:
    """方案 A：最少调查轮数 = rounds_per_unknown × blocking unknown 数。"""
    return _blocking_investigable_count(analysis) * max(1, rounds_per_unknown)


def _minimum_rounds_prompt(remaining_rounds: int) -> str:
    return (
        f"Investigation budget: at least {remaining_rounds} more round(s) remain "
        "before you may finish. Keep gathering evidence: cross-check every task "
        "unknown against project observations, verify each resolution, and confirm "
        "the acceptance criteria are grounded. Do not finish yet."
    )


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


def _duplicate_no_progress_prompt(
    tool_name: str,
    cached_observation_id: str,
    pending_observation_ids: list[str],
) -> str:
    ids = _dedupe_strings([cached_observation_id, *pending_observation_ids])
    return (
        "The investigation repeated already-observed discovery tool calls without "
        f"new progress: {tool_name or 'invalid'}. Do not call those discovery actions again. "
        "Use resolve_unknowns if the cached observation answers a blocking unknown, "
        "record_investigation_findings if it contains material findings, or "
        "finish_investigation if investigation is already sufficient. "
        f"Relevant observation ids: {', '.join(ids) if ids else 'none'}."
    )


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


def _record_consumes_observations(arguments: dict, observation_ids: list[str]) -> bool:
    pending = {str(item).strip() for item in observation_ids if str(item).strip()}
    if not pending:
        return False
    for field in ("beliefs", "resolutions"):
        for item in arguments.get(field, []):
            if isinstance(item, dict) and pending.intersection(_reference_list(item.get("evidence"))):
                return True
    return False


def _resolve_unknown_arguments(arguments: dict) -> dict:
    normalized = dict(arguments)
    normalized["resolutions"] = _resolutions(normalized.get("resolutions"))
    return normalized


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


def _is_hypothesis_verifier_call(name: str, arguments: dict) -> bool:
    if name != "subagent":
        return False
    agent = str(arguments.get("agent") or arguments.get("name") or "")
    return agent.strip().removeprefix("@").casefold() == "hypothesis-verifier"


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
    delegated = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") == "partially_resolved"
        and item.get("reason") == CLEARIFY_UNRESOLVED_REASON
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
            and item.get("type") in ("product_decision", "engineering_decision")
            and not any(_same_unknown_id(item["id"], completed_id) for completed_id in completed)
            and not any(_same_unknown_id(item["id"], delegated_id) for delegated_id in delegated)
            and (
                item.get("resolution_strategy") == "clearify"
                or any(_same_unknown_id(item["id"], pending_id) for pending_id in needs_clearify)
                or item.get("type") == "engineering_decision"
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
    merged = {
        **analysis,
        "unknowns": _merge_unknowns(
            _initial_unknowns(analysis)
            + _unknowns(recorded.get("unknowns"))
            + _unknowns(recorded.get("new_unknowns"))
        ),
    }
    # 已 resolve 的 unknown 会被 _open_analysis_unknowns 从 unknowns 移除，
    # 但它们仍是任务契约的一部分——保留 resolutions 让校验层能识别（模型
    # 可能继续为已解决的 unknown 补充证据，这不应当作"不在契约中"拒绝）。
    recorded_resolutions = recorded.get("resolutions") if isinstance(recorded, dict) else None
    if recorded_resolutions:
        merged["resolutions"] = _resolutions(analysis.get("resolutions")) + _resolutions(recorded_resolutions)
    return merged


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


def _unknowns_missing_project_evidence(
    recorded: dict,
    observations: list[dict],
    analysis: dict | None,
) -> list[str]:
    initial = [
        item for item in (analysis or {}).get("unknowns", [])
        if isinstance(item, dict)
        and item.get("blocking")
        and item.get("resolution_strategy") == "investigate_project"
        and str(item.get("id") or "").strip()
    ]
    if not initial:
        return []
    completed = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") in {"resolved", "deferred"}
        and str(item.get("unknown_id") or "").strip()
    }
    supported = _supported_unknown_ids(recorded, observations)
    return [
        str(item["id"])
        for item in initial
        if not any(_same_unknown_id(item["id"], done_id) for done_id in completed)
        and not any(_same_unknown_id(item["id"], supported_id) for supported_id in supported)
    ]


def _pending_observation_unknown_ids(
    observations: list[dict],
    pending_observation_ids: list[str],
    analysis: dict | None,
    recorded: dict,
) -> list[str]:
    pending = {str(item).strip() for item in pending_observation_ids if str(item).strip()}
    if not pending:
        return []
    candidates = [
        item for item in (analysis or {}).get("unknowns", [])
        if isinstance(item, dict)
        and item.get("blocking")
        and item.get("resolution_strategy") == "investigate_project"
        and str(item.get("id") or "").strip()
    ]
    completed = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") in {"resolved", "deferred"}
        and str(item.get("unknown_id") or "").strip()
    }
    target_ids = {
        _normalize_unknown_id(target)
        for observation in observations
        if isinstance(observation, dict)
        and str(observation.get("id") or "").strip() in pending
        and _positive_project_observation(observation)
        for target in observation.get("target_unknown_ids", [])
    }
    return [
        str(item["id"])
        for item in candidates
        if any(_same_unknown_id(item["id"], target_id) for target_id in target_ids)
        and not any(_same_unknown_id(item["id"], done_id) for done_id in completed)
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


def _observation_mentioned(text: str, observation: dict) -> bool:
    if not text:
        return False
    haystack = text.casefold()
    path = str(observation.get("path") or "").replace("\\", "/")
    title = str(observation.get("title") or "")
    names = [path, Path(path).name if path else "", title]
    return any(name and name.casefold() in haystack for name in names)


def _require_control_reason(arguments: dict, name: str) -> None:
    if not str(arguments.get("reason") or "").strip():
        raise ValueError(f"{name} requires reason")


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
    state: InvestigationState,
    runtime: InvestigationRuntime,
) -> Iterator[dict]:
    initial_unknowns = _initial_unknowns(runtime.analysis)
    target_resolutions = [
        item
        for item in state.findings.recorded.get("resolutions", [])
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
        for item in state.findings.recorded.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") == "partially_resolved"
    }
    independently_verified_ids = {
        str(resolution.get("unknown_id") or "").strip()
        for resolution in state.findings.recorded.get("resolutions", [])
        if isinstance(resolution, dict)
        and any(
            observation.get("verification")
            for observation in state.observations.items
            if isinstance(observation, dict)
            and observation.get("id") in resolution.get("evidence", [])
        )
    }
    contract = {
        "statements": runtime.analysis.get("statements", []),
        "acceptance_criteria": runtime.analysis.get("acceptance_criteria", []),
        "constraints": runtime.analysis.get("constraint_statements", []),
        "scope": runtime.analysis.get("scope_statements", {}),
        "reference_baselines": runtime.analysis.get("reference_baselines", []),
        "unknowns": runtime.analysis.get("unknowns", []),
        "execution_mode": runtime.analysis.get("execution_mode", ""),
    }
    all_beliefs = [
        item for item in state.findings.recorded.get("beliefs", [])
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
        target_literals = _grounding_code_literals(" ".join([
            str(resolution.get("answer") or ""),
            *[
                str(belief.get("statement") or "")
                for belief in target_beliefs
            ],
        ]))
        resolution_view = dict(resolution)
        spans = _resolution_grounding_evidence_spans(
            resolution_view,
            {"beliefs": target_beliefs},
            state.observations.items,
        )
        if spans:
            resolution_view["grounding_evidence_spans"] = spans
        context = json.dumps({
            "authoritative_task_contract": contract,
            "proposed_findings": {
                "beliefs": target_beliefs,
                "resolutions": [resolution_view],
            },
            "observation_index": [
                _observation_context_view(item, target_literals)
                for item in state.observations.items
                if isinstance(item, dict)
                and str(item.get("id") or "").strip() in target_observation_ids
            ],
            "authorized_user_decisions": [
                {
                    "unknown_id": str(resolution.get("unknown_id") or "").strip(),
                    "answer": str(resolution.get("answer") or ""),
                }
                for resolution in target_resolutions
                if str(resolution.get("reason") or "") == CLEARIFY_RESOLUTION_REASON
                and str(resolution.get("answer") or "").strip()
            ],
            "required_unknown_ids": [unknown_id],
        }, ensure_ascii=False)
        cache_key = "audit:v2:" + hashlib.sha256(context.encode("utf-8")).hexdigest()
        if cache_key in state.caches.audit:
            cached_verdicts = state.caches.audit[cache_key].get("verdicts", [])
            verdicts.extend(cached_verdicts)
            yield from _quality_gate_events(runtime.run_id, unknown_id, cached_verdicts, 0)
            continue
        audit_messages = [{"role": "system", "content": prompt.build_investigation_auditor(
            app_settings.get_output_language()
        )}]
        audit = {"verdicts": []}
        for attempt in range(REQUIRED_AUDIT_ATTEMPTS):

            def ask(_path: str, slot_prompt: str) -> JSONValue:
                assistant = _call_model(runtime.provider, runtime.model, [
                    *audit_messages,
                    {"role": "user", "content": f"{slot_prompt}\ncontext: {context}"},
                ], tools=[])
                if usage := _usage_delta(runtime.pricing_rules, assistant.pop("_usage", {})):
                    _add_usage(state.usage.total, usage)
                    usage_events.append(start_event(
                        f"{runtime.run_id}-usage-investigation-audit-{len(usage_events)}",
                        "usage",
                        {"delta": usage, "total": state.usage.total},
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
        state.caches.audit[cache_key] = audit
        verdicts.extend(audit["verdicts"])
        yield from _quality_gate_events(runtime.run_id, unknown_id, audit["verdicts"], attempt)
    for event in usage_events:
        yield event
    return {"verdicts": verdicts}


def _quality_gate_events(
    run_id: str,
    unknown_id: str,
    verdicts: list[dict],
    index: int,
):
    """把语义质量门（audit）的判定结果作为事件发送给前端展示。"""
    for i, verdict in enumerate(verdicts):
        yield start_event(
            f"{run_id}-quality-gate-{unknown_id}-{index}-{i}",
            "quality_gate",
            {
                "unknown_id": str(verdict.get("unknown_id") or ""),
                "status": str(verdict.get("status") or ""),
                "reason": str(verdict.get("reason") or ""),
                "missing": verdict.get("missing") or [],
                "repair_mode": str(verdict.get("repair_mode") or ""),
                "hypothesis": str(verdict.get("hypothesis") or ""),
                "question": str(verdict.get("question") or ""),
            },
        )


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
            "missing": _semantic_missing_items(item.get("missing")),
            "repair_mode": (
                "append_missing_only"
                if str(item.get("repair_mode") or "").strip() == "append_missing_only"
                else ""
            ),
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


def _finalize_investigation(
    state: InvestigationState,
    runtime: InvestigationRuntime,
    *,
    reason: str = "Investigation needs a final structured summary.",
) -> Iterator[dict]:
    state.messages.append({"role": "user", "content": prompt.build_investigation_finalize(reason)})
    last_error = ""
    last_content = ""
    last_arguments: dict | None = None

    attempts = app_settings.get_round_limit("investigation_finalization_attempts")
    repeated_tool_error_name = ""
    repeated_tool_error_count = 0
    already_resolved_error_count = 0
    repeated_finalization_error_key = ""
    repeated_finalization_error_count = 0
    stop_finalization = False
    quality_audit: dict = {}
    best_progress = _finalization_progress_score(state.findings.recorded, state.observations.items)
    no_progress_attempts = 0
    for attempt in _round_indexes(attempts, start=0):
        thinking_id = f"{runtime.run_id}-thinking-final-{attempt}"
        yield start_event(thinking_id, "thinking", {
            "text": reason,
            "done": False,
            "open": True,
        })
        assistant = _call_model(
            runtime.provider,
            runtime.model,
            state.messages,
            tools=[_resolve_unknowns_tool_schema(), _record_findings_tool_schema(), _finish_tool_schema()],
            tool_choice="required",
        )
        if usage := _usage_delta(runtime.pricing_rules, assistant.pop("_usage", {})):
            _add_usage(state.usage.total, usage)
            yield start_event(f"{runtime.run_id}-usage-final-{attempt}", "usage", {
                "delta": usage,
                "total": state.usage.total,
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
        state.messages.append(replay)
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
                        _beliefs(state.findings.recorded.get("beliefs")),
                        state.observations.items,
                    )
                else:
                    record_arguments = _record_arguments(record_arguments)
                    _require_control_reason(record_arguments, "record_investigation_findings")
                required_resolution_ids = _unknowns_needing_resolution(
                    state.findings.recorded,
                    state.observations.items,
                    runtime.analysis,
                )
                if (
                    record_name != "resolve_unknowns"
                    and not required_resolution_ids
                    and not _has_finding_fields(record_arguments)
                ):
                    state.messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(_nothing_to_record_result(), ensure_ascii=False),
                    })
                    continue
                if record_name != "resolve_unknowns" and not _has_finding_fields(record_arguments):
                    record_arguments = yield from _record_findings_by_slots(
                        state,
                        runtime,
                        reason=str(record_arguments.get("reason") or "").strip(),
                        analysis=runtime.analysis or {},
                        required_resolution_ids=required_resolution_ids,
                    )
                    if not _has_finding_fields(record_arguments):
                        state.messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": json.dumps(_nothing_to_record_result(), ensure_ascii=False),
                        })
                        continue
                _require_finding_fields(record_arguments)
                _reject_empty_repair(record_arguments, state.findings.recorded)
                state.findings.recorded = _merge_recorded_findings(
                    state.findings.recorded,
                    record_arguments,
                )
                state.findings.recorded = _bind_grounding_evidence(
                    state.findings.recorded,
                    state.observations.items,
                )
                state.findings.recorded = _apply_direct_resolution_gate(
                    state.findings.recorded,
                    state.observations.items,
                )
                if (runtime.analysis or {}).get("_canonicalized"):
                    state.findings.last_quality_audit = yield from _audit_recorded_findings(
                        state,
                        runtime,
                    )
                    quality_audit = state.findings.last_quality_audit
                    state.findings.recorded, _, _ = _apply_investigation_audit(
                        state.findings.recorded,
                        state.findings.last_quality_audit,
                        observations=state.observations.items,
                        analysis=runtime.analysis or {},
                    )
                state.messages.append({
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
                    state.findings.recorded = _merge_recorded_findings(
                        state.findings.recorded,
                        partial_arguments,
                    )
                if repeated_tool_error_name == record_name:
                    repeated_tool_error_count += 1
                else:
                    repeated_tool_error_name = record_name
                    repeated_tool_error_count = 1
                state.messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _tool_repair_error_json(
                        exc,
                        record_name or "record_investigation_findings",
                        raw_arguments,
                        partial_arguments,
                        observations=state.observations.items,
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
                        state.findings.recorded,
                        last_arguments,
                        prefer_finish_summary=not _analysis_requests_implementation(runtime.analysis),
                    ),
                    analysis=runtime.analysis,
                    observations=state.observations.items,
                    repair_conflicts=True,
                    workspace_dir=runtime.workspace_dir,
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
                state.messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _tool_repair_error_json(
                        ValueError(last_error),
                        "finish_investigation",
                        raw_arguments,
                        partial_arguments,
                        observations=state.observations.items,
                    ),
                })
                stop_finalization = repeated_tool_error_count >= MAX_REPEATED_TOOL_ERRORS
            else:
                if quality_audit:
                    final["quality_audit"] = quality_audit
                state.messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(final, ensure_ascii=False),
                })
                if final.get("resolution_repair") and (attempts <= 0 or attempt < attempts - 1):
                    last_error = "Investigation findings need explicit resolutions before finalizing."
                    state.messages.append({
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
        progress = _finalization_progress_score(state.findings.recorded, state.observations.items)
        if progress > best_progress:
            best_progress = progress
            no_progress_attempts = 0
        else:
            no_progress_attempts += 1

        if stop_finalization:
            return _runtime_recovered_investigation(
                last_error or "Investigation finalization repeated the same invalid tool call.",
                runtime.analysis,
                state.observations.items,
                state.findings.recorded,
            )
        if repeated_finalization_error_count >= MAX_REPEATED_TOOL_ERRORS:
            return _runtime_recovered_investigation(
                last_error or "Investigation finalization repeated the same invalid response.",
                runtime.analysis,
                state.observations.items,
                state.findings.recorded,
            )
        if no_progress_attempts >= MAX_REPEATED_TOOL_ERRORS:
            return _runtime_recovered_investigation(
                last_error or "Investigation finalization made no contract progress.",
                runtime.analysis,
                state.observations.items,
                state.findings.recorded,
            )

        if attempts <= 0 or attempt < attempts - 1:
            state.messages.append({
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
                    state.findings.recorded,
                    last_arguments,
                    prefer_finish_summary=not _analysis_requests_implementation(runtime.analysis),
                ),
                analysis=runtime.analysis,
                observations=state.observations.items,
                repair_conflicts=True,
                workspace_dir=runtime.workspace_dir,
            )
        except Exception:
            pass

    return _runtime_recovered_investigation(
        last_error or "finish_investigation did not produce a usable result.",
        runtime.analysis,
        state.observations.items,
        state.findings.recorded,
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
            else "Investigation auto-recovered from tool errors; see task panel for details."
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


def _clearify_resolution(arguments: dict, answer: dict | None, state: InvestigationState, runtime: InvestigationRuntime) -> dict | None:
    resolutions = _clearify_resolutions(arguments, answer, state, runtime)
    return resolutions[0] if resolutions else None


def _clearify_resolutions(arguments: dict, answer: dict | None, state: InvestigationState, runtime: InvestigationRuntime) -> list[dict]:
    return [
        item for item in _clearify_resolution_records(arguments, answer, state, runtime)
        if item.get("status") == "resolved"
    ]


def _clearify_resolution_records(arguments: dict, answer: dict | None, state: InvestigationState, runtime: InvestigationRuntime) -> list[dict]:
    answer = answer or {}
    target_ids = _target_unknown_ids(arguments)
    if not target_ids:
        if arguments.get("orientation"):
            return []
        raise ValueError("clearify answer has no target unknown")
    classification = _classify_clearify_answer(arguments, answer, state, runtime)
    status = "resolved" if classification == "valid_answer" else "partially_resolved"
    reason = CLEARIFY_UNRESOLVED_REASON if status == "partially_resolved" else CLEARIFY_RESOLUTION_REASON
    text = _clearify_answer_text(answer)
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


def _classify_clearify_answer(arguments: dict, answer: dict, state: InvestigationState, runtime: InvestigationRuntime) -> str:
    """判定 clearify 用户答案是否为有效答案。

    优先级（结构化事件优先，零 NLP 判定）：
    1. 空文本 → non_answer（纯逻辑）
    2. action == select_option 或存在 selected_option_id（无 custom 标记）→ valid_answer（点选候选选项=明确答案）
    3. action == defer → non_answer（用户显式"我不知道/继续调查"）
    4. 自由文本（custom 或纯 response）→ LLM 语义判定；失败降级 non_answer（fail closed，不重试）
    """
    text = _clearify_answer_text(answer)
    if not text:
        return "non_answer"
    action = str(answer.get("action") or "").strip()
    if action == "select_option" or (answer.get("selected_option_id") and not answer.get("custom")):
        return "valid_answer"
    if action == "defer":
        return "non_answer"
    try:
        return _classify_clearify_answer_llm(arguments, answer, state, runtime)
    except Exception:
        return "non_answer"


def _classify_clearify_answer_llm(arguments: dict, answer: dict, state: InvestigationState, runtime: InvestigationRuntime) -> str:
    question = str(arguments.get("question") or "")
    options = arguments.get("candidate_answers") or arguments.get("options") or []
    text = _clearify_answer_text(answer)
    options_text = "\n".join(
        f"- {str(o.get('label') or o.get('value') or o) if isinstance(o, dict) else o}"
        for o in options
    ) or "(none)"
    prompt = (
        "Classify whether the user's message contains actionable information "
        "that can resolve or materially narrow the specific clarification question.\n\n"
        "valid_answer:\n"
        "- directly answers the question; or\n"
        "- provides concrete facts, constraints, preferences, corrections, "
        "partial information, or uncertainty that still materially narrows it.\n\n"
        "non_answer:\n"
        "- says they do not know / have no preference / cannot answer;\n"
        "- delegates the decision or investigation back to the agent without "
        "providing useful information;\n"
        "- is empty, irrelevant, purely conversational, or only asks a question;\n"
        "- expresses uncertainty without adding any concrete information.\n\n"
        "Important: do not classify based on trigger words such as \"don't know\", "
        "\"not sure\", \"maybe\", or \"investigate\". Consider the full semantic content. "
        "If the message both expresses uncertainty and provides useful concrete "
        "information, classify as valid_answer.\n\n"
        f"Question: {question}\n"
        f"Candidate options:\n{options_text}\n"
        f"User answer: {text}\n\n"
        "Output JSON: {\"classification\": \"valid_answer\" or \"non_answer\", \"reason\": \"<one short sentence>\"}"
    )
    messages = [{"role": "user", "content": prompt}]
    assistant = _call_model(
        runtime.provider,
        runtime.model,
        messages,
        tools=[],
    )
    usage = _usage_delta(runtime.pricing_rules, assistant.pop("_usage", {}))
    if usage:
        _add_usage(state.usage.total, usage)
    raw = _content_text(assistant.get("content"))
    match = re.search(r'"classification"\s*:\s*"([^"]+)"', raw or "")
    if not match:
        raise ValueError(f"malformed classification output: {raw!r}")
    classification = match.group(1)
    if classification not in ("valid_answer", "non_answer"):
        raise ValueError(f"unknown classification: {classification!r}")
    return classification


def _clearify_answer_text(answer: dict) -> str:
    return str(
        answer.get("response")
        or answer.get("text")
        or answer.get("selected_option_label")
        or ""
    ).strip()


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
        "Each resolution must include unknown_id, status, answer, observation_ids or belief_ids, and reason.",
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
    workspace_dir: str = "",
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
        resolutions = _drop_invalid_resolution_refs(resolutions, beliefs, observations or [], repairs, workspace_dir=workspace_dir)
    else:
        _validate_resolution_refs(resolutions, beliefs, observations or [])
    resolutions = _enforce_resolution_evidence(resolutions, initial_unknowns, strict=not repair_conflicts)
    resolutions = _strip_closed_resolution_repair_diagnostics(resolutions)
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
    bugfix_readiness_state = None
    if bugfix_reasons:
        checks = {}
        for reason in bugfix_reasons:
            field = str(reason).replace("bugfix_readiness:", "").strip()
            checks[field] = False
        bugfix_readiness_state = {
            "gate": "bugfix_readiness",
            "status": "not_ready",
            "checks": checks,
            "reasons": bugfix_reasons,
        }
    ready = readiness["ready"]
    hard_readiness_reasons = [
        reason for reason in readiness.get("reasons", [])
        if (
            reason.endswith(":not_resolved")
            or reason.endswith(":missing_evidence")
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
        "bugfix_readiness_state": bugfix_readiness_state,
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
    final["project_facts"] = _investigation_project_facts(
        beliefs,
        resolutions,
        analysis,
    )
    if repair_request:
        final["resolution_repair"] = repair_request
    return final


def _strip_submitted_repair_diagnostics(arguments: dict) -> dict:
    """record 时剥离模型提交的 repair 诊断字段（repair_mode/semantic_missing）。

    这些字段是 audit 质量门的输出标记，模型在 REPAIR 阶段会从上下文把上一轮
    的 missing 原样抄进自己提交的 resolution。若不清理，merge 后
    _semantic_repair_resolution_ids 看到 repair_mode 就永远判该 unknown 待修，
    即使模型已补齐证据，也会无限 REPAIR（U4 类死循环根因）。
    """
    cleaned = dict(arguments)
    resolutions = cleaned.get("resolutions")
    if isinstance(resolutions, list):
        cleaned["resolutions"] = [
            (
                {key: value for key, value in item.items() if key not in {"repair_mode", "semantic_missing"}}
                if isinstance(item, dict)
                else item
            )
            for item in resolutions
        ]
    return cleaned


def _strip_closed_resolution_repair_diagnostics(resolutions: list[dict]) -> list[dict]:
    cleaned = []
    for resolution in resolutions:
        if not isinstance(resolution, dict):
            continue
        item = dict(resolution)
        if item.get("status") in {"resolved", "deferred"}:
            item.pop("repair_mode", None)
            item.pop("semantic_missing", None)
        cleaned.append(item)
    return cleaned


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


def _apply_investigation_audit(
    recorded: dict,
    audit: dict,
    *,
    observations: list[dict] | None = None,
    strict_grounding: bool = True,
    allow_verification: bool = True,
    analysis: dict | None = None,
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
                _grounding_unsupported_for_resolution(
                    resolution,
                    result,
                    observations or [],
                )
                if strict_grounding
                else []
            )
            # Derived inferences may reference runtime paths and identifiers
            # that cannot appear as literals in source code (e.g. .venv/Scripts/stratumcode,
            # python.exe). Skip unsupported-literal check for derived inferences — they are
            # by definition the model's synthesis, not direct source quotes.
            # _grounding_unsupported_for_resolution 内部已同时豁免 derived_inference
            # 与 absence（否定性结论）两类。
            supporting_ids = _supporting_observation_ids(unsupported, observations or [])
            if supporting_ids:
                resolution["evidence"] = _dedupe_strings([
                    *_reference_list(resolution.get("evidence")),
                    *supporting_ids,
                ])
                unsupported = _grounding_unsupported_for_resolution(
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
                spans = _resolution_grounding_evidence_spans(
                    resolution,
                    result,
                    observations or [],
                )
                if spans:
                    resolution["grounding_evidence_spans"] = spans
                resolution["status"] = "resolved"
                resolution.pop("repair_mode", None)
                resolution.pop("semantic_missing", None)
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
                resolution.pop("repair_mode", None)
                resolution.pop("semantic_missing", None)
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
                resolution.pop("repair_mode", None)
                resolution.pop("semantic_missing", None)
                clearify_questions[unknown_id] = question
                continue
            status = "investigate"
        missing = _semantic_missing_items(verdict.get("missing"))
        original_missing = missing
        # read_only 模式下，"运行时证据"（测试/日志/复现/实际运行表现）按设计
        # 不可得。审计模型若要求这类证据，直接移除——代码路径推断 + 明确声明
        # 无运行时证据，就是该 unknown 在只读调查中的最终答案，不应进入 REPAIR。
        if missing and _analysis_is_read_only(analysis):
            missing = [
                item
                for item in missing
                if not _RUNTIME_EVIDENCE_RE.search(str(item.get("requirement") or ""))
            ]
        # 语义门禁打回时无条件进入 append_missing_only 修复：
        # 不依赖 verdict 是否带 repair_mode 字段（audit 模型常缺失该字段，
        # 缺失时旧实现 pop 掉 repair_mode，导致下一轮 repair_ids 为空、
        # 主循环误入 FINISH 分支、模型 record 被 already_resolved 拦截
        # 的三面夹击死锁——d5eef05a）。
        if missing:
            resolution["semantic_missing"] = missing
            resolution["repair_mode"] = "append_missing_only"
        else:
            resolution.pop("repair_mode", None)
            resolution.pop("semantic_missing", None)
        if missing:
            resolution["status"] = "partially_resolved"
        elif original_missing and _analysis_is_read_only(analysis):
            # 缺失要求全部是"运行时证据"类且被只读约束过滤：代码路径推断即最终答案
            resolution["status"] = "resolved"
        else:
            resolution["status"] = "partially_resolved"
        resolution["reason"] = (
            reason
            or _semantic_missing_reason(missing)
            or "The semantic audit found insufficient evidence."
        )
    result["resolutions"] = resolutions
    return result, verification_requests, clearify_questions


def _semantic_missing_reason(missing: list[dict]) -> str:
    requirements = [
        str(item.get("requirement") or "").strip()
        for item in missing
        if isinstance(item, dict) and str(item.get("requirement") or "").strip()
    ]
    return "Missing semantic requirements: " + "; ".join(requirements) if requirements else ""


def _resolution_requires_semantic_audit(resolution: dict, initial_unknowns: list[dict]) -> bool:
    if _is_user_product_decision(resolution, initial_unknowns):
        return False
    if _is_engineering_decision_resolution(resolution, initial_unknowns):
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


def _semantic_repair_resolution_ids(recorded: dict) -> set[str]:
    return {
        str(item.get("unknown_id") or "")
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and (
            item.get("repair_mode") == "append_missing_only"
            or (
                item.get("status") == "partially_resolved"
                and str(item.get("reason") or "").startswith((
                    GROUNDING_LITERAL_REASON_PREFIX,
                    STATE_WRITE_REASON_PREFIX,
                ))
            )
        )
        and str(item.get("unknown_id") or "")
    }


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


def _require_repair_resolutions(arguments: dict, repair_ids: set[str]) -> None:
    """Require a record call during active semantic repair to cover repair targets.

    When the semantic quality gate is waiting on specific unknowns, a bare
    beliefs-only record (or a record patching unrelated unknowns) makes zero
    progress on the repair list and lets the model spin forever. Force every
    record call to include a resolution for at least one pending repair id.
    """
    if not repair_ids:
        return
    provided = {
        str(item.get("unknown_id") or "").strip()
        for item in arguments.get("resolutions", [])
        if isinstance(item, dict) and str(item.get("unknown_id") or "").strip()
    }
    covered = [rid for rid in repair_ids if any(_same_unknown_id(rid, pid) for pid in provided)]
    if covered:
        return
    missing = sorted(repair_ids)
    raise ValueError(
        "semantic repair is active for unknowns: " + ", ".join(missing) +
        "; record_investigation_findings must include a repair resolution "
        "(repair_mode=append_missing_only with new belief_ids/evidence) for at "
        "least one of them before finishing."
    )


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
    if not summary:
        summary = "Investigation complete."
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


def _validate_resolution_refs(resolutions: list[dict], beliefs: list[dict], observations: list[dict]) -> None:
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    observation_refs = _observation_reference_map(observations)
    belief_by_id = {
        str(item.get("id") or "").strip(): item
        for item in beliefs
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    usable_belief_status = {"plausible", "supported", "strongly_supported", "runtime_confirmed"}
    for resolution in resolutions:
        missing_evidence = _normalize_evidence_refs(resolution, evidence_ids, observation_refs)
        if missing_evidence:
            sample_ids = sorted(evidence_ids)[:8]
            raise ValueError(
                f"resolution {resolution['unknown_id']} references unknown evidence ids: "
                + ", ".join(missing_evidence)
                + ". Evidence ids must be observation ids returned by read/glob/grep "
                "(not tool call ids)"
                + (f"; current observations: {', '.join(sample_ids)}" + ("..." if len(evidence_ids) > 8 else "") if sample_ids else "")
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


def _salvage_resolution_candidates(arguments: dict, observations: list[dict]) -> list[dict]:
    resolutions = _resolutions(arguments.get("resolutions"))
    if not resolutions:
        return []
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    observation_refs = _observation_reference_map(observations)
    salvaged = []
    for resolution in resolutions:
        if not (
            str(resolution.get("unknown_id") or "").strip()
            and (
                str(resolution.get("answer") or "").strip()
                or str(resolution.get("reason") or "").strip()
            )
        ):
            continue
        missing = _normalize_evidence_refs(resolution, evidence_ids, observation_refs)
        if missing:
            resolution["invalid_evidence_ids"] = missing
            resolution["evidence_binding"] = "unbound"
        if not resolution.get("evidence") and not resolution.get("belief_ids"):
            bound = _bind_grounding_evidence({"resolutions": [dict(resolution)]}, observations)
            resolution = bound["resolutions"][0]
        if not resolution.get("evidence") and not resolution.get("belief_ids"):
            resolution["evidence_binding"] = "unbound"
            if resolution.get("status") == "resolved":
                resolution["status"] = "partially_resolved"
                resolution["reason"] = (
                    resolution.get("reason")
                    or "Resolution answer was retained, but valid observation evidence is still unbound."
                )
        elif missing:
            resolution["evidence_binding"] = "rebound"
        salvaged.append(resolution)
    return salvaged


def _drop_invalid_resolution_refs(
    resolutions: list[dict],
    beliefs: list[dict],
    observations: list[dict],
    repairs: list[str],
    workspace_dir: str = "",
) -> list[dict]:
    file_issues = _require_file_reads(resolutions, observations, workspace_dir)
    if workspace_dir:
        lsp_issues = _require_lsp_definition_reads(resolutions, observations, workspace_dir)
        file_issues = file_issues + lsp_issues
    if file_issues:
        raise ValueError("; ".join(file_issues))
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    observation_refs = _observation_reference_map(observations)
    usable_beliefs = {
        str(item.get("id") or "").strip()
        for item in beliefs
        if isinstance(item, dict)
        and str(item.get("id") or "").strip()
        and item.get("status") in {"plausible", "supported", "strongly_supported", "runtime_confirmed"}
    }
    changed = False
    for resolution in resolutions:
        original_evidence = resolution.get("evidence", [])
        _normalize_evidence_refs(resolution, evidence_ids, observation_refs)
        evidence = resolution.get("evidence", [])
        beliefs = [item for item in resolution.get("belief_ids", []) if item in usable_beliefs]
        changed = changed or evidence != original_evidence or beliefs != resolution.get("belief_ids", [])
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


def _is_engineering_decision_resolution(
    resolution: dict,
    unknowns: list[dict],
) -> bool:
    unknown_id = str(resolution.get("unknown_id") or "").strip()
    return any(
        _same_unknown_id(item.get("id"), unknown_id)
        and item.get("type") == "engineering_decision"
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
        unknown_type = source.get("type") or "code_fact"
        if resolution.get("reason") == CLEARIFY_UNRESOLVED_REASON:
            unknown_type = "code_fact"
        question = source.get("question") or _question_from_resolution(resolution)
        unresolved.append({
            "id": resolution["unknown_id"],
            "question": question,
            "blocking": resolution["status"] in {"partially_resolved", "needs_clearify"} and bool(source.get("blocking", True)),
            "type": unknown_type,
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

def _unknown_id_for_question(question: str, unknowns: list[dict]) -> str:
    key = _question_key(question)
    for item in unknowns:
        if item.get("id") and _question_key(item.get("question", "")) == key:
            return item["id"]
    return ""


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


def _summary(final: dict) -> str:
    lines = [final.get("summary") or "Investigation complete."]
    if final.get("open_questions"):
        lines.append(f"\n{app_settings.text('summary_open_questions')}")
        lines.extend(f"- {item}" for item in final["open_questions"][:5])
    return "\n".join(lines)
