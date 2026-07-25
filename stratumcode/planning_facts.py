from __future__ import annotations


def normalize_project_facts(investigation: dict) -> list[dict]:
    """Return stable structured facts for planning stages."""
    raw_facts = investigation.get("patch_planning_facts") or investigation.get("patch_planning_context") or []
    facts = []
    used_ids: set[str] = set()
    for index, raw in enumerate(raw_facts, start=1):
        if isinstance(raw, dict):
            fact_id = str(raw.get("id") or f"PF{index}").strip()
            text = str(raw.get("text") or raw.get("fact") or raw.get("statement") or "").strip()
            commands = [
                str(item).strip()
                for item in raw.get("verification_commands", [])
                if str(item).strip()
            ] if isinstance(raw.get("verification_commands"), list) else []
        else:
            fact_id = f"PF{index}"
            text = str(raw or "").strip()
            commands = []
        if not text:
            continue
        if not fact_id or fact_id in used_ids:
            fact_id = _available_fact_id(index, used_ids)
        used_ids.add(fact_id)
        fact = {"id": fact_id, "text": text}
        if commands:
            fact["verification_commands"] = commands
        facts.append(fact)
    return facts


def _available_fact_id(index: int, used_ids: set[str]) -> str:
    while f"PF{index}" in used_ids:
        index += 1
    return f"PF{index}"
