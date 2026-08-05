from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from itertools import count

from .. import app_settings, model_settings, prompt
from ..agent_runtime import (
    call_model as _runtime_call_model,
)
from ..agent_runtime import (
    content_text as _runtime_content_text,
)
from ..agent_runtime import (
    stage_progress,
)
from .session_memory import _session_sources
from .task_contract import (
    _ensure_task_contract,
    _limited_unknowns,
)

LOGGER = logging.getLogger(__name__)

TASK_INTENT_TYPES = {"feature", "bugfix", "refactor", "question", "investigation", "other"}
TASK_EXECUTION_MODES = {"implement", "read_only"}
TASK_EFFORTS = {"fast", "standard", "deep"}
TASK_RISKS = {"low", "medium", "high"}
TASK_QUALITY_GATES = {"basic", "semantic", "strict"}
TASK_CERTAINTIES = {"certain", "uncertain", "guess"}
TASK_CLUE_KINDS = {"file", "line", "symbol", "route", "other"}
DEFAULT_TASK_SLOT_ATTEMPTS = 2
TASK_CONTRACT_AUDIT_MODES = ("material_counterexample",)
IMPLEMENT_INTENT_TYPES = {"bugfix", "feature", "refactor"}
CLUE_METADATA_FIELDS = {"note", "source_ref", "source_refs", "source_excerpt"}


def _analysis_requests_implementation(analysis: dict | None) -> bool:
    mode = str((analysis or {}).get("execution_mode") or "").strip().casefold()
    if mode:
        return mode == "implement"
    return str(((analysis or {}).get("intent") or {}).get("type") or "").strip() in IMPLEMENT_INTENT_TYPES


