from __future__ import annotations

from collections.abc import Callable
import json
import re

from ..status.task_updates import _unknown_task_status
from .constants import (
    CLEARIFY_RESOLUTION_REASON,
    GROUNDING_LITERAL_SPAN_CONTEXT_LINES,
    GROUNDING_LITERAL_SPAN_MAX_ITEMS,
    GROUNDING_LITERAL_SPAN_MAX_LINE_CHARS,
    GROUNDING_LITERAL_SPAN_MAX_LINES,
    GROUNDING_LITERAL_REASON_PREFIX,
    STATE_WRITE_REASON_PREFIX,
)
from .ids import _find_by_unknown_id, _normalize_unknown_id, _same_unknown_id, _unknowns
from .util import _dedupe_strings, _skip_ws, _string_list


def _missing_investigation_audit(*_args: object, **_kwargs: object) -> tuple[dict, list[dict], dict[str, str]]:
    raise RuntimeError("investigation audit implementation is not registered")


_apply_investigation_audit_impl = _missing_investigation_audit


InvestigationAuditImpl = Callable[..., tuple[dict, list[dict], dict[str, str]]]


def _set_investigation_audit_impl(impl: InvestigationAuditImpl) -> None:
    global _apply_investigation_audit_impl
    _apply_investigation_audit_impl = impl


def _analysis_is_read_only(analysis: dict | None) -> bool:
    return str((analysis or {}).get("execution_mode") or "").strip().casefold() == "read_only"


def _belief_text(item: dict) -> str:
    return str(item.get("statement") or item.get("text") or item.get("summary") or item.get("content") or "").strip()


def _observation_context_view(
    observation: dict,
    literals: list[str] | None = None,
) -> dict:
    view = {
        "id": observation.get("id", ""),
        "tool": observation.get("tool", ""),
        "title": observation.get("title", ""),
        "summary": observation.get("summary", ""),
        "evidence_excerpt": observation.get("evidence_excerpt", ""),
        "verification": observation.get("verification", {}),
        "target_unknown_ids": observation.get("target_unknown_ids", []),
        "reason": observation.get("reason", ""),
        "path": observation.get("path", ""),
    }
    spans = _observation_grounding_literal_spans(observation, literals or [])
    if spans:
        view["literal_evidence_spans"] = spans
    return view


def _observation_grounding_literal_spans(
    observation: dict,
    literals: list[str],
) -> list[dict]:
    evidence = str(
        observation.get("_grounding_evidence")
        or observation.get("evidence_excerpt")
        or observation.get("summary")
        or ""
    )
    if not evidence or not literals:
        return []
    observation_id = str(observation.get("id") or "")
    path = str(observation.get("path") or "")
    spans = []
    for literal, start, end in _literal_line_ranges(evidence, literals):
        excerpt = _format_line_span_excerpt(evidence, start, end, [literal])
        if not excerpt:
            continue
        span = {
            "observation_id": observation_id,
            "literal": literal,
            "line_start": start,
            "line_end": end,
            "excerpt": excerpt,
        }
        if path:
            span["path"] = path
        spans.append(span)
        if len(spans) >= GROUNDING_LITERAL_SPAN_MAX_ITEMS:
            break
    return spans


def _literal_line_ranges(evidence: str, literals: list[str]) -> list[tuple[str, int, int]]:
    lines = evidence.splitlines() or [evidence]
    ranges: list[tuple[str, int, int]] = []
    seen: set[tuple[str, int, int]] = set()
    normalized_lines = [re.sub(r"\s+", "", line) for line in lines]
    for literal in _dedupe_strings(literals):
        normalized = re.sub(r"\s+", "", literal)
        if not normalized:
            continue
        for index, line in enumerate(normalized_lines):
            if normalized in line:
                start, end = _context_line_range(index, index, len(lines))
                key = (literal, start, end)
                if key not in seen:
                    ranges.append(key)
                    seen.add(key)
                break
        else:
            for index in range(len(lines)):
                window_end = min(len(lines), index + GROUNDING_LITERAL_SPAN_MAX_LINES)
                window = "".join(normalized_lines[index:window_end])
                if normalized not in window:
                    continue
                start, end = _context_line_range(index, window_end - 1, len(lines))
                key = (literal, start, end)
                if key not in seen:
                    ranges.append(key)
                    seen.add(key)
                break
    return ranges


