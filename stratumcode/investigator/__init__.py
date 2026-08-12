from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
from collections.abc import Callable, Iterator
from enum import Enum, StrEnum
from pathlib import Path
from typing import ParamSpec, TypeVar
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
from . import finalize as _finalize_module
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
    _investigation_task_updates,
    _observation_context_view,
    _observation_reference_map,
    _observation_refs,
    _partial_tool_arguments,
    _recorded_resolves_initial_unknowns,
    _record_arguments,
    _reference_list,
    _require_control_reason,
    _resolve_unknown_arguments,
    _semantic_missing_items,
    _semantic_repair_payload,
    _tool_call_summary,
    _unknowns_needing_resolution,
    _validate_resolution_refs,
)
from .directive import _investigation_directive
from .finalize import (
    _apply_direct_resolution_gate,
    _apply_investigation_audit,
    _audit_recorded_findings,
    _complete_resolutions,
    _finalize_investigation,
    _finish_arguments,
    _finish_payload,
    _normalize_investigation_audit,
    _resolution_repair_prompt,
    _resolution_requires_semantic_audit,
    _runtime_recovered_investigation,
)
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


_P = ParamSpec("_P")
_R = TypeVar("_R")


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


def _with_finalize_runtime_hooks(func: Callable[_P, _R]) -> Callable[_P, _R]:
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        _finalize_module._call_model = _call_model
        _finalize_module._record_findings_by_slots = _record_findings_by_slots
        return func(*args, **kwargs)

    return wrapped


_audit_recorded_findings = _with_finalize_runtime_hooks(_audit_recorded_findings)
_finalize_investigation = _with_finalize_runtime_hooks(_finalize_investigation)


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


def _record_consumes_observations(arguments: dict, observation_ids: list[str]) -> bool:
    pending = {str(item).strip() for item in observation_ids if str(item).strip()}
    if not pending:
        return False
    for field in ("beliefs", "resolutions"):
        for item in arguments.get(field, []):
            if isinstance(item, dict) and pending.intersection(_reference_list(item.get("evidence"))):
                return True
    return False


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








def _summary(final: dict) -> str:
    lines = [final.get("summary") or "Investigation complete."]
    if final.get("open_questions"):
        lines.append(f"\n{app_settings.text('summary_open_questions')}")
        lines.extend(f"- {item}" for item in final["open_questions"][:5])
    return "\n".join(lines)
