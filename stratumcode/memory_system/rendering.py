from __future__ import annotations

from .models import MemorySnapshot


def render_snapshot(snapshot: MemorySnapshot, *, consumer: str = "light_agent") -> str:
    sections = []
    if snapshot.references:
        sections.append(_section("Resolved conversation references", [
            f"- {item.get('label')}: {item.get('content')} ({item.get('confidence')})"
            for item in snapshot.references[:6]
        ]))
    fresh = [item for item in snapshot.records if item.get("freshness") != "stale"]
    if fresh:
        sections.append(_section("Reusable memory", [
            f"- [{item.get('scope')}/{item.get('kind')}/{item.get('confidence')}] {item.get('statement')}{_semantic_suffix(item)}"
            for item in fresh[:12]
        ]))
    if snapshot.stale:
        sections.append(_section("Possibly relevant but stale", [
            f"- [{item.get('kind')}] {item.get('statement')}"
            for item in snapshot.stale[:6]
        ]))
    if snapshot.omitted:
        sections.append(f"Omitted related memory: {snapshot.omitted.get('available_related_records', 0)} records due to token budget.")
    if not sections:
        return ""
    return f"Memory context for {consumer}:\n" + "\n\n".join(section for section in sections if section)


def _section(title: str, lines: list[str]) -> str:
    lines = [line for line in lines if line.strip()]
    if not lines:
        return ""
    return title + ":\n" + "\n".join(lines)


def _semantic_suffix(item: dict) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    parts = []
    if payload.get("predicate"):
        parts.append(f"predicate={payload['predicate']}")
    if payload.get("affected_paths"):
        parts.append("paths=" + ", ".join(str(path) for path in payload["affected_paths"][:4]))
    if payload.get("applies_when"):
        parts.append(f"applies_when={payload['applies_when']}")
    if payload.get("invalidated_by"):
        parts.append("invalidated_by=" + ", ".join(str(item) for item in payload["invalidated_by"][:3]))
    return " (" + "; ".join(parts) + ")" if parts else ""
