from __future__ import annotations

import json
import re
import sys

from .. import app_settings, prompt
from ..agent_runtime import (
    add_usage as _add_usage,
    call_model as _agent_call_model,
    content_text as _content_text,
    start_event,
    usage_delta as _usage_delta,
)
from ..json2slots import JSONValue, json2slots
from ..status.task_contract import LEGACY_NEEDS_USER_STATUS
from ..status.task_analysis import _json_candidates
from .constants import (
    CLEARIFY_RESOLUTION_REASON,
    FINDING_FIELDS,
    REQUIRED_FINDING_SLOT_ATTEMPTS,
    RESOLUTION_KINDS,
    STATE_WRITE_REASON_PREFIX,
)
from .domain import (
    _analysis_is_read_only,
    _belief_text,
    _beliefs,
    _observation_context_view,
    _observation_reference_payload,
    _observation_refs,
    _reference_list,
    _semantic_missing_items,
)
from .ids import (
    _find_by_unknown_id,
    _initial_unknowns,
    _merge_unknowns,
    _normalize_unknown_id,
    _question_key,
    _unknowns,
)
from .state import InvestigationRuntime, InvestigationState
from .util import _dedupe_strings, _string_list


def _call_model(*args: object, **kwargs: object) -> dict:
    # Preserve the historical patch point exposed by stratumcode.investigator.
    investigator = sys.modules.get("stratumcode.investigator")
    patched = getattr(investigator, "_call_model", None) if investigator is not None else None
    if patched is not None and patched is not _agent_call_model:
        return patched(*args, **kwargs)
    return _agent_call_model(*args, **kwargs)


def _has_finding_fields(arguments: dict) -> bool:
    return any(isinstance(arguments.get(field), list) and arguments.get(field) for field in FINDING_FIELDS)


def _nothing_to_record_result(next_action: str = "finish_investigation") -> dict:
    return {
        "recorded": False,
        "code": "nothing_to_record",
        "next_action": next_action,
    }