def analyze_task_stream(
    message: str,
    context: list[str],
    workspace_dir: str,
    session_context: dict | None = None,
    *,
    progress_event_id: str = "",
    call_model=_runtime_call_model,
    content_text=_runtime_content_text,
    resolve_model=model_settings.resolve,
) -> Iterator[dict]:
    setting = (
        resolve_model(model_settings.DEFAULT_STAGE)
        or resolve_model(model_settings.EVIDENCE_STAGE)
    )
    if setting is None:
        raise ValueError(
            "No model configured for task analysis. Configure a default or evidence model in Providers."
        )

    provider = setting["provider"]
    model = setting["model_id"]
    analyzer_attempts = 0

    def tracked_call_model(
        provider_setting: dict,
        model_id: str,
        messages: list[dict],
        **kwargs: object,
    ) -> dict:
        nonlocal analyzer_attempts
        analyzer_attempts += 1
        return call_model(provider_setting, model_id, messages, **kwargs)

    analyzer_errors = []
    semantic_repairs = []
    selected_context = [item for item in context if not _workspace_snapshot_line(str(item))]
    source_catalog = _session_sources(message, selected_context, session_context)
    slot_context = selected_context
    system = {"role": "system", "content": prompt.build_task_analyzer(app_settings.get_output_language())}
    progress = []
    if progress_event_id:
        yield stage_progress(
            progress_event_id,
            progress,
            "intent_scope",
            "Task intent and scope",
            description=message,
        )
    compact_analysis, intent_slot, errors = _compact_contract_or_intent_slot(
        provider,
        model,
        tracked_call_model,
        content_text,
        [
            system,
            {"role": "user", "content": prompt.build_task_compact_contract_user(
                message=message,
                directory=workspace_dir,
                context=slot_context,
                source_catalog=source_catalog,
            )},
        ],
        message=message,
        context=selected_context,
        source_catalog=source_catalog,
    )
    analyzer_errors.extend(errors)
    if compact_analysis is not None:
        analysis = compact_analysis
        _sanitize_optional_contract(analysis)
        analysis["model"] = model
        analysis["provider"] = provider["name"]
        analysis["analyzer_attempts"] = analyzer_attempts
        analysis["evidence_hypothesis"] = _analysis_hypothesis(message, analysis)
        if progress_event_id:
            yield stage_progress(
                progress_event_id,
                progress,
                "intent_scope",
                "Task intent and scope",
                description=message,
                state="done",
            )
            yield stage_progress(
                progress_event_id,
                progress,
                "acceptance_contract",
                "Acceptance contract",
                description=str(analysis.get("intent", {}).get("summary") or message),
                detail=f"{len(analysis.get('acceptance_criteria', []))} acceptance criteria",
                state="done",
            )
            yield stage_progress(
                progress_event_id,
                progress,
                "unknowns",
                "Investigation unknowns",
                description="Identify facts and decisions that still require verification.",
                detail=f"{len(analysis.get('unknowns', []))} unknowns",
                state="done",
            )
        return analysis
    if intent_slot is None:
        intent_slot, errors = _task_slot_json(
            provider,
            model,
            tracked_call_model,
            content_text,
            [
                system,
                {"role": "user", "content": prompt.build_task_intent_slot_user(
                    message=message,
                    directory=workspace_dir,
                    context=slot_context,
                    source_catalog=source_catalog,
                )},
            ],
            "intent_scope",
            required=_intent_summary_present,
        )
        analyzer_errors.extend(errors)
    canonical_intent = _canonical_analysis(
        message, selected_context, source_catalog, intent_slot or {}, {}, {}
    )
    # 部分恢复：compact 输出可能含 acceptance/unknowns（即使整体不 ready）——
    # 提取出来避免重跑完整 slot 流程（compact data 本身是合法 slot 结构）
    partial_acceptance = None
    partial_unknowns = None
    if intent_slot:
        if isinstance(intent_slot.get("acceptance_criteria"), list) and intent_slot["acceptance_criteria"]:
            partial_acceptance = intent_slot
        if isinstance(intent_slot.get("unknowns"), list) and intent_slot["unknowns"]:
            partial_unknowns = intent_slot
    if progress_event_id:
        yield stage_progress(
            progress_event_id,
            progress,
            "intent_scope",
            "Task intent and scope",
            description=message,
            detail=f"{len(canonical_intent.get('requirements', []))} requirements",
            state="done",
        )
    acceptance_slot = _conditional_bugfix_acceptance(canonical_intent)
    acceptance_recovered = False
    if progress_event_id:
        yield stage_progress(
            progress_event_id,
            progress,
            "acceptance_contract",
            "Acceptance contract",
            description=str(canonical_intent.get("intent", {}).get("summary") or message),
        )
    if acceptance_slot is None and partial_acceptance is None:
        acceptance_slot, errors = _task_slot_json(
            provider=provider,
            model=model,
            call_model=tracked_call_model,
            content_text=content_text,
            messages=[
                system,
                {"role": "user", "content": prompt.build_task_acceptance_slot_user(
                    message=message,
                    directory=workspace_dir,
                    context=slot_context,
                    intent_slot=_intent_slot_payload(canonical_intent),
                    source_catalog=source_catalog,
                )},
            ],
            label="acceptance_contract",
            required=lambda data: _acceptance_contract_validation(
                provider=provider,
                model=model,
                call_model=tracked_call_model,
                content_text=content_text,
                data=data,
                message=message,
                source_catalog=source_catalog,
                canonical_intent=canonical_intent,
                repair_warnings=semantic_repairs,
            ),
        )
        analyzer_errors.extend(errors)
        acceptance_recovered = acceptance_slot is None
        if acceptance_recovered:
            acceptance_slot = _requirement_acceptance_fallback(canonical_intent)
    canonical_acceptance = _canonical_analysis(
        message, selected_context, source_catalog, intent_slot or {}, partial_acceptance or acceptance_slot or {}, {}
    )
    acceptance_slots = canonical_acceptance["acceptance_criteria"]
    if progress_event_id:
        yield stage_progress(
            progress_event_id,
            progress,
            "acceptance_contract",
            "Acceptance contract",
            description=str(canonical_intent.get("intent", {}).get("summary") or message),
            detail=f"{len(acceptance_slots)} acceptance criteria",
            state="done",
        )
        yield stage_progress(
            progress_event_id,
            progress,
            "unknowns",
            "Investigation unknowns",
            description="Identify facts and decisions that still require verification.",
        )
    if partial_unknowns is not None:
        unknowns_slot = partial_unknowns
        unknowns_errors: list[str] = []
    else:
        unknowns_slot, errors = _task_slot_json(
            provider,
            model,
            tracked_call_model,
            content_text,
            [
                system,
                {"role": "user", "content": prompt.build_task_unknowns_slot_user(
                    message=message,
                    directory=workspace_dir,
                    context=slot_context,
                    intent_slot=_contract_slot_payload(canonical_acceptance),
                    acceptance_slots=acceptance_slots,
                    source_catalog=source_catalog,
                )},
            ],
            "unknowns",
        )
        unknowns_errors = errors
    analyzer_errors.extend(unknowns_errors)
    minimal_recovery_error = ""
    try:
        analysis = _canonical_analysis(
            message,
            selected_context,
            source_catalog,
            intent_slot or {},
            acceptance_slot or {},
            unknowns_slot or {},
        )
    except ValueError as exc:
        analyzer_errors.append(str(exc))
        minimal_recovery_error = str(exc)
        analysis = _minimal_task_analysis(message, context)
    _sanitize_optional_contract(analysis)
    if semantic_repairs:
        analysis["analyzer_warnings"] = (
            list(analysis.get("analyzer_warnings", []))
            + semantic_repairs
        )
    analysis["model"] = model
    analysis["provider"] = provider["name"]
    analysis["analyzer_attempts"] = analyzer_attempts
    partial_recovery = intent_slot is None or acceptance_recovered or unknowns_slot is None
    if analyzer_errors:
        analysis["analyzer_warnings"] = (
            list(analysis.get("analyzer_warnings", []))
            + _summarize_analyzer_errors(analyzer_errors)
        )
    if partial_recovery:
        analysis["recovered_from_partial_analyzer_output"] = True
    if minimal_recovery_error:
        analysis["analyzer_errors"] = [minimal_recovery_error]
        analysis["analyzer_error"] = "minimal recovery: task analyzer slot recovery used runtime defaults for invalid or missing content"
    analysis["evidence_hypothesis"] = _analysis_hypothesis(message, analysis)
    if progress_event_id:
        yield stage_progress(
            progress_event_id,
            progress,
            "unknowns",
            "Investigation unknowns",
            description="Identify facts and decisions that still require verification.",
            detail=f"{len(analysis.get('unknowns', []))} unknowns",
            state="done",
        )
    return analysis


