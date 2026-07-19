from __future__ import annotations

import os
import re

def _attach_session_relationship(analysis: dict, existing_tasks: list[dict]) -> None:
    parent = str(analysis.get("parent_goal_id") or "").strip()
    if parent and any(item.get("id") == parent for item in existing_tasks):
        analysis["root_goal_id"] = parent


def _session_context(state: dict) -> dict:
    observations = _observations_freshness(state)
    knowledge = _knowledge_freshness({**state, "observations": observations})
    return {
        "tasks": state.get("taskItems", []),
        "goals": [item for item in state.get("taskItems", []) if item.get("kind") == "goal"],
        "recent_user_messages": [item.get("content", "") for item in state.get("messages", []) if item.get("role") == "user"][-5:],
        "recent_turns": _recent_conversation_turns(state.get("messages", [])),
        "observations": observations,
        "knowledge": knowledge,
        "investigations": state.get("investigations", []),
    }


def _select_session_memory(message: str, analysis: dict | None, session_context: dict) -> dict:
    if not session_context:
        return {}
    query = _memory_query(message, analysis)
    reuse_ids = set(analysis.get("reused_context_ids", [])) if isinstance(analysis, dict) else set()
    if isinstance(analysis, dict):
        reuse_ids |= _task_scoped_memory_ids(session_context, str(analysis.get("id") or ""))
    goals = _rank_memory_items(query, session_context.get("goals", []), ("text",), limit=3, pinned_ids=reuse_ids)
    if not goals:
        goals = [item for item in session_context.get("goals", []) if isinstance(item, dict)][:3]
    tasks = _rank_memory_items(query, session_context.get("tasks", []), ("text", "reason"), limit=12, pinned_ids=reuse_ids)
    knowledge = _rank_memory_items(
        query,
        [item for item in session_context.get("knowledge", []) if item.get("fresh", True)],
        ("statement", "id"),
        limit=10,
        pinned_ids=reuse_ids,
    )
    observation_ids = {
        obs_id
        for item in knowledge
        for obs_id in item.get("observation_ids", [])
    }
    observations = _rank_memory_items(
        query,
        [item for item in session_context.get("observations", []) if item.get("fresh")],
        ("summary", "title", "path", "tool", "id"),
        limit=12,
        pinned_ids=observation_ids | reuse_ids,
    )
    investigations = _rank_memory_items(
        query,
        session_context.get("investigations", []),
        ("request", "summary", "id"),
        limit=5,
        pinned_ids=reuse_ids,
    )
    return {
        "tasks": tasks,
        "goals": goals,
        "recent_user_messages": session_context.get("recent_user_messages", [])[-3:],
        "recent_turns": session_context.get("recent_turns", [])[-4:],
        "observations": observations,
        "knowledge": knowledge,
        "investigations": investigations,
    }


def _task_scoped_memory_ids(session_context: dict, task_id: str) -> set[str]:
    task_id = str(task_id or "").strip()
    if not task_id:
        return set()
    prefix = f"{task_id}:"
    ids: set[str] = set()
    for bucket in ("goals", "tasks", "observations", "knowledge", "investigations"):
        for item in session_context.get(bucket, []):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            if item_id == task_id or item_id.startswith(prefix):
                ids.add(item_id)
            if bucket == "knowledge":
                for obs_id in item.get("observation_ids", []) if isinstance(item.get("observation_ids"), list) else []:
                    obs_id = str(obs_id or "")
                    if obs_id == task_id or obs_id.startswith(prefix):
                        ids.add(obs_id)
    return ids


def _memory_query(message: str, analysis: dict | None) -> set[str]:
    parts = [message]
    if isinstance(analysis, dict):
        intent = analysis.get("intent") if isinstance(analysis.get("intent"), dict) else {}
        parts.append(intent.get("summary", ""))
        parts.extend(item.get("text", "") for item in analysis.get("acceptance_criteria", []) if isinstance(item, dict))
        parts.extend(item.get("question", "") for item in analysis.get("unknowns", []) if isinstance(item, dict))
        parts.extend(analysis.get("constraints", []) if isinstance(analysis.get("constraints"), list) else [])
    return _memory_terms(" ".join(str(part or "") for part in parts))


