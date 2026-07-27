from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from html import escape
from importlib import import_module
from pathlib import Path
from uuid import uuid4

from . import skills
from .db import db_session
from .subagent_catalog import list_available

GLOBAL_TARGET = "global"
TARGET_MODES = {"merge", "replace"}
MAX_SKILL_BYTES = 128_000
MAX_SUPPORTING_FILES = 10
MAX_CATALOG_CHARS = 12_000
_CONTEXT: ContextVar[dict | None] = ContextVar("skill_context", default=None)


def targets() -> list[dict]:
    from .status import handlers

    result = [{
        "id": GLOBAL_TARGET,
        "kind": "global",
        "name": GLOBAL_TARGET,
        "label": "Global",
        "description": "Available to targets that merge global skills.",
    }]
    for state, handler in handlers().items():
        module = import_module(handler.__module__)
        if not getattr(module, "SKILL_TARGET", True):
            continue
        name = state.value
        result.append({
            "id": f"state:{name}",
            "kind": "state",
            "name": name,
            "label": str(getattr(module, "SKILL_LABEL", name.replace("_", " ").title())),
            "description": f"Skills available while the agent is {name.replace('_', ' ')}.",
        })
    for agent in list_available():
        name = str(agent.get("name") or "").strip()
        if not name:
            continue
        result.append({
            "id": f"subagent:{name}",
            "kind": "subagent",
            "name": name,
            "label": str(agent.get("display_name") or name),
            "description": str(agent.get("task") or ""),
        })
    return result


def configuration() -> dict:
    _ensure_tables()
    valid_targets = {item["id"] for item in targets()}
    valid_skills = {item["id"] for item in skills.list_local()["items"]}
    assignments = {target_id: [] for target_id in valid_targets}
    modes = {
        target_id: "merge"
        for target_id in valid_targets
        if target_id != GLOBAL_TARGET
    }
    with db_session() as database:
        rows = database.execute(
            "SELECT target_id, skill_id FROM skill_assignments ORDER BY rowid"
        ).fetchall()
        settings = database.execute(
            "SELECT target_id, mode FROM skill_target_settings"
        ).fetchall()
    for row in rows:
        if row["target_id"] in assignments and row["skill_id"] in valid_skills:
            assignments[row["target_id"]].append(row["skill_id"])
    for row in settings:
        if row["target_id"] in modes and row["mode"] in TARGET_MODES:
            modes[row["target_id"]] = row["mode"]
    return {
        "targets": targets(),
        "assignments": assignments,
        "modes": modes,
    }


def save_assignments(target_id: str, skill_ids: list[str], mode: str = "merge") -> dict:
    target_id = str(target_id or "").strip()
    valid_targets = {item["id"] for item in targets()}
    if target_id not in valid_targets:
        raise ValueError(f"unknown skill target: {target_id}")
    valid_skills = {item["id"] for item in skills.list_local()["items"]}
    normalized = list(dict.fromkeys(str(item) for item in skill_ids))
    unknown = [item for item in normalized if item not in valid_skills]
    if unknown:
        raise ValueError(f"unknown skill: {unknown[0]}")
    mode = str(mode or "merge").strip().casefold()
    if target_id != GLOBAL_TARGET and mode not in TARGET_MODES:
        raise ValueError("skill target mode must be merge or replace")

    _ensure_tables()
    with db_session() as database:
        database.execute("DELETE FROM skill_assignments WHERE target_id = ?", (target_id,))
        database.executemany(
            "INSERT INTO skill_assignments (target_id, skill_id) VALUES (?, ?)",
            [(target_id, skill_id) for skill_id in normalized],
        )
        if target_id != GLOBAL_TARGET:
            database.execute(
                """
                INSERT INTO skill_target_settings (target_id, mode)
                VALUES (?, ?)
                ON CONFLICT(target_id) DO UPDATE SET mode = excluded.mode
                """,
                (target_id, mode),
            )
    return configuration()


@contextmanager
def target_scope(target_id: str):
    catalog = _skills_for_target(target_id)
    context = {
        "target_id": target_id,
        "catalog": catalog,
        "loaded": {},
        "events": [],
        "selection_event_id": "",
        "selection_resolved": False,
    }
    if catalog:
        event = _skill_event(
            target_id,
            "selecting",
            "Selecting relevant skills",
            available=[
                {"name": item["name"], "description": item["description"]}
                for item in catalog
            ],
        )
        context["selection_event_id"] = event["id"]
        context["events"].append(event)
    token = _CONTEXT.set(context)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def available_skills() -> list[dict]:
    context = _CONTEXT.get()
    return list(context.get("catalog", [])) if context else []