def analyze_task(
    message: str,
    context: list[str],
    workspace_dir: str,
    session_context: dict | None = None,
    *,
    call_model=_runtime_call_model,
    content_text=_runtime_content_text,
    resolve_model=model_settings.resolve,
) -> dict:
    stream = analyze_task_stream(
        message,
        context,
        workspace_dir,
        session_context,
        call_model=call_model,
        content_text=content_text,
        resolve_model=resolve_model,
    )
    while True:
        try:
            next(stream)
        except StopIteration as stopped:
            return stopped.value


def _compact_contract_or_intent_slot(
    provider: dict,
    model: str,
    call_model,
    content_text,
    messages: list[dict],
    *,
    message: str,
    context: list[str],
    source_catalog: list[dict],
) -> tuple[dict | None, dict | None, list[str]]:
    assistant = call_model(provider, model, messages, tools=[])
    raw = content_text(assistant.get("content"))
    try:
        if assistant.get("tool_calls"):
            raise ValueError("tool calls are not allowed")
        data = _json_object(raw)
    except ValueError as exc:
        return None, None, [f"compact_contract: {exc}"]
    try:
        analysis = _validate_task_analysis({
            "origin_message": message,
            "source_catalog": source_catalog,
            **data,
        })
        if _compact_contract_ready(analysis):
            analysis["compact_analyzer"] = True
            return analysis, None, []
    except ValueError as exc:
        return None, data if _intent_summary_present(data) else None, [f"compact_contract: {exc}"]
    return None, data if _intent_summary_present(data) else None, []


def _compact_contract_ready(analysis: dict) -> bool:
    if analysis.get("effort") == "deep":
        return False
    return bool(
        analysis.get("intent", {}).get("summary")
        and analysis.get("acceptance_criteria")
    )


def _task_slot_json(
    provider: dict,
    model: str,
    call_model,
    content_text,
    messages: list[dict],
    label: str,
    *,
    required=None,
) -> tuple[dict | None, list[str]]:
    errors = []
    last_invalid_key = ""
    repeated_invalid = 0
    attempts = app_settings.get_round_limit("task_analyzer_attempts") or DEFAULT_TASK_SLOT_ATTEMPTS
    for attempt in _attempt_indexes(attempts):
        assistant = call_model(provider, model, messages, tools=[])
        raw = content_text(assistant.get("content"))
        try:
            if assistant.get("tool_calls"):
                raise ValueError("tool calls are not allowed")
            data = _json_object(raw)
            nested = data.get(label)
            if isinstance(nested, dict):
                data = nested
            validation = required(data) if required else True
            if validation is not True:
                raise ValueError(
                    validation
                    if isinstance(validation, str) and validation
                    else "required slot fields are missing"
                )
            return data, errors
        except ValueError as exc:
            error = f"{label}: {exc}"
            errors.append(error)
            LOGGER.info(
                "task analyzer slot candidate rejected",
                extra={
                    "slot": label,
                    "attempt": attempt,
                    "error": str(exc),
                    "finish_reason": assistant.get("finish_reason"),
                    "raw_excerpt": raw[:4000],
                },
            )
            invalid_key = f"{raw[:1000]}::{exc}"
            repeated_invalid = repeated_invalid + 1 if invalid_key == last_invalid_key else 1
            last_invalid_key = invalid_key
            messages.extend([
                {"role": "assistant", "content": raw[:4000]},
                {"role": "user", "content": prompt.retry_json_instruction(
                    exc,
                    kind="output_contract",
                    forbidden="final task-analysis ids, schema wrappers, or Markdown",
                )},
            ])
            if repeated_invalid >= DEFAULT_TASK_SLOT_ATTEMPTS:
                return None, errors
    return None, errors


def _summarize_analyzer_errors(errors: list[str]) -> list[str]:
    grouped: dict[str, list[str]] = {}
    for error in errors:
        label, separator, detail = str(error).partition(": ")
        grouped.setdefault(label if separator else "task_analyzer", []).append(
            detail if separator else str(error)
        )
    return [
        (
            f"{label}: {items[0]}"
            if len(items) == 1
            else f"{label}: recovered after {len(items)} rejected attempts; last issue: {items[-1]}"
        )
        for label, items in grouped.items()
    ]


def _canonical_analysis(
    message: str,
    context: list[str],
    source_catalog: list[dict],
    intent_slot: dict,
    acceptance_slot: dict,
    unknowns_slot: dict,
) -> dict:
    data = _analysis_from_slots(message, context, intent_slot, acceptance_slot, unknowns_slot)
    data["origin_message"] = message
    data["source_catalog"] = source_catalog
    return _validate_task_analysis(data)


def _intent_summary_present(data: dict) -> bool:
    nested = data.get("intent")
    intent: dict = nested if isinstance(nested, dict) else data
    return bool(str(intent.get("summary") or "").strip())


