from __future__ import annotations

import json
import re

from ..json2slots import JSONValue
from ..status.task_analysis import _json_candidates
from .constants import FINDING_FIELDS


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
