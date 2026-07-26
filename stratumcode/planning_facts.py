from __future__ import annotations

from pathlib import Path


def normalize_project_facts(investigation: dict) -> list[dict]:
    """Return stable structured facts for planning stages."""
    primary_facts = [
        item
        for key in ("project_facts", "patch_planning_facts")
        for item in (
            investigation.get(key)
            if isinstance(investigation.get(key), list)
            else []
        )
    ]
    superseded_files = {
        str(path).replace("\\", "/").casefold()
        for raw in primary_facts
        if isinstance(raw, dict) and raw.get("authority") == "runtime_validation"
        for path in raw.get("supersedes_files", [])
        if str(path).strip()
    }
    raw_facts = [
        raw
        for raw in primary_facts
        if _is_validation_fact(raw) or not _mentions_superseded_file(raw, superseded_files)
    ]
    if not raw_facts:
        raw_facts = investigation.get("patch_planning_context") or []
    facts = []
    used_ids: set[str] = set()
    used_text: set[str] = set()
    for index, raw in enumerate(raw_facts, start=1):
        if isinstance(raw, dict):
            fact_id = str(raw.get("id") or f"PF{index}").strip()
            text = str(raw.get("text") or raw.get("fact") or raw.get("statement") or "").strip()
            commands = [
                str(item).strip()
                for item in raw.get("verification_commands", [])
                if str(item).strip()
            ] if isinstance(raw.get("verification_commands"), list) else []
            metadata = {
                key: raw[key]
                for key in (
                    "authority",
                    "unknown_ids",
                    "acceptance_criteria_ids",
                    "evidence_ids",
                    "belief_ids",
                    "supersedes_files",
                )
                if key in raw
            }
        else:
            fact_id = f"PF{index}"
            text = str(raw or "").strip()
            commands = []
            metadata = {}
        if not text:
            continue
        if text in used_text:
            continue
        if not fact_id or fact_id in used_ids:
            fact_id = _available_fact_id(index, used_ids)
        used_ids.add(fact_id)
        used_text.add(text)
        fact = {"id": fact_id, "text": text, **metadata}
        if commands:
            fact["verification_commands"] = commands
        facts.append(fact)
    return facts


def _is_validation_fact(raw: object) -> bool:
    return isinstance(raw, dict) and raw.get("authority") == "runtime_validation"


def _mentions_superseded_file(raw: object, files: set[str]) -> bool:
    if not files:
        return False
    if isinstance(raw, dict):
        text = str(raw.get("text") or raw.get("fact") or raw.get("statement") or "").casefold()
    else:
        text = str(raw or "").casefold()
    return any(path in text or Path(path).name.casefold() in text for path in files)


def _available_fact_id(index: int, used_ids: set[str]) -> str:
    while f"PF{index}" in used_ids:
        index += 1
    return f"PF{index}"
