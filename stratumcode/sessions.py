from __future__ import annotations

import json
from datetime import datetime

from .db import db_session

SESSION_SCHEMA = """
    CREATE TABLE sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        state_json TEXT NOT NULL DEFAULT '{}',
        usage_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
    )
"""


def _ensure_table() -> None:
    with db_session() as db:
        db.execute(SESSION_SCHEMA.replace("CREATE TABLE sessions", "CREATE TABLE IF NOT EXISTS sessions"))
        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(sessions)").fetchall()
        }
        id_column = next(
            row for row in db.execute("PRAGMA table_info(sessions)").fetchall()
            if row["name"] == "id"
        )
        if str(id_column["type"]).upper() != "INTEGER":
            _migrate_legacy_sessions(db)
            return
        if "state_json" not in columns:
            db.execute("ALTER TABLE sessions ADD COLUMN state_json TEXT NOT NULL DEFAULT '{}'")
            db.execute(
                "UPDATE sessions SET state_json = ? WHERE state_json = '{}'",
                (json.dumps(_default_state(), ensure_ascii=False),),
            )
        if "usage_json" not in columns:
            db.execute("ALTER TABLE sessions ADD COLUMN usage_json TEXT NOT NULL DEFAULT '{}'")
            db.execute(
                "UPDATE sessions SET usage_json = ? WHERE usage_json = '{}'",
                (json.dumps(_default_state()["usage"], ensure_ascii=False),),
            )
        if "updated_at" not in columns:
            db.execute("ALTER TABLE sessions ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
            db.execute(
                """
                UPDATE sessions
                SET updated_at = COALESCE(NULLIF(created_at, ''), CURRENT_TIMESTAMP)
                WHERE updated_at = ''
                """
            )


def _migrate_legacy_sessions(db) -> None:
    rows = db.execute("SELECT rowid, * FROM sessions ORDER BY rowid").fetchall()
    columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)").fetchall()}
    db.execute("ALTER TABLE sessions RENAME TO sessions_legacy")
    db.execute(SESSION_SCHEMA)
    for row in rows:
        state = _loads(row["state_json"], {}) if "state_json" in columns else {}
        usage = _loads(row["usage_json"], {}) if "usage_json" in columns else {}
        if not state:
            state = _default_state()
            if "messages_json" in columns:
                state["messages"] = _loads(row["messages_json"], [])
        if not usage and "token_usage_json" in columns:
            old_usage = _loads(row["token_usage_json"], {})
            usage = {
                "input_tokens": old_usage.get("prompt", 0),
                "output_tokens": old_usage.get("completion", 0),
                "total_tokens": old_usage.get("total", 0),
            }
        usage = {**_default_state()["usage"], **usage}
        state = {**_default_state(), **state, "usage": usage}
        db.execute(
            """
            INSERT INTO sessions (
                workspace_id, name, state_json, usage_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["workspace_id"],
                row["name"],
                json.dumps(state, ensure_ascii=False),
                json.dumps(usage, ensure_ascii=False),
                row["created_at"],
                row["updated_at"],
            ),
        )
    db.execute("DROP TABLE sessions_legacy")


def _default_state() -> dict:
    return {
        "messages": [],
        "evidenceRuns": [],
        "activeRunId": "",
        "taskItems": [],
        "observations": [],
        "investigations": [],
        "knowledge": [],
        "fileContext": [],
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "cost": 0,
            "currency": "USD",
        },
    }


def _created_name() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _loads(value: str, fallback):
    try:
        parsed = json.loads(value or "")
    except json.JSONDecodeError:
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def create(workspace_id: int) -> dict:
    _ensure_table()
    name = _created_name()
    state = _default_state()
    usage = state["usage"]
    with db_session() as db:
        cursor = db.execute(
            """
            INSERT INTO sessions (workspace_id, name, state_json, usage_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                int(workspace_id),
                name,
                json.dumps(state, ensure_ascii=False),
                json.dumps(usage, ensure_ascii=False),
            ),
        )
        session_id = int(cursor.lastrowid)
    return get(session_id)


def list_by_workspace(workspace_id: int) -> list[dict]:
    _ensure_table()
    with db_session() as db:
        rows = db.execute(
            """
            SELECT id, workspace_id, name, usage_json, created_at, updated_at
            FROM sessions
            WHERE workspace_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            """,
            (int(workspace_id),),
        ).fetchall()
    items = []
    for row in rows:
        usage = _loads(row["usage_json"], {})
        items.append({**dict(row), "usage": usage})
    return items


def get(session_id: int) -> dict:
    _ensure_table()
    with db_session() as db:
        row = db.execute(
            """
            SELECT id, workspace_id, name, state_json, usage_json, created_at, updated_at
            FROM sessions
            WHERE id = ?
            """,
            (int(session_id),),
        ).fetchone()
    if row is None:
        raise ValueError("session not found")
    state = _loads(row["state_json"], _default_state())
    usage = _loads(row["usage_json"], state.get("usage", {}))
    return {**dict(row), "state": state, "usage": usage}