def _intent_slot_payload(analysis: dict) -> dict:
    return {
        "intent": analysis.get("intent", {}),
        "execution_mode": analysis.get("execution_mode", "read_only"),
        "effort": analysis.get("effort", "standard"),
        "risk": analysis.get("risk", "medium"),
        "quality_gate": analysis.get("quality_gate", app_settings.get_effort_profile(analysis.get("effort"))["quality_gate"]),
        "requirements": analysis.get("requirements", []),
        "constraints": analysis.get("constraint_statements", []),
        "hypotheses": [
            item for item in analysis.get("hypotheses", [])
            if item.get("certainty") != "guess"
        ],
        "clues": analysis.get("clues", []),
        "reference_baselines": analysis.get("reference_baselines", []),
        "investigation_targets": analysis.get("investigation_targets", []),
    }


def _contract_slot_payload(analysis: dict) -> dict:
    return {
        **_intent_slot_payload(analysis),
        "acceptance_criteria": analysis.get("acceptance_criteria", []),
        "behavior_contract": analysis.get("behavior_contract", {}),
        "scope": analysis.get("scope", {}),
    }


def _requirement_acceptance_fallback(analysis: dict) -> dict:
    criteria = [
        {
            "text": str(item.get("text") or "").strip(),
            "authority": "derived",
            "derived_from": [str(item.get("id") or "").strip()],
        }
        for item in analysis.get("requirements", [])
        if isinstance(item, dict)
        and str(item.get("text") or "").strip()
        and str(item.get("id") or "").strip()
    ]
    return {"acceptance_criteria": criteria}


def _conditional_bugfix_acceptance(analysis: dict) -> dict | None:
    if analysis.get("intent", {}).get("type") != "bugfix":
        return None
    hypotheses = [
        str(item.get("text") or "").strip()
        for item in analysis.get("hypotheses", [])
        if item.get("certainty") == "uncertain"
        and str(item.get("text") or "").strip()
    ]
    if not hypotheses:
        return None
    language = app_settings.get_output_language()
    target = hypotheses[0]
    conditional = {
        "zh": f"如果调查确认用户所述问题（{target}），修复后该问题不再发生。",
        "ja": f"調査でユーザーの申告（{target}）が確認された場合、修正後はその問題が再発しない。",
    }.get(
        language,
        f"If Investigation confirms the reported issue ({target}), the fix prevents it from recurring.",
    )
    requirements = analysis.get("requirements", [])
    if not requirements:
        return None
    criteria = [{
        "text": conditional,
        "authority": "derived",
        "derived_from": [str(requirements[0].get("id") or "REQ1")],
    }]
    criteria.extend(
        {
            "text": str(item.get("text") or "").strip(),
            "authority": "derived",
            "derived_from": [str(item.get("id") or "")],
        }
        for item in requirements[1:]
        if str(item.get("text") or "").strip()
        and str(item.get("id") or "").strip()
    )
    return {"acceptance_criteria": criteria}


def _analysis_from_slots(message: str, context: list[str], intent_slot: dict, acceptance_slot: dict, unknowns_slot: dict) -> dict:
    fallback = _fallback_task_analysis(message, context)
    nested_intent = intent_slot.get("intent")
    intent_data: dict = nested_intent if isinstance(nested_intent, dict) else intent_slot
    intent_meta = intent_slot
    acceptance_data = acceptance_slot or {}
    unknown_data = unknowns_slot or {}
    intent_type = str(intent_data.get("intent_type") or intent_data.get("type") or fallback["intent"]["type"]).strip().casefold()
    if intent_type not in TASK_INTENT_TYPES:
        intent_type = fallback["intent"]["type"]
    execution_mode = str(
        intent_data.get("execution_mode")
        or intent_meta.get("execution_mode")
        or ""
    ).strip().casefold()
    execution_mode_recovered = execution_mode not in TASK_EXECUTION_MODES
    if execution_mode_recovered:
        execution_mode = "read_only"
    effort = str(intent_data.get("effort") or intent_meta.get("effort") or "standard").strip().casefold()
    if effort not in TASK_EFFORTS:
        effort = "standard"
    risk = str(intent_data.get("risk") or intent_meta.get("risk") or "medium").strip().casefold()
    if risk not in TASK_RISKS:
        risk = "medium"
    quality_gate = str(
        intent_data.get("quality_gate")
        or intent_meta.get("quality_gate")
        or app_settings.get_effort_profile(effort)["quality_gate"]
    ).strip().casefold()
    if quality_gate not in TASK_QUALITY_GATES:
        quality_gate = app_settings.get_effort_profile(effort)["quality_gate"]
    summary = str(intent_data.get("summary") or fallback["intent"]["summary"]).strip()
    acceptance = _runtime_acceptance_slots(acceptance_data, message, context)
    unknowns = _runtime_unknowns(unknown_data, acceptance, fallback, effort=effort)
    data = {
        "intent": {"type": intent_type, "summary": summary},
        "execution_mode": execution_mode,
        "effort": effort,
        "risk": risk,
        "quality_gate": quality_gate,
        "requirements": intent_meta.get("requirements", []),
        "acceptance_criteria": acceptance,
        "behavior_contract": _slot_behavior_contract(acceptance_data, fallback),
        "constraints": _slot_string_list(intent_meta.get("constraints"), fallback["constraints"]),
        "scope": _slot_scope(acceptance_data, fallback),
        "hypotheses": _slot_hypotheses(intent_meta.get("hypotheses")),
        "clues": _unique_clues(_slot_clues(intent_meta.get("clues")) + fallback["clues"]),
        "reference_baselines": intent_meta.get("reference_baselines", []),
        "investigation_targets": intent_meta.get("investigation_targets", []),
        "unknowns": unknowns,
    }
    if execution_mode_recovered:
        data["analyzer_warnings"] = [
            "intent_scope: missing or invalid execution_mode; defaulted to read_only"
        ]
    return data


