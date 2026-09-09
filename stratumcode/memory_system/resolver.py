from __future__ import annotations

from .llm import call_memory_json
from . import store


def resolve_references(workspace_dir: str, session_id: int | None, query: str) -> list[dict]:
    if session_id is None:
        return []
    refs = store.list_refs(workspace_dir, session_id, limit=80)
    if not refs:
        return []
    data = call_memory_json("resolve_references", {
        "query": query,
        "refs": [_ref_candidate(ref) for ref in refs],
    })
    if not isinstance(data, dict) or data.get("needs_clarification"):
        return []
    items = data.get("references")
    if not isinstance(items, list):
        return []
    by_id = {str(ref.get("id") or ""): ref for ref in refs}
    resolved = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ref = by_id.get(str(item.get("id") or ""))
        if ref is None:
            continue
        confidence = _enum(item.get("confidence"), {"high", "medium", "low"})
        reason = str(item.get("reason") or "").strip()
        if confidence == "high" and reason:
            resolved.append({**ref, "confidence": confidence, "reason": reason})
    return resolved[:5]


def _ref_candidate(ref: dict) -> dict:
    payload = ref.get("payload") if isinstance(ref.get("payload"), dict) else {}
    return {
        "id": ref.get("id", ""),
        "ref_key": ref.get("ref_key", ""),
        "turn_id": ref.get("turn_id", ""),
        "created_at": ref.get("created_at", ""),
        "label": ref.get("label", ""),
        "content": ref.get("content", ""),
        "target_record_id": ref.get("target_record_id", ""),
        "kind": payload.get("kind", ""),
        "index": payload.get("index", ""),
    }


def _enum(value: object, allowed: set[str]) -> str:
    text = str(value or "").strip().casefold()
    return text if text in allowed else ""
