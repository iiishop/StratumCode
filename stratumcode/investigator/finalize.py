from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from uuid import uuid4

from .. import app_settings, prompt
from ..agent_runtime import (
    add_usage as _add_usage,
    assistant_visible_text as _assistant_visible_text,
    call_model as _call_model,
    content_text as _content_text,
    start_event,
    usage_delta as _usage_delta,
)
from ..json2slots import JSONValue, json2slots
from ..status.task_analysis import _analysis_requests_implementation
from ..status.task_contract import LEGACY_ASK_USER_STRATEGY
from .constants import (
    CLEARIFY_RESOLUTION_REASON,
    CLEARIFY_UNRESOLVED_REASON,
    FINDING_FIELDS,
    GROUNDING_LITERAL_REASON_PREFIX,
    MAX_REPEATED_TOOL_ERRORS,
    READ_ONLY_SUMMARY_MIN_RESOLUTION_RATIO,
    REQUIRED_AUDIT_ATTEMPTS,
    SEMANTIC_AUDIT_KINDS,
    STATE_WRITE_REASON_PREFIX,
    _RUNTIME_EVIDENCE_RE,
)
from .domain import (
    _analysis_is_read_only,
    _belief_text,
    _beliefs,
    _investigation_task_updates,
    _observation_context_view,
    _observation_reference_map,
    _partial_tool_arguments,
    _record_arguments,
    _reference_list,
    _require_control_reason,
    _resolve_unknown_arguments,
    _semantic_missing_items,
    _tool_call_summary,
    _unknowns_needing_resolution,
    _validate_resolution_refs,
)
from .evidence import (
    _bind_grounding_evidence,
    _drop_invalid_belief_refs,
    _enforce_resolution_evidence,
    _grounding_code_literals,
    _grounding_unsupported_for_resolution,
    _is_user_product_decision,
    _normalize_evidence_refs,
    _require_file_reads,
    _require_lsp_definition_reads,
    _resolution_grounding_evidence_spans,
    _supporting_observation_ids,
    _unsupported_grounding_literals,
    _validate_belief_refs,
)
from .findings import (
    _empty_recorded_findings,
    _has_finding_fields,
    _merge_recorded_findings,
    _missing_grounding_state_writes,
    _nothing_to_record_result,
    _record_findings_by_slots,
    _reject_empty_repair,
    _require_finding_fields,
    _resolutions,
)
from .ids import (
    _find_by_unknown_id,
    _initial_unknowns,
    _merge_unknowns,
    _question_key,
    _same_unknown_id,
    _unknowns,
)
from .state import InvestigationRuntime, InvestigationState
from .tools import (
    _finish_tool_schema,
    _record_findings_tool_schema,
    _resolve_unknowns_tool_schema,
    _tool_arguments,
    _tool_repair_error_json,
)
from .util import _dedupe_strings, _round_indexes, _string_list


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

