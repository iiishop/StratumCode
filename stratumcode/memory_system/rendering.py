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
            f"- [{item.get('scope')}/{item.get('kind')}/{item.get('confidence')}] {item.get('statement')}"
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
