from __future__ import annotations

import re

from .constants import (
    CLEARIFY_RESOLUTION_REASON,
    GROUNDING_LITERAL_SPAN_CONTEXT_LINES,
    GROUNDING_LITERAL_SPAN_MAX_ITEMS,
    GROUNDING_LITERAL_SPAN_MAX_LINE_CHARS,
    GROUNDING_LITERAL_SPAN_MAX_LINES,
    GROUNDING_LITERAL_REASON_PREFIX,
    STATE_WRITE_REASON_PREFIX,
)
from .ids import _normalize_unknown_id, _same_unknown_id, _unknowns
from .util import _dedupe_strings, _string_list


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
