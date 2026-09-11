from __future__ import annotations

import json
import re
from .conversation import recent_turns
from .llm import call_memory_json
from .models import MemorySnapshot
from .store import list_records


def select(
    *,
    workspace_dir: str,
    session_id: int | None,
    query: str,
    analysis: dict | None = None,
    scopes: tuple[str, ...] = ("turn", "session", "project"),
    token_budget: int = 4000,
) -> MemorySnapshot:
    if not str(query or "").strip() and not analysis:
        return MemorySnapshot()
    turns = recent_turns(workspace_dir, session_id) if "session" in scopes or "turn" in scopes else []
    references = []
    records = [
        item for item in list_records(workspace_dir, limit=1000, scoped=True, session_id=session_id)
        if item.get("scope") in scopes and item.get("status") in {"accepted", "edited"}
        and (item.get("scope") == "project" or session_id is not None and item.get("session_id") == session_id)
        and item.get("kind") != "conversation" and not item.get("superseded_by")
    ]
    if not records:
        return MemorySnapshot(references=references, recent_turns=turns)
    lookup = query
    records.sort(key=lambda item: _relevance(item, lookup, session_id), reverse=True)
    records = records[:200]
    selection = _llm_selection(query, analysis, session_id, records, references, turns)
    selected_ids = set(selection.get("selected_record_ids", []))
    if not selected_ids:
        # A unavailable memory model must not erase deterministic, relevant durable context.
        selected_ids = {item["id"] for item in records[:6] if _relevance(item, lookup, session_id)[0] > 0}
    stale_ids = set(selection.get("stale_record_ids", []))
    conflict_ids = set(selection.get("conflict_record_ids", []))
    conflict_ids |= {item["id"] for item in records if any(link.get("relation") == "conflicts" for link in item.get("relations", []))}
    summary_ids = set(selection.get("summary_record_ids", []))
    for ref in references:
        target = str(ref.get("target_record_id") or "").strip()
        if target:
            selected_ids.add(target)
    fresh = [
        item for item in records
        if str(item.get("id") or "") in selected_ids and item.get("freshness") != "stale"
    ]
    stale = [
        item for item in records
        if str(item.get("id") or "") in stale_ids or (
            str(item.get("id") or "") in selected_ids and item.get("freshness") == "stale"
        )
    ]
    fresh.sort(key=lambda item: _selection_order(item, selection), reverse=False)
    selected, omitted = _fit_budget(fresh, token_budget)
    summaries = [item for item in selected if str(item.get("id") or "") in summary_ids or item.get("kind") == "summary"]
    conflicts = [item for item in selected if str(item.get("id") or "") in conflict_ids]
    return MemorySnapshot(
        records=selected, recent_turns=turns,
        references=references,
        stale=stale[:12],
        conflicts=conflicts,
        summaries=summaries,
        omitted={"reason": "token_budget", "available_related_records": omitted} if omitted else {},
    )


def _llm_selection(query: str, analysis: dict | None, session_id: int | None, records: list[dict], references: list[dict], turns: list[dict] | None = None) -> dict:
    data = call_memory_json("select_records", {
        "query": query,
        "analysis": analysis or {},
        "session_id": session_id,
        "recent_turns": turns or [],
        "references": [_reference_payload(item) for item in references],
        "records": [_record_payload(item) for item in records[:200]],
    })
    if not isinstance(data, dict):
        return {}
    return {
        "selected_record_ids": _known_ids(data.get("selected_record_ids"), records),
        "stale_record_ids": _known_ids(data.get("stale_record_ids"), records),
        "conflict_record_ids": _known_ids(data.get("conflict_record_ids"), records),
        "summary_record_ids": _known_ids(data.get("summary_record_ids"), records),
    }


def _record_payload(item: dict) -> dict:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return {
        "id": item.get("id", ""),
        "scope": item.get("scope", ""),
        "kind": item.get("kind", ""),
        "subject_kind": item.get("subject_kind", ""),
        "subject_key": item.get("subject_key", ""),
        "statement": item.get("statement", ""),
        "confidence": item.get("confidence", ""),
        "status": item.get("status", ""),
        "source_record_ids": item.get("source_record_ids", []),
        "relations": item.get("relations", []),
        "freshness": item.get("freshness", ""),
        "session_id": item.get("session_id"),
        "source": item.get("source", ""),
        "updated_at": item.get("updated_at", ""),
        "semantic": {
            "predicate": payload.get("predicate", ""),
            "objects": payload.get("objects", []),
            "affected_paths": payload.get("affected_paths", []),
            "applies_when": payload.get("applies_when", ""),
            "invalidated_by": payload.get("invalidated_by", []),
            "importance": payload.get("importance", "unknown"),
        },
    }


def _reference_payload(item: dict) -> dict:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return {
        "id": item.get("id", ""),
        "label": item.get("label", ""),
        "content": item.get("content", ""),
        "target_record_id": item.get("target_record_id", ""),
        "confidence": item.get("confidence", ""),
        "reason": item.get("reason", ""),
        "kind": payload.get("kind", ""),
    }


def _known_ids(value: object, records: list[dict]) -> list[str]:
    known = {str(item.get("id") or "") for item in records}
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item) in known]


def _selection_order(item: dict, selection: dict) -> int:
    ids = selection.get("selected_record_ids", [])
    record_id = str(item.get("id") or "")
    return ids.index(record_id) if record_id in ids else len(ids)


def _fit_budget(records: list[dict], token_budget: int) -> tuple[list[dict], int]:
    selected = []
    used = 0
    budget = max(0, int(token_budget))
    for item in records:
        cost = max(16, len(json.dumps(_record_payload(item), ensure_ascii=False)))
        if used + cost > budget:
            continue
        selected.append(item)
        used += cost
    return selected, max(0, len(records) - len(selected))


def _relevance(item: dict, query: str, session_id: int | None) -> tuple[int, int]:
    tokens = set(re.findall(r"[a-z0-9_./-]{2,}|[\u4e00-\u9fff]{2}", query.casefold()))
    text = (str(item.get("statement", "")) + " " + str(item.get("subject_key", ""))).casefold()
    return (sum(len(token) for token in tokens if token in text), int(item.get("session_id") == session_id and session_id is not None))