def _runtime_acceptance_slots(
    data: dict | None,
    message: str,
    context: list[str],
    *,
    use_fallback: bool = True,
) -> list[dict]:
    fallback = _fallback_task_analysis(message, context)["acceptance_criteria"]
    value = (data or {}).get("acceptance_criteria")
    if value is None:
        value = (data or {}).get("acceptance")
    items = []
    if isinstance(value, list):
        for raw in value:
            text = str(raw.get("text") or raw.get("description") or "") if isinstance(raw, dict) else str(raw)
            text = text.strip()
            if text:
                item = {"id": f"AC{len(items) + 1}", "text": text}
                if isinstance(raw, dict):
                    for field in ("authority", "source_ref", "source_refs", "source_excerpt", "derived_from"):
                        if field in raw:
                            item[field] = raw[field]
                items.append(item)
    return items or (fallback if use_fallback else [])


def _canonical_acceptance_present(
    data: dict,
    message: str,
    source_catalog: list[dict],
    canonical_intent: dict,
) -> bool:
    acceptance = _runtime_acceptance_slots(data, message, [], use_fallback=False)
    if not acceptance:
        return False
    probe = {
        "origin_message": message,
        "source_catalog": source_catalog,
        "intent": canonical_intent.get("intent", {}),
        "reference_baselines": canonical_intent.get("reference_baselines", []),
        "acceptance_criteria": acceptance,
        "behavior_contract": data.get("behavior_contract", {}),
        "scope": data.get("scope", {}),
        "unknowns": [],
    }
    return bool(_ensure_task_contract(probe).get("acceptance_criteria"))


def _acceptance_contract_validation(
    *,
    provider: dict,
    model: str,
    call_model,
    content_text,
    data: dict,
    message: str,
    source_catalog: list[dict],
    canonical_intent: dict,
    repair_warnings: list[str],
) -> bool | str:
    if not _canonical_acceptance_present(
        data,
        message,
        source_catalog,
        canonical_intent,
    ):
        return "required acceptance contract fields are missing"
    candidate = _canonical_analysis(
        message,
        [],
        source_catalog,
        _intent_slot_payload(canonical_intent),
        data,
        {},
    )
    return _semantic_contract_audit(
        provider=provider,
        model=model,
        call_model=call_model,
        content_text=content_text,
        payload={
            "source_catalog": source_catalog,
            "requirements": canonical_intent.get("requirements", []),
            "reference_baselines": canonical_intent.get("reference_baselines", []),
            "candidate_contract": {
                "acceptance_criteria": candidate.get("acceptance_criteria", []),
                "behavior_contract": candidate.get("behavior_contract", {}),
                "scope": candidate.get("scope", {}),
            },
        },
        removable_contract=data,
        repair_requirements=canonical_intent.get("requirements", []),
        repair_warnings=repair_warnings,
    )


def _semantic_contract_audit(
    *,
    provider: dict,
    model: str,
    call_model,
    content_text,
    payload: dict,
    removable_contract: dict | None = None,
    repair_requirements: list[dict] | None = None,
    repair_warnings: list[str] | None = None,
) -> bool | str:
    equivalent, issues, error = _task_contract_audit(
        provider=provider,
        model=model,
        call_model=call_model,
        content_text=content_text,
        payload=payload,
    )
    if error:
        return error
    if equivalent and not issues:
        return True
    if removable_contract is not None:
        issues = _repair_contract_differences(
            removable_contract,
            issues,
            repair_requirements or [],
            repair_warnings if repair_warnings is not None else [],
        )
        if not issues:
            return True
    reasons = [
        f"{item.get('path') or 'contract'}: {item.get('reason') or 'semantic drift'}"
        for item in issues
        if isinstance(item, dict)
    ]
    return "semantic contract drift: " + (
        "; ".join(reasons) or "auditor rejected the derived contract"
    )