def rename(session_id: int, name: str) -> None:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("session name is required")
    _ensure_table()
    with db_session() as db:
        db.execute(
            "UPDATE sessions SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (cleaned, int(session_id)),
        )


def save_state(session_id: int, state: dict) -> None:
    if not isinstance(state, dict):
        raise ValueError("state must be an object")
    current = get(session_id)["state"]
    state = {**current, **state}
    state["taskItems"] = _merge_by_task(current.get("taskItems", []), state.get("taskItems", []))
    state["knowledge"] = _merge_by_id(current.get("knowledge", []), state.get("knowledge", []))
    state["investigations"] = _merge_by_id(current.get("investigations", []), state.get("investigations", []))
    state["observations"] = _cap_observations(
        _merge_by_id(current.get("observations", []), state.get("observations", [])),
        state["knowledge"],
    )
    _write_state(session_id, state)


def _write_state(session_id: int, state: dict) -> None:
    usage = state.get("usage") if isinstance(state.get("usage"), dict) else {}
    _ensure_table()
    with db_session() as db:
        db.execute(
            """
            UPDATE sessions
            SET state_json = ?, usage_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                json.dumps(state, ensure_ascii=False),
                json.dumps(usage, ensure_ascii=False),
                int(session_id),
            ),
        )


def delete(session_id: int) -> None:
    _ensure_table()
    with db_session() as db:
        cursor = db.execute("DELETE FROM sessions WHERE id = ?", (int(session_id),))
        if cursor.rowcount == 0:
            raise ValueError("session not found")


def merge_investigation(
    session_id: int,
    task_items: list[dict],
    observations: list[dict],
    *,
    investigation: dict | None = None,
    knowledge: list[dict] | None = None,
) -> None:
    state = get(session_id)["state"]
    state["taskItems"] = _merge_by_task(state.get("taskItems", []), task_items or [])
    state["observations"] = _cap_observations(_merge_by_id(state.get("observations", []), observations or []), state.get("knowledge", []) + (knowledge or []))
    if investigation:
        state["investigations"] = _merge_by_id(state.get("investigations", []), [investigation])
    if knowledge:
        state["knowledge"] = _merge_by_id(state.get("knowledge", []), knowledge)
        state["observations"] = _cap_observations(state["observations"], state["knowledge"])
    _write_state(session_id, state)


def _merge_by_id(old: list[dict], new: list[dict]) -> list[dict]:
    merged = [item for item in old if isinstance(item, dict)]
    by_id = {item.get("id"): index for index, item in enumerate(merged) if item.get("id")}
    for item in new:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if item_id and item_id in by_id:
            merged[by_id[item_id]] = {**merged[by_id[item_id]], **item}
        else:
            if item_id:
                by_id[item_id] = len(merged)
            merged.append(item)
    return merged


def _merge_by_task(old: list[dict], new: list[dict]) -> list[dict]:
    merged = [item for item in old if isinstance(item, dict)]
    for item in new:
        if not isinstance(item, dict) or not item.get("text"):
            continue
        index = next((i for i, row in enumerate(merged) if _same_task(row, item)), None)
        if index is None:
            merged.append(item)
            continue
        if merged[index].get("kind") == "goal":
            continue
        if _status_rank(item.get("status")) < _status_rank(merged[index].get("status")):
            item = {**item, "status": merged[index].get("status")}
        merged[index] = {**merged[index], **item, "id": merged[index].get("id") or item.get("id")}
    return merged


def _same_task(left: dict, right: dict) -> bool:
    if _same_task_id(left.get("id"), right.get("id")):
        return True
    if _same_task_id(left.get("id"), right.get("target_id")) or _same_task_id(right.get("id"), left.get("target_id")):
        return True
    left_trace = left.get("trace") if isinstance(left.get("trace"), list) else []
    right_trace = right.get("trace") if isinstance(right.get("trace"), list) else []
    left_ids = [left.get("id"), *left_trace]
    right_ids = [right.get("id"), *right_trace]
    return any(_same_task_id(left_id, right_id) for left_id in left_ids for right_id in right_ids)


    return re.sub(r"\W+", "", re.sub(r"[（(][^）)]*[）)]", "", value or "")).casefold()


def _task_id_tail(value: str | None) -> str:
    return str(value or "").rsplit(":", 1)[-1]


def _same_task_id(left: str | None, right: str | None) -> bool:
    left = str(left or "")
    right = str(right or "")
    if not left or not right:
        return False
    if left == right:
        return True
    if ":" in left and ":" in right:
        return False
    return _task_id_tail(left) == _task_id_tail(right)


def _status_rank(status: str | None) -> int:
    return {"unknown": 0, "pending": 0, "added": 1, "updated": 1, "deferred": 2, "blocked": 2, "active": 2, "known": 3}.get(status or "", 0)


def _cap_observations(observations: list[dict], knowledge: list[dict]) -> list[dict]:
    pinned = {
        obs_id
        for item in knowledge
        if isinstance(item, dict)
        for obs_id in item.get("observation_ids", [])
    }
    tail = observations[-40:]
    tail_ids = {item.get("id") for item in tail}
    extras = [item for item in observations if item.get("id") in pinned and item.get("id") not in tail_ids]
    return extras + tail