def _record_slot_template(
    belief_observation_ids: list[str] | None = None,
    resolution_ids: list[str] | None = None,
) -> dict[str, JSONValue]:
    return {
        "beliefs": ["____" for _ in belief_observation_ids or []],
        "resolutions": ["____" for _ in resolution_ids or []],
        "new_unknowns": "____",
    }


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
            "observation_ids, and belief_ids. status is resolved, partially_resolved, needs_clearify, or deferred. "
            "For append-only semantic repairs, include repair_mode=append_missing_only."
        )
    contracts = {
        "beliefs": (
            "Return a JSON array of objects with statement, status, observation_ids. "
            "status is one of unverified, plausible, supported, strongly_supported, runtime_confirmed, contradicted, invalidated. "
            "observation_ids must use runtime refs such as obs_1 or exact observation ids."
        ),
        "resolutions": (
            "Return a JSON array of objects with unknown_id, status, answer, observation_ids, belief_ids, reason. "
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


def _empty_recorded_findings() -> dict:
    return {field: [] for field in FINDING_FIELDS}


def _record_findings_by_slots(
    state: InvestigationState,
    runtime: InvestigationRuntime,
    *,
    reason: str,
    required_resolution_ids: list[str] | None = None,
) -> Iterator[dict]:
    required_resolution_ids = required_resolution_ids or []
    belief_observation_ids = _dedupe_strings([
        *state.observations.pending_ids,
        *_semantic_repair_observation_ids(state.findings.recorded, required_resolution_ids),
    ])
    resolution_slot_ids = _record_resolution_slot_ids(
        runtime.analysis,
        state.observations.items,
        state.findings.recorded,
        state.observations.pending_ids,
        required_resolution_ids,
    )
    slot_messages = [
        {"role": "system", "content": prompt.build_investigation_static(
            app_settings.get_output_language()
        )},
        {"role": "user", "content": _record_slot_context(
            reason,
            runtime.analysis,
            state.observations.items,
            state.findings.recorded,
            state.observations.pending_ids,
            required_resolution_ids,
            belief_observation_ids,
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
        slot_prompt = _record_slot_prompt(
            path,
            prompt_text,
            required=required,
            required_answer_literals=_required_state_write_literals(
                path,
                resolution_slot_ids,
                state.findings.recorded,
                state.observations.items,
            ),
        )
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
        attempt_messages = [*slot_messages, {"role": "user", "content": slot_prompt}]
        for attempt in range(attempts):
            assistant = _call_model(
                runtime.provider,
                runtime.model,
                attempt_messages,
                tools=[],
                use_skills=False,
            )
            if usage := _usage_delta(runtime.pricing_rules, assistant.pop("_usage", {})):
                _add_usage(state.usage.total, usage)
                usage_events.append(start_event(
                    f"{runtime.run_id}-usage-record-slot-{len(usage_events)}",
                    "usage",
                    {"delta": usage, "total": state.usage.total},
                ))
            raw = _normalize_record_slot_answer(
                _content_text(assistant.get("content")),
                path,
            )
            if _valid_record_slot_value(raw, path, required=required):
                break
            if attempt + 1 < attempts:
                attempt_messages = [
                    *slot_messages,
                    {"role": "user", "content": slot_prompt},
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": (
                        f"The {path} slot has the wrong shape. Return {expected}"
                    )},
                ]
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
        state.findings.recorded,
    )
    resolutions = _runtime_slot_resolutions(
        filled.get("resolutions"),
        resolution_slot_ids,
        state.observations.items,
        state.findings.recorded,
        beliefs,
        state.observations.pending_ids,
    )
    result: dict = {
        "reason": reason,
        **_empty_recorded_findings(),
        "beliefs": beliefs,
        "resolutions": resolutions,
        "new_unknowns": _runtime_new_unknowns(
            filled.get("new_unknowns"),
            runtime.analysis,
            state.findings.recorded,
            resolutions,
        ),
    }
    return result


def _semantic_repair_observation_ids(
    recorded_findings: dict,
    required_resolution_ids: list[str],
) -> list[str]:
    required = {_normalize_unknown_id(item) for item in required_resolution_ids}
    return _dedupe_strings([
        evidence_id
        for resolution in recorded_findings.get("resolutions", [])
        if isinstance(resolution, dict)
        and resolution.get("repair_mode") == "append_missing_only"
        and _normalize_unknown_id(str(resolution.get("unknown_id") or "")) in required
        for evidence_id in _reference_list(resolution.get("evidence"))
    ])


def _record_slot_context(
    reason: str,
    analysis: dict,
    observations: list[dict],
    recorded_findings: dict,
    pending_observation_ids: list[str],
    required_resolution_ids: list[str],
    belief_observation_ids: list[str] | None = None,
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
        "observation_refs": _observation_reference_payload(observations),
        "required_resolution_ids": list(required_resolution_ids),
        "observations": _record_slot_relevant_observations(
            observations,
            required_resolution_ids,
            belief_observation_ids or pending_observation_ids,
        ),
        "already_recorded": _record_slot_relevant_findings(
            recorded_findings,
            required_resolution_ids,
            belief_observation_ids or pending_observation_ids,
        ),
        "runtime_slot_bindings": {
            "beliefs": [
                {"index": index, "observation_id": observation_id}
                for index, observation_id in enumerate(belief_observation_ids or pending_observation_ids)
            ],
            "resolutions": [
                {"index": index, "unknown_id": unknown_id}
                for index, unknown_id in enumerate(resolution_slot_ids or required_resolution_ids)
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _record_slot_relevant_findings(
    recorded_findings: dict,
    required_resolution_ids: list[str],
    observation_ids: list[str],
) -> dict:
    required = {_normalize_unknown_id(item) for item in required_resolution_ids}
    evidence = {str(item).strip() for item in observation_ids if str(item).strip()}
    resolutions = [
        item for item in recorded_findings.get("resolutions", [])
        if isinstance(item, dict)
        and (
            not required
            or _normalize_unknown_id(str(item.get("unknown_id") or "")) in required
        )
    ]
    belief_ids = {
        belief_id
        for resolution in resolutions
        for belief_id in _reference_list(resolution.get("belief_ids"))
    }
    beliefs = [
        item for item in recorded_findings.get("beliefs", [])
        if isinstance(item, dict)
        and (
            str(item.get("id") or "").strip() in belief_ids
            or bool(evidence.intersection(_reference_list(item.get("evidence"))))
        )
    ]
    return {
        **_empty_recorded_findings(),
        "beliefs": beliefs,
        "resolutions": resolutions,
        "new_unknowns": [
            item for item in recorded_findings.get("new_unknowns", [])
            if isinstance(item, dict)
            and (
                not required
                or _normalize_unknown_id(str(item.get("id") or "")) in required
            )
        ],
    }


def _record_slot_relevant_observations(
    observations: list[dict],
    required_resolution_ids: list[str],
    observation_ids: list[str],
) -> list[dict]:
    required = {_normalize_unknown_id(item) for item in required_resolution_ids}
    selected_ids = {str(item).strip() for item in observation_ids if str(item).strip()}
    selected = [
        item for item in observations
        if isinstance(item, dict)
        and (
            str(item.get("id") or "").strip() in selected_ids
            or bool(required.intersection(
                _normalize_unknown_id(value)
                for value in item.get("target_unknown_ids", [])
            ))
        )
    ]
    return [_observation_context_view(item) for item in selected[-12:]]


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

def _require_finding_fields(arguments: dict) -> None:
    if not _has_finding_fields(arguments):
        raise ValueError(
            "record_investigation_findings must include at least one non-empty findings array; canonical arrays are "
            "beliefs, resolutions, unknowns, new_unknowns, user_decisions_required, or task_updates"
        )

def _resolution_kind(raw: dict, status: str) -> str:
    value = str(raw.get("kind") or "").strip()
    if value in RESOLUTION_KINDS:
        return value
    if status == "deferred":
        return "deferred"
    if status == "needs_clearify":
        return "user_decision"
    return "derived_inference"

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

def _recorded_findings_signature(recorded: dict) -> str:
    beliefs = [
        {
            "key": _belief_identity_key(item),
            "evidence": sorted(_reference_list(item.get("evidence"))),
            "status": str(item.get("status") or "").strip(),
        }
        for item in _beliefs(recorded.get("beliefs"))
    ]
    resolutions = [
        {
            "unknown_id": _normalize_unknown_id(item.get("unknown_id")),
            "status": str(item.get("status") or "").strip(),
            "evidence": sorted(_reference_list(item.get("evidence"))),
            "belief_ids": sorted(_reference_list(item.get("belief_ids"))),
        }
        for item in _resolutions(recorded.get("resolutions"))
    ]
    unknowns = [
        {
            "id": _normalize_unknown_id(item.get("id")),
            "status": str(item.get("status") or "").strip(),
            "strategy": str(item.get("resolution_strategy") or "").strip(),
        }
        for item in _unknowns(recorded.get("unknowns")) + _unknowns(recorded.get("new_unknowns"))
    ]
    return json.dumps(
        {
            "beliefs": sorted(beliefs, key=lambda item: item["key"]),
            "resolutions": sorted(resolutions, key=lambda item: item["unknown_id"]),
            "unknowns": sorted(unknowns, key=lambda item: item["id"]),
            "decisions": sorted(_string_list(recorded.get("user_decisions_required"))),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

def _merge_recorded_findings(current: dict, update: dict) -> dict:
    merged = {field: list(current.get(field, [])) for field in FINDING_FIELDS}
    belief_aliases: dict[str, str] = {}
    if isinstance(update.get("beliefs"), list):
        merged["beliefs"], belief_aliases = _merge_beliefs_by_identity(
            merged["beliefs"],
            update["beliefs"],
        )
    for field in FINDING_FIELDS:
        if field == "beliefs":
            continue
        value = update.get(field)
        if isinstance(value, list):
            if field == "resolutions" and belief_aliases:
                value = [_remap_resolution_belief_ids(item, belief_aliases) for item in value]
            merged[field] = _merge_list_by_identity(merged[field], value)
            if field == "resolutions":
                # clearify 用户答案是权威决定：覆盖 unknown 的先前模型自解析
                # 避免 answers 里两条矛盾答案并存（重复 clearify 的根源）。
                merged[field] = _supersede_resolutions_with_clearify(merged[field])
    return merged

def _supersede_resolutions_with_clearify(resolutions: list[dict]) -> list[dict]:
    clearify_ids = {
        str(item.get("unknown_id") or "").strip()
        for item in resolutions
        if isinstance(item, dict) and str(item.get("reason") or "") == CLEARIFY_RESOLUTION_REASON
    }
    if not clearify_ids:
        return resolutions
    return [
        item
        for item in resolutions
        if not (
            isinstance(item, dict)
            and str(item.get("unknown_id") or "").strip() in clearify_ids
            and str(item.get("reason") or "") != CLEARIFY_RESOLUTION_REASON
        )
    ]

def _merge_beliefs_by_identity(left: list, right: list) -> tuple[list, dict[str, str]]:
    result = list(left)
    aliases: dict[str, str] = {}
    positions: dict[str, int] = {}
    for index, item in enumerate(result):
        key = _belief_identity_key(item)
        if key:
            positions[key] = index
    for item in right:
        key = _belief_identity_key(item)
        if key and key in positions:
            existing = result[positions[key]]
            if isinstance(existing, dict) and isinstance(item, dict):
                result[positions[key]] = _merge_belief(existing, item)
                old_id = str(existing.get("id") or "").strip()
                new_id = str(item.get("id") or "").strip()
                if old_id and new_id and old_id != new_id:
                    aliases[new_id] = old_id
            else:
                result[positions[key]] = item
        else:
            if key:
                positions[key] = len(result)
            result.append(item)
    return result, aliases

def _merge_belief(existing: dict, item: dict) -> dict:
    merged = {**existing, **item}
    if existing.get("id"):
        merged["id"] = existing["id"]
    merged["evidence"] = _dedupe_strings([
        *_reference_list(existing.get("evidence")),
        *_reference_list(item.get("evidence")),
    ])
    return merged

def _remap_resolution_belief_ids(item: object, aliases: dict[str, str]) -> object:
    if not isinstance(item, dict) or not aliases:
        return item
    return {
        **item,
        "belief_ids": [
            aliases.get(str(raw).strip(), str(raw).strip())
            for raw in item.get("belief_ids", [])
            if str(raw).strip()
        ],
    }

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
                if item.get("repair_mode") == "append_missing_only" and item.get("unknown_id"):
                    result[positions[key]] = _append_resolution_repair(existing, item)
                elif existing.get("reason") == CLEARIFY_RESOLUTION_REASON:
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

def _append_resolution_repair(existing: dict, repair: dict) -> dict:
    merged = dict(existing)
    new_refs = False
    for field in ("evidence", "belief_ids"):
        old_values = _reference_list(existing.get(field))
        new_values = _reference_list(repair.get(field))
        if any(value not in old_values for value in new_values):
            new_refs = True
        merged[field] = _dedupe_strings([
            *old_values,
            *new_values,
        ])
    for field in ("status", "reason"):
        if str(repair.get(field) or "").strip():
            merged[field] = repair[field]
    # 保留 repair_mode/semantic_missing：append-only 修复是否真正通过
    # 只能由 audit（finish 时的语义门禁）来解决，模型提交 repair 时不能
    # 自我宣布 resolved。旧实现在这里 pop，导致下一轮 repair_ids 为空。
    # 主循环误入 FINISH 分支、模型 read/record 被 already_resolved 拦截
    # 的三面夹击硬锁（d5eef05a 第二形态）。
    return merged

def _reject_empty_repair(arguments: dict, recorded: dict) -> None:
    """Reject append_missing_only repair resolutions that add no new evidence.

    Without this guard a model caught in the semantic repair loop can resubmit
    the same partially_resolved resolution forever (empty belief_ids/evidence),
    keeping the unknown permanently in the repair set with zero progress.
    """
    if not isinstance(arguments.get("resolutions"), list):
        return
    for item in arguments["resolutions"]:
        if not isinstance(item, dict):
            continue
        if str(item.get("repair_mode") or "").strip() != "append_missing_only":
            continue
        unknown_id = str(item.get("unknown_id") or "").strip()
        if not unknown_id:
            continue
        existing = _find_by_unknown_id(
            [res for res in recorded.get("resolutions", []) if isinstance(res, dict)],
            unknown_id,
        )
        old_evidence = set(_reference_list((existing or {}).get("evidence")))
        old_beliefs = set(_reference_list((existing or {}).get("belief_ids")))
        new_evidence = set(_reference_list(item.get("evidence")))
        new_beliefs = set(_reference_list(item.get("belief_ids")))
        if str(item.get("status") or "") == "resolved":
            continue
        if new_evidence - old_evidence or new_beliefs - old_beliefs:
            continue
        raise ValueError(
            f"append_missing_only repair for {unknown_id} adds no new evidence or "
            "belief_ids; gather the missing observations first (read/grep/code_nav), "
            "then resubmit the repair with the new references."
        )

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

def _belief_identity_key(item) -> str:
    if not isinstance(item, dict):
        return str(item)
    key = str(item.get("key") or item.get("fact_key") or "").strip()
    if key:
        return f"fact:{key.casefold()}"
    statement = _normalize_statement(_belief_text(item))
    if statement:
        return f"statement:{statement}"
    item_id = str(item.get("id") or "").strip()
    return f"id:{item_id}" if item_id else ""

def _normalize_statement(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()

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
        item = {
            "unknown_id": unknown_id,
            "status": status,
            "kind": _resolution_kind(raw, status),
            "answer": str(raw.get("answer") or "").strip(),
            "evidence": _observation_refs(raw),
            "belief_ids": _string_list(raw.get("belief_ids")),
            "reason": str(raw.get("reason") or "").strip(),
        }
        if str(raw.get("repair_mode") or "").strip() == "append_missing_only":
            item["repair_mode"] = "append_missing_only"
        if isinstance(raw.get("semantic_missing"), list):
            item["semantic_missing"] = _semantic_missing_items(raw.get("semantic_missing"))
        items.append(item)
    return items
