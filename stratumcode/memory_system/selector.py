from __future__ import annotations

import re

from .models import MemorySnapshot
from .resolver import resolve_references
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
    references = resolve_references(workspace_dir, session_id, query)
    records = [
        item for item in list_records(workspace_dir, limit=1000)
        if item.get("scope") in scopes and item.get("status") != "reverted"
    ]
    terms = _query_terms(query, analysis)
    scored = [(_score_record(item, terms, session_id, references), item) for item in records]
    fresh = [item for score, item in scored if score > 0 and item.get("freshness") != "stale"]
    stale = [item for score, item in scored if score > 0 and item.get("freshness") == "stale"]
    fresh.sort(key=lambda item: _sort_key(item, session_id), reverse=True)
    selected, omitted = _fit_budget(fresh, token_budget)
    summaries = [item for item in selected if item.get("kind") == "summary"]
    conflicts = [item for item in selected if item.get("payload", {}).get("conflict")]
    return MemorySnapshot(
        records=selected,
        references=references,
        stale=stale[:12],
        conflicts=conflicts,
        summaries=summaries,
        omitted={"reason": "token_budget", "available_related_records": omitted} if omitted else {},
    )


def _query_terms(query: str, analysis: dict | None) -> set[str]:
    parts = [query or ""]
    if isinstance(analysis, dict):
        parts.append(str(analysis.get("summary") or ""))
        intent = analysis.get("intent") if isinstance(analysis.get("intent"), dict) else {}
        parts.append(str(intent.get("summary") or ""))
        for bucket in ("unknowns", "acceptance_criteria", "constraints", "clues"):
            for item in analysis.get(bucket, []) if isinstance(analysis.get(bucket), list) else []:
                parts.append(str(item))
    return {
        item.casefold()
        for item in re.findall(r"[\w\u4e00-\u9fff]+", " ".join(parts))
        if len(item) > 1
    }


def _score_record(item: dict, terms: set[str], session_id: int | None, refs: list[dict]) -> int:
    text = " ".join(str(item.get(key) or "") for key in ("statement", "subject_key", "kind", "source"))
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    text += " " + " ".join(str(value) for value in payload.values() if isinstance(value, (str, int, float)))
    score = len(terms & _terms(text))
    if item.get("session_id") == session_id:
        score += 4
    if item.get("confidence") == "verified":
        score += 3
    if item.get("scope") == "project":
        score += 1
    target_ids = {str(ref.get("target_record_id") or "") for ref in refs}
    if str(item.get("id") or "") in target_ids:
        score += 20
    return score


def _terms(value: str) -> set[str]:
    return {item.casefold() for item in re.findall(r"[\w\u4e00-\u9fff]+", value or "") if len(item) > 1}


def _sort_key(item: dict, session_id: int | None) -> tuple[int, int, str]:
    return (
        1 if item.get("session_id") == session_id else 0,
        1 if item.get("confidence") == "verified" else 0,
        str(item.get("updated_at") or item.get("created_at") or ""),
    )


def _fit_budget(records: list[dict], token_budget: int) -> tuple[list[dict], int]:
    selected = []
    used = 0
    budget = max(800, int(token_budget or 4000))
    for item in records:
        cost = max(16, len(str(item.get("statement") or "")) // 4 + 12)
        if selected and used + cost > budget:
            break
        selected.append(item)
        used += cost
    return selected, max(0, len(records) - len(selected))