def _repair_contract_differences(
    contract: dict,
    issues: list[dict],
    requirements: list[dict],
    warnings: list[str],
) -> list[dict]:
    removable = re.compile(
        r"^(behavior_contract\.(?:inputs|outputs|success_behaviors)"
        r"|scope\.(?:in|undecided))(?:\[(\d+)\])?$"
    )
    removals: dict[str, set[int] | None] = {}
    acceptance_issues: dict[int, dict] = {}
    remaining = []
    for issue in issues:
        path = str(issue.get("path") or "") if isinstance(issue, dict) else ""
        match = removable.fullmatch(path)
        acceptance_match = re.fullmatch(r"acceptance_criteria\[(\d+)\]", path)
        if acceptance_match and isinstance(issue, dict):
            acceptance_issues[int(acceptance_match.group(1))] = issue
            continue
        if not match:
            remaining.append(issue)
            continue
        root, raw_index = match.groups()
        if raw_index is None:
            removals[root] = None
        elif root not in removals:
            removals[root] = {int(raw_index)}
        elif (root_indexes := removals[root]) is not None:
            root_indexes.add(int(raw_index))
    for root, indexes in removals.items():
        section, field = root.split(".", 1)
        parent = contract.get(section)
        if not isinstance(parent, dict):
            continue
        values = parent.get(field)
        if indexes is None:
            parent[field] = []
        elif isinstance(values, list):
            parent[field] = [
                item for index, item in enumerate(values)
                if index not in indexes
            ]
        warnings.append(
            f"acceptance_contract: removed unsupported {root} after semantic review"
        )
    criteria = contract.get("acceptance_criteria")
    requirement_by_id = {
        str(item.get("id") or ""): item
        for item in requirements
        if isinstance(item, dict) and item.get("id")
    }
    repaired_indexes = set()
    if isinstance(criteria, list):
        repaired_criteria = []
        for index, criterion in enumerate(criteria):
            issue = acceptance_issues.get(index)
            if issue is None or not isinstance(criterion, dict):
                repaired_criteria.append(criterion)
                continue
            derived_ids = [
                str(item)
                for item in criterion.get("derived_from", [])
                if str(item) in requirement_by_id
            ]
            replacements = [
                {
                    "text": requirement_by_id[item]["text"],
                    "authority": "derived",
                    "derived_from": [item],
                }
                for item in derived_ids
            ]
            if not replacements:
                repaired_criteria.append(criterion)
                continue
            repaired_criteria.extend(replacements)
            repaired_indexes.add(index)
            warnings.append(
                "acceptance_contract: replaced unsupported "
                f"acceptance_criteria[{index}] with canonical requirement"
            )
        contract["acceptance_criteria"] = repaired_criteria
    remaining.extend(
        issue
        for index, issue in acceptance_issues.items()
        if index not in repaired_indexes
    )
    return remaining


def _sanitize_optional_contract(analysis: dict) -> None:
    hypotheses = analysis.get("hypotheses", [])
    warnings = analysis.setdefault("analyzer_warnings", [])
    analysis["hypotheses"] = [
        item for item in hypotheses
        if item.get("certainty") != "guess"
    ]
    for index, item in enumerate(hypotheses):
        if item.get("certainty") == "guess":
            warnings.append(
                f"hypotheses[{index}]: removed because no authoritative source supports it"
            )


def _task_contract_audit(
    *,
    provider: dict,
    model: str,
    call_model,
    content_text,
    payload: dict,
) -> tuple[bool, list[dict], str]:
    assistant = call_model(
        provider,
        model,
        [
            {
                "role": "system",
                "content": prompt.build_task_contract_auditor(
                    app_settings.get_output_language()
                ),
            },
            {"role": "user", "content": json.dumps({
                "audit_modes": list(TASK_CONTRACT_AUDIT_MODES),
                **payload,
            }, ensure_ascii=False)},
        ],
        tools=[],
    )
    try:
        audit = _json_object(content_text(assistant.get("content")))
    except ValueError as exc:
        return False, [], f"semantic contract audit was invalid: {exc}"
    issues = audit.get("differences", audit.get("issues"))
    issues = issues if isinstance(issues, list) else []
    equivalent = audit.get("equivalent", audit.get("valid"))
    return equivalent is True, issues, ""


def _runtime_unknowns(
    data: dict,
    acceptance: list[dict],
    fallback: dict,
    *,
    effort: str = "standard",
) -> list[dict]:
    raw = data.get("unknown_content")
    if raw is None:
        raw = data.get("unknowns")
    if not isinstance(raw, list):
        return fallback["unknowns"]
    criteria_ids = [item["id"] for item in acceptance]
    items = []
    for raw_item in raw[:5]:
        if isinstance(raw_item, dict):
            question = str(raw_item.get("question") or raw_item.get("text") or raw_item.get("description") or "").strip()
            if not question:
                continue
            blocking = bool(raw_item.get("blocking", True))
            unknown_type = str(raw_item.get("type") or "code_fact").strip().casefold()
            requested_strategy = str(
                raw_item.get("resolution_strategy") or "investigate_project"
            ).strip().casefold()
            if unknown_type == "product_decision":
                blocking = True
                strategy = "clearify"
            else:
                strategy = (
                    "deferred"
                    if requested_strategy == "deferred" or not blocking
                    else "investigate_project"
                )
            items.append({
                "id": f"U{len(items) + 1}",
                "question": question,
                "blocking": blocking,
                "type": unknown_type,
                "why": str(raw_item.get("why") or raw_item.get("reason") or "").strip(),
                "resolution_strategy": strategy,
                "acceptance_criteria_ids": _slot_ids(raw_item.get("acceptance_slots"), criteria_ids),
            })
        else:
            question = str(raw_item).strip()
            if question:
                items.append(question)
    try:
        return _limited_unknowns(items, acceptance, effort)
    except ValueError:
        return fallback["unknowns"]