def catalog_prompt() -> str:
    context = _CONTEXT.get()
    if not context or not context["catalog"]:
        return ""
    lines = [
        "Skills are optional workflows. Load only skills clearly relevant to the current request.",
        "Call load_skill before other tools when a listed skill is needed. Do not load skills speculatively.",
        f'<available_skills target="{escape(context["target_id"])}">',
    ]
    for item in context["catalog"]:
        lines.append(
            f'<skill name="{escape(item["name"])}">'
            f"<description>{escape(item['description'])}</description></skill>"
        )
        if sum(len(line) for line in lines) >= MAX_CATALOG_CHARS:
            break
    lines.append("</available_skills>")
    loaded = [item["name"] for item in context["loaded"].values()]
    if loaded:
        lines.append(f"Already loaded in this target: {', '.join(loaded)}.")
    return "\n".join(lines)


def loaded_messages() -> list[dict]:
    context = _CONTEXT.get()
    if not context:
        return []
    return [
        {"role": "system", "content": item["content"]}
        for item in context["loaded"].values()
    ]


def load_skill(name: str) -> str:
    context = _CONTEXT.get()
    if not context:
        raise ValueError("skills are not enabled for the current agent target")
    requested = str(name or "").strip().casefold()
    item = next(
        (entry for entry in context["catalog"] if entry["name"].casefold() == requested),
        None,
    )
    if item is None:
        raise ValueError(f"skill is not enabled for this agent target: {name}")
    if item["id"] in context["loaded"]:
        return context["loaded"][item["id"]]["content"]

    skill_file = Path(item["skill_file"])
    raw = skill_file.read_bytes()
    if len(raw) > MAX_SKILL_BYTES:
        raise ValueError(f"skill exceeds {MAX_SKILL_BYTES} bytes: {item['name']}")
    content = _without_frontmatter(raw.decode("utf-8", errors="replace")).strip()
    files = _supporting_files(skill_file.parent)
    rendered = "\n".join([
        f'<skill_content name="{escape(item["name"])}">',
        content,
        "",
        f"Base directory: {skill_file.parent}",
        "Relative paths in this skill are relative to the base directory.",
        "<skill_files>",
        *[f"<file>{path}</file>" for path in files],
        "</skill_files>",
        "</skill_content>",
    ])
    context["loaded"][item["id"]] = {"name": item["name"], "content": rendered}
    context["events"].append(_skill_event(
        context["target_id"],
        "loaded",
        item["name"],
        description=item["description"],
        path=str(skill_file),
        source=item["source"],
    ))
    return rendered


def pop_events() -> list[dict]:
    context = _CONTEXT.get()
    if not context:
        return []
    events = context["events"]
    context["events"] = []
    return events


def finish_selection(selected: list[str]) -> None:
    context = _CONTEXT.get()
    if not context or context["selection_resolved"]:
        return
    context["selection_resolved"] = True
    context["events"].append({
        "op": "update",
        "id": context["selection_event_id"],
        "patch": {
            "status": "done",
            "name": "Skill selection complete",
            "selected": selected,
        },
    })


def tool_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "Load one relevant skill from the skills available to the current agent target.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact skill name from available_skills.",
                    },
                },
                "required": ["name"],
            },
        },
    }


def _skills_for_target(target_id: str) -> list[dict]:
    config = configuration()
    assignments = config["assignments"]
    selected = list(assignments.get(target_id, []))
    if target_id != GLOBAL_TARGET and config["modes"].get(target_id, "merge") == "merge":
        selected = list(assignments.get(GLOBAL_TARGET, [])) + selected
    selected = list(dict.fromkeys(selected))
    by_id = {item["id"]: item for item in skills.list_local()["items"]}
    return [by_id[skill_id] for skill_id in selected if skill_id in by_id]


def _ensure_tables() -> None:
    with db_session() as database:
        database.execute("""
            CREATE TABLE IF NOT EXISTS skill_assignments (
                target_id TEXT NOT NULL,
                skill_id TEXT NOT NULL,
                PRIMARY KEY (target_id, skill_id)
            )
        """)
        database.execute("""
            CREATE TABLE IF NOT EXISTS skill_target_settings (
                target_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL
            )
        """)


def _without_frontmatter(content: str) -> str:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return content
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1:])
    return content


def _supporting_files(skill_dir: Path) -> list[str]:
    result = []
    for path in sorted(skill_dir.rglob("*")):
        if path.is_file() and path.name != "SKILL.md":
            result.append(str(path.resolve()))
            if len(result) >= MAX_SUPPORTING_FILES:
                break
    return result


def _skill_event(
    target_id: str,
    status: str,
    name: str,
    **data,
) -> dict:
    return {
        "op": "start",
        "id": f"skill-{uuid4().hex[:10]}",
        "event": "skill",
        "data": {
            "target_id": target_id,
            "status": status,
            "name": name,
            **data,
        },
    }
