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
from ..status.task_analysis import _json_candidates
from .constants import (
    FINDING_FIELDS,
    REQUIRED_FINDING_SLOT_ATTEMPTS,
    STATE_WRITE_REASON_PREFIX,
)
from .domain import (
    _analysis_is_read_only,
    _belief_text,
    _beliefs,
    _observation_context_view,
    _observation_reference_payload,
    _reference_list,
)
from .ids import (
    _initial_unknowns,
    _merge_unknowns,
    _normalize_unknown_id,
    _question_key,
    _unknowns,
)
from .util import _dedupe_strings


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
    belief_observation_ids = _dedupe_strings([
        *pending_observation_ids,
        *_semantic_repair_observation_ids(recorded_findings, required_resolution_ids),
    ])
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
                recorded_findings,
                observations,
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
                provider,
                model,
                attempt_messages,
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