def _slot_ids(value, ids: list[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for raw in value:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= index <= len(ids):
            result.append(ids[index - 1])
    return result


def _slot_behavior_contract(data: dict, fallback: dict) -> dict:
    if "behavior_contract" not in data:
        return fallback["behavior_contract"]
    value = data.get("behavior_contract")
    return value if isinstance(value, dict) else {}


def _slot_scope(data: dict, fallback: dict) -> dict:
    value = data.get("scope")
    return value if isinstance(value, dict) else fallback["scope"]


def _slot_string_list(value, fallback: list[str]) -> list[str]:
    return value if isinstance(value, list) else fallback


def _slot_hypotheses(value) -> list[dict]:
    try:
        return _hypotheses(value)
    except ValueError:
        return []


def _slot_clues(value) -> list[dict]:
    try:
        return _clues(value)
    except ValueError:
        return []


def _attempt_indexes(limit: int, start: int = 1):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


def _json_object(raw: str) -> dict:
    text = (raw or "").strip()
    candidates = _json_candidates(text)
    if not candidates:
        raise ValueError("response is not a JSON object")
    errors = []
    for candidate in candidates:
        for body in (candidate, _repair_jsonish(candidate)):
            try:
                data, _ = json.JSONDecoder().raw_decode(body)
                if not isinstance(data, dict):
                    raise ValueError("top-level JSON must be an object")
                return data
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append(str(exc))
    raise ValueError(f"invalid JSON: {errors[-1]}")


def _json_candidates(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    candidates = []
    candidates.extend(match.group(1).strip() for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE))
    candidates.append(text)
    for index, char in enumerate(text):
        if char in "{[":
            candidates.append(text[index:])
    candidates.extend(text[match.start():] for match in re.finditer(r"\bnull\b", text))
    result = []
    seen = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result


def _repair_jsonish(text: str) -> str:
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = re.sub(r":\s*([,}\]])", r": null\1", text)
    return text


def _minimal_task_analysis(message: str, context: list[str], raw: str = "") -> dict:
    result = _fallback_task_analysis(message, context)
    request = " ".join(str(message or "").split()).strip()
    raw_text = " ".join(str(raw or "").split()).strip()
    summary = _sentence_from_raw(raw_text) or request[:160] or result["intent"]["summary"]
    result["intent"] = {"type": "other", "summary": summary}
    result["execution_mode"] = "read_only"
    result["acceptance_criteria"] = [{"id": "AC1", "text": request[:220] or summary}]
    result["unknowns"] = [{
        "id": "U1",
        "question": _implementation_unknown(request or summary),
        "blocking": True,
        "type": "code_fact",
        "why": "Patch planning needs the exact code path and project convention for this requested behavior.",
        "resolution_strategy": "investigate_project",
        "acceptance_criteria_ids": ["AC1"],
    }]
    result["recovered_from_minimal_analyzer_output"] = True
    return result


def _sentence_from_raw(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[{}\[\]\"']", " ", text)
    parts = [part.strip(" :-") for part in re.split(r"[\u3002.!?\n\r]+", text) if part.strip(" :-")]
    return next((part[:160] for part in parts if len(part) >= 8), "")


def _implementation_unknown(request: str) -> str:
    target = request[:120] or "the requested behavior"
    return f"Which existing code path controls this behavior: {target}?"


def _fallback_task_analysis(message: str, context: list[str]) -> dict:
    text = " ".join(str(message or "").split()).strip()
    clues = _fallback_clues(context)
    return {
        "intent": {"type": "other", "summary": text[:160] or "Handle the user request."},
        "execution_mode": "read_only",
        "acceptance_criteria": [
            {"id": "AC1", "text": text[:220] or "The requested behavior is completed."},
        ],
        "behavior_contract": {
            "inputs": [],
            "outputs": [],
            "success_behaviors": [],
            "failure_behaviors": [],
            "boundaries": [],
        },
        "constraints": [],
        "scope": {"in": [text[:220] or "Requested work"], "out": [], "undecided": []},
        "hypotheses": [],
        "clues": clues,
        "unknowns": [
            {
                "id": "U1",
                "question": _implementation_unknown(text),
                "blocking": True,
                "type": "code_fact",
                "why": "Implementation or answer must be grounded in the current workspace.",
                "resolution_strategy": "investigate_project",
                "acceptance_criteria_ids": ["AC1"],
            }
        ],
    }


def _fallback_clues(context: list[str]) -> list[dict]:
    clues = []
    for item in context or []:
        raw = str(item)
        value = raw.strip()
        if value and not _workspace_snapshot_line(raw):
            clues.append({"kind": "file", "value": value, "path": value, "line": 0, "symbol": "", "note": "user-provided context"})
    return clues


def _workspace_snapshot_line(value: str) -> bool:
    raw = value.rstrip()
    text = raw.strip()
    if text == "Workspace snapshot:":
        return True
    if text.startswith(("- root:", "- visible files:", "- visible directories:", "- files:")):
        return True
    return raw.startswith("  - ") and bool(re.search(r"\(\d+ bytes(?: / empty)?\)$", text))


def _validate_task_analysis(data: dict) -> dict:
    intent = data.get("intent")
    if not isinstance(intent, dict):
        raise ValueError("intent must be an object")
    intent_type = str(intent.get("type") or "other").strip().casefold()
    summary = str(intent.get("summary") or "").strip()
    if intent_type not in TASK_INTENT_TYPES:
        intent_type = "other"
    if not summary:
        raise ValueError("intent.summary is required")

    result = dict(data)
    result["intent"] = {"type": intent_type, "summary": summary}
    execution_mode = str(data.get("execution_mode") or "").strip().casefold()
    result["execution_mode"] = (
        execution_mode
        if execution_mode in TASK_EXECUTION_MODES
        else "read_only"
    )
    result["hypotheses"] = _optional_field(lambda: _hypotheses(data.get("hypotheses")), [])
    result["clues"] = _optional_field(lambda: _clues(data.get("clues")), [])
    result = _ensure_task_contract(result)
    _merge_input_output_acceptance(result)
    _normalize_execution_mode(result)
    return result


def _normalize_execution_mode(analysis: dict) -> None:
    if analysis.get("execution_mode") != "read_only":
        return
    if analysis.get("intent", {}).get("type") not in IMPLEMENT_INTENT_TYPES:
        return
    if not analysis.get("acceptance_criteria"):
        return
    analysis["execution_mode"] = "implement"
    analysis.setdefault("analyzer_warnings", []).append(
        "intent_scope: read_only conflicts with implementation intent and acceptance criteria; changed to implement"
    )


def _merge_input_output_acceptance(analysis: dict) -> None:
    criteria = [
        item for item in analysis.get("acceptance_criteria", [])
        if isinstance(item, dict)
    ]
    if len(criteria) < 2:
        return
    input_items = [item for item in criteria if _is_io_acceptance(item.get("text"), "input")]
    output_items = [item for item in criteria if _is_io_acceptance(item.get("text"), "output")]
    if not input_items or not output_items or len(input_items) + len(output_items) != len(criteria):
        return
    input_text = "；".join(str(item.get("text") or "").strip() for item in input_items)
    output_text = "；".join(str(item.get("text") or "").strip() for item in output_items)
    text = (
        f"当{input_text}时，{output_text}。"
        if _contains_cjk(input_text + output_text)
        else f"Given {input_text}, {output_text}."
    )
    merged = {
        "id": "AC1",
        "text": text,
        "authority": "derived",
        "source_refs": [],
        "derived_from": sorted({
            raw
            for item in criteria
            for raw in (item.get("derived_from") or [])
            if str(raw).strip()
        }),
    }
    analysis["acceptance_criteria"] = [merged]
    for unknown in analysis.get("unknowns", []):
        if isinstance(unknown, dict) and unknown.get("acceptance_criteria_ids"):
            unknown["acceptance_criteria_ids"] = ["AC1"]
    statements = []
    for item in analysis.get("statements", []):
        if not (
            isinstance(item, dict)
            and str(item.get("id") or "").startswith("AC")
        ):
            statements.append(item)
    analysis["statements"] = [*statements, merged]
    analysis.setdefault("analyzer_warnings", []).append(
        "acceptance_contract: merged input/output fragments into one observable criterion"
    )


def _is_io_acceptance(value: object, kind: str) -> bool:
    text = str(value or "").strip().casefold()
    if not text:
        return False
    if kind == "input":
        return text.startswith(("input ", "accept input", "takes input", "given input", "输入"))
    return text.startswith(("output ", "return ", "returns ", "produce ", "produces ", "输出", "返回"))


def _contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def _optional_field(parser, fallback):
    try:
        return parser()
    except ValueError:
        return fallback


def _hypotheses(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("hypotheses must be an array")
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("hypotheses items must be objects")
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        certainty = str(raw.get("certainty") or "uncertain").strip().casefold()
        if certainty not in TASK_CERTAINTIES:
            certainty = "uncertain"
        items.append({"text": text, "certainty": certainty})
    return items


def _clues(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("clues must be an array")
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("clues items must be objects")
        value_text = str(raw.get("value") or raw.get("path") or raw.get("symbol") or "").strip()
        if not value_text:
            continue
        kind = str(raw.get("kind") or "other").strip().casefold()
        if kind not in TASK_CLUE_KINDS:
            kind = "other"
        line = raw.get("line")
        try:
            line = int(line) if line not in (None, "") else None
        except (TypeError, ValueError):
            line = None
        items.append({
            "kind": kind,
            "value": value_text,
            "path": str(raw.get("path") or "").strip(),
            "line": line if line and line > 0 else None,
            "symbol": str(raw.get("symbol") or "").strip(),
            "note": str(raw.get("note") or "").strip(),
            **({
                field: raw[field]
                for field in ("source_ref", "source_refs", "source_excerpt")
                if field in raw
            }),
        })
    return items


def _unique_clues(items: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for item in items:
        key = _clue_identity(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _clue_identity(item: dict) -> str:
    payload = {
        key: value
        for key, value in item.items()
        if key not in CLUE_METADATA_FIELDS and value not in (None, "", [], {})
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _analysis_hypothesis(message: str, analysis: dict) -> str:
    for hypothesis in analysis["hypotheses"]:
        if hypothesis.get("certainty") != "guess":
            return hypothesis["text"]
    return ""