def _context_line_range(hit_start: int, hit_end: int, total_lines: int) -> tuple[int, int]:
    return (
        max(1, hit_start + 1 - GROUNDING_LITERAL_SPAN_CONTEXT_LINES),
        min(total_lines, hit_end + 1 + GROUNDING_LITERAL_SPAN_CONTEXT_LINES),
    )


def _format_line_span_excerpt(
    evidence: str,
    line_start: int,
    line_end: int,
    literals: list[str],
) -> str:
    lines = evidence.splitlines() or [evidence]
    selected = lines[line_start - 1:line_end]
    return "\n".join(
        f"L{line_start + offset}: {_trim_grounding_span_line(line, literals)}"
        for offset, line in enumerate(selected)
    )


def _trim_grounding_span_line(line: str, literals: list[str]) -> str:
    text = str(line)
    if len(text) <= GROUNDING_LITERAL_SPAN_MAX_LINE_CHARS:
        return text
    for literal in literals:
        if not literal:
            continue
        index = text.find(literal)
        if index < 0:
            continue
        half = max(1, (GROUNDING_LITERAL_SPAN_MAX_LINE_CHARS - len(literal)) // 2)
        start = max(0, index - half)
        end = min(len(text), index + len(literal) + half)
        prefix = "..." if start else ""
        suffix = "..." if end < len(text) else ""
        return f"{prefix}{text[start:end]}{suffix}"
    half = GROUNDING_LITERAL_SPAN_MAX_LINE_CHARS // 2
    return f"{text[:half]}...{text[-half:]}"


def _reference_list(value) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return _string_list(value)


def _observation_reference_map(observations: list[dict]) -> dict[str, str]:
    refs: dict[str, str] = {}
    index = 1
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        observation_id = str(observation.get("id") or "").strip()
        if not observation_id:
            continue
        refs[f"obs_{index}"] = observation_id
        index += 1
    return refs


def _observation_reference_payload(observations: list[dict], *, limit: int = 12) -> list[dict]:
    refs = _observation_reference_map(observations)
    payload = []
    by_id = {
        str(item.get("id") or "").strip(): item
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    for ref, observation_id in list(refs.items())[-limit:]:
        observation = by_id.get(observation_id, {})
        payload.append({
            "ref": ref,
            "id": observation_id,
            "tool": str(observation.get("tool") or ""),
            "title": str(observation.get("title") or observation.get("summary") or ""),
        })
    return payload


def _observation_refs(value: dict) -> list[str]:
    return _dedupe_strings([
        *_reference_list(value.get("observation_ids")),
        *_reference_list(value.get("evidence")),
    ])


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
        evidence = _observation_refs(raw)
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


def _semantic_missing_items(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        requirement = str(raw.get("requirement") or raw.get("text") or "").strip()
        if not requirement:
            continue
        items.append({
            "acceptance_id": str(raw.get("acceptance_id") or "").strip(),
            "requirement": requirement,
        })
    return items


def _recorded_resolves_initial_unknowns(
    recorded: dict,
    analysis: dict | None,
    *,
    repair_ids: set[str] | None = None,
) -> bool:
    """Return whether recorded resolutions cover all initial unknowns."""
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
    if repair_ids:
        return False
    resolved = [
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") in {"resolved", "deferred"}
        and not _clearify_resolution_lacks_evidence(item)
    ]
    return all(any(_same_unknown_id(required_id, item) for item in resolved) for required_id in required_ids)


def _clearify_resolution_lacks_evidence(item: dict) -> bool:
    """Return whether a clearify resolution still lacks code evidence."""
    if str(item.get("reason") or "") != CLEARIFY_RESOLUTION_REASON:
        return False
    return not _reference_list(item.get("evidence"))


def _semantic_repair_payload(recorded: dict, unknown_ids: set[str]) -> dict:
    target_ids = {
        _normalize_unknown_id(item)
        for item in unknown_ids
        if str(item).strip()
    }
    resolutions = [
        item for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and _normalize_unknown_id(str(item.get("unknown_id") or "")) in target_ids
    ]
    recorded_ids = _dedupe_strings([
        belief_id
        for resolution in resolutions
        for belief_id in _reference_list(resolution.get("belief_ids"))
    ])
    missing = [
        missing_item
        for resolution in resolutions
        for missing_item in _semantic_missing_items(resolution.get("semantic_missing"))
    ]
    if not missing:
        missing = []
        for resolution in resolutions:
            _reason = str(resolution.get("reason") or "").strip()
            if _reason.startswith(GROUNDING_LITERAL_REASON_PREFIX):
                _literal = _reason[len(GROUNDING_LITERAL_REASON_PREFIX):].strip()
                _reason = f"Find and cite the exact code observation that contains: {_literal}" if _literal else "Cite the exact code observation that contains the claimed code literal."
            elif _reason.startswith(STATE_WRITE_REASON_PREFIX):
                _writes = _reason[len(STATE_WRITE_REASON_PREFIX):].strip()
                _reason = f"Account for the following observed state writes in the resolution: {_writes}" if _writes else "Account for observed state writes in the resolution."
            if not _reason:
                _reason = "Add the missing semantic support."
            missing.append({
                "acceptance_id": "",
                "requirement": _reason,
            })
    return {
        "accepted": False,
        "recorded_ids": recorded_ids,
        "missing": missing,
        "repair_mode": "append_missing_only",
    }


def _tool_call_summary(tool_calls: list[dict]) -> str:
    from .tools import _tool_arguments, _tool_call_subject

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


def _resolve_unknown_arguments(arguments: dict) -> dict:
    from .findings import _resolutions

    normalized = dict(arguments)
    normalized["resolutions"] = _resolutions(normalized.get("resolutions"))
    return normalized


def _record_arguments(arguments: dict) -> dict:
    from .evidence import _alias_beliefs

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


def _unknowns_needing_resolution(recorded: dict, observations: list[dict], analysis: dict | None) -> list[str]:
    from .evidence import _positive_project_observation, _supporting_belief

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
    supported = _supported_unknown_ids(recorded, observations, _positive_project_observation, _supporting_belief)
    return [
        str(item["id"])
        for item in initial
        if not any(_same_unknown_id(item["id"], known_id) for known_id in accounted)
        and any(_same_unknown_id(item["id"], supported_id) for supported_id in supported)
    ]


def _supported_unknown_ids(
    recorded: dict,
    observations: list[dict],
    positive_project_observation: Callable[[dict], bool],
    supporting_belief: Callable[[dict], bool],
) -> set[str]:
    observations_by_id = {
        str(item.get("id") or "").strip(): item
        for item in observations
        if isinstance(item, dict) and positive_project_observation(item)
    }
    by_unknown: dict[str, list[dict]] = {}
    for observation in observations_by_id.values():
        for unknown_id in observation.get("target_unknown_ids", []):
            by_unknown.setdefault(_normalize_unknown_id(unknown_id), []).append(observation)
    supported = set()
    for raw in recorded.get("beliefs", []):
        if not isinstance(raw, dict) or not supporting_belief(raw):
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
    from pathlib import Path

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


def _validate_resolution_refs(resolutions: list[dict], beliefs: list[dict], observations: list[dict]) -> None:
    from .evidence import _normalize_evidence_refs

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
    gated, _, _ = _apply_investigation_audit_impl(
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