def _rank_memory_items(
    query: set[str],
    items: list[dict],
    fields: tuple[str, ...],
    *,
    limit: int,
    pinned_ids: set[str] | None = None,
) -> list[dict]:
    pinned_ids = pinned_ids or set()
    ranked = []
    for index, item in enumerate(item for item in items if isinstance(item, dict)):
        item_id = str(item.get("id") or "")
        text = " ".join(str(item.get(field) or "") for field in fields)
        score = len(query & _memory_terms(text))
        if item_id in pinned_ids:
            score += 100
        if score > 0:
            ranked.append((score, index, item))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [item for _, _, item in ranked[:limit]]


def _memory_terms(value: str) -> set[str]:
    text = str(value or "").casefold()
    terms = {match.group(0) for match in re.finditer(r"[a-z0-9_./:-]{2,}", text)}
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(chunk) == 1:
            terms.add(chunk)
        else:
            terms.update(chunk[index:index + 2] for index in range(len(chunk) - 1))
    return terms


def _session_context_lines(session_context: dict | None) -> list[str]:
    if not session_context:
        return []
    lines = []
    lines.extend(f"Session goal: {item.get('text')}" for item in session_context.get("goals", [])[:3])
    lines.extend(f"Known: {item.get('statement')}" for item in session_context.get("knowledge", [])[:8] if item.get("fresh", True))
    lines.extend(f"Prior investigation: {item.get('summary')}" for item in session_context.get("investigations", [])[:3] if item.get("summary"))
    for turn in session_context.get("recent_turns", [])[-4:]:
        if turn.get("user"):
            lines.append(f"Recent user: {turn['user']}")
        if turn.get("assistant"):
            lines.append(f"Recent assistant: {turn['assistant']}")
    return lines


def _recent_conversation_turns(messages: list[dict]) -> list[dict]:
    turns = []
    pending_user = ""
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "user":
            pending_user = _clip_memory_line(message.get("content", ""))
        elif role == "assistant":
            assistant = _clip_memory_line(_assistant_summary_from_message(message))
            if pending_user or assistant:
                turns.append({"user": pending_user, "assistant": assistant})
                pending_user = ""
    if pending_user:
        turns.append({"user": pending_user, "assistant": ""})
    return turns[-4:]


def _assistant_summary_from_message(message: dict) -> str:
    for event in reversed(message.get("events", []) if isinstance(message.get("events"), list) else []):
        if not isinstance(event, dict) or event.get("type") != "output":
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        content = data.get("content")
        if content:
            return str(content)
    return ""


def _clip_memory_line(value: str, limit: int = 320) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _observations_freshness(state: dict) -> list[dict]:
    items = []
    for raw in state.get("observations", []):
        item = dict(raw)
        path = item.get("path")
        fresh = not bool(path)
        if path and os.path.exists(path):
            stat = os.stat(path)
            fresh = item.get("mtime_ns") == stat.st_mtime_ns and item.get("size") == stat.st_size
        item["fresh"] = fresh
        items.append(item)
    return items


def _knowledge_freshness(state: dict) -> list[dict]:
    observations = state.get("observations", [])
    by_id = {item.get("id"): item for item in observations}
    by_call = {_call_key(item.get("id")): item for item in observations if _call_key(item.get("id"))}
    result = []
    for raw in state.get("knowledge", []):
        item = dict(raw)
        ids = []
        fresh = True
        for obs_id in item.get("observation_ids", []):
            observation = by_id.get(obs_id) or by_call.get(_call_key(obs_id))
            if observation:
                ids.append(observation["id"])
                fresh = fresh and observation.get("fresh", True)
            else:
                ids.append(obs_id)
                fresh = False
        item["observation_ids"] = ids
        item["fresh"] = fresh
        result.append(item)
    return result


def _call_key(value: str | None) -> str:
    text = str(value or "").split(":")[-1]
    return re.sub(r"call_\d+_", "call_", text)
