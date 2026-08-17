from __future__ import annotations

PRIMARY_FACT_KEYS = ("project_facts", "patch_planning_facts")
TEXT_KEYS = ("text", "fact", "statement")
METADATA_KEYS = (
    "authority",
    "unknown_ids",
    "acceptance_criteria_ids",
    "evidence_ids",
    "belief_ids",
    "supersedes_files",
)
RUNTIME_VALIDATION_AUTHORITY = "runtime_validation"


def normalize_project_facts(investigation: dict) -> list[dict]:
    """Return stable structured facts for planning stages."""
    primary_facts = _primary_fact_items(investigation)
    raw_facts = _current_fact_items(primary_facts)
    if not raw_facts:
        raw_facts = _list_value(investigation, "patch_planning_context")
    return _deduped_facts(raw_facts)


def _primary_fact_items(investigation: dict) -> list[object]:
    return [
        item
        for key in PRIMARY_FACT_KEYS
        for item in _list_value(investigation, key)
    ]


def _current_fact_items(raw_facts: list[object]) -> list[object]:
    superseded_files = _superseded_file_refs(raw_facts)
    return [
        raw
        for raw in raw_facts
        if _is_validation_fact(raw) or not _mentions_superseded_file(raw, superseded_files)
    ]


def _deduped_facts(raw_facts: list[object]) -> list[dict]:
    facts = []
    used_ids: set[str] = set()
    used_text: set[str] = set()
    for index, raw in enumerate(raw_facts, start=1):
        fact_id, text, commands, metadata = _normalized_fact(raw, index)
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


def _normalized_fact(raw: object, index: int) -> tuple[str, str, list[str], dict[str, object]]:
    if not isinstance(raw, dict):
        return f"PF{index}", str(raw or "").strip(), [], {}
    return (
        str(raw.get("id") or f"PF{index}").strip(),
        _fact_text(raw),
        _verification_commands(raw),
        {key: raw[key] for key in METADATA_KEYS if key in raw},
    )


def _fact_text(raw: dict) -> str:
    return str(next((raw.get(key) for key in TEXT_KEYS if raw.get(key)), "")).strip()


def _verification_commands(raw: dict) -> list[str]:
    commands = raw.get("verification_commands", [])
    if not isinstance(commands, list):
        return []
    return [command for item in commands if (command := str(item).strip())]


def _list_value(raw: object, key: str) -> list[object]:
    if not isinstance(raw, dict):
        return []
    value = raw.get(key)
    return value if isinstance(value, list) else []


def _superseded_file_refs(raw_facts: list[object]) -> tuple[set[str], set[str]]:
    paths = {
        _normalized_path(path)
        for raw in raw_facts
        if _is_validation_fact(raw)
        for path in _list_value(raw, "supersedes_files")
        if str(path).strip()
    }
    return paths, {_path_name(path) for path in paths}


def _is_validation_fact(raw: object) -> bool:
    return isinstance(raw, dict) and raw.get("authority") == RUNTIME_VALIDATION_AUTHORITY


def _mentions_superseded_file(raw: object, refs: tuple[set[str], set[str]]) -> bool:
    paths, names = refs
    if not paths:
        return False
    text = (_fact_text(raw) if isinstance(raw, dict) else str(raw or "").strip()).casefold()
    return any(path in text for path in paths) or any(name and name in text for name in names)


def _normalized_path(path: object) -> str:
    return str(path).strip().replace("\\", "/").casefold()


def _path_name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _available_fact_id(index: int, used_ids: set[str]) -> str:
    while f"PF{index}" in used_ids:
        index += 1
    return f"PF{index}"
