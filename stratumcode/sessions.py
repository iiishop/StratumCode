from __future__ import annotations

import json
from datetime import datetime, timezone

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
_USAGE_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_tokens", "total_tokens")


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
        "title": "",
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


def usage_events(workspace_id: int | None = None) -> dict:
    _ensure_table()
    with db_session() as db:
        if workspace_id is None:
            rows = db.execute(
                """
                SELECT s.id, s.workspace_id, s.name, s.state_json, s.usage_json,
                       s.created_at, s.updated_at, w.name AS workspace_name, w.path AS workspace_path
                FROM sessions s
                LEFT JOIN workspaces w ON w.id = s.workspace_id
                ORDER BY datetime(s.created_at) DESC, s.id DESC
                """
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT s.id, s.workspace_id, s.name, s.state_json, s.usage_json,
                       s.created_at, s.updated_at, w.name AS workspace_name, w.path AS workspace_path
                FROM sessions s
                LEFT JOIN workspaces w ON w.id = s.workspace_id
                WHERE s.workspace_id = ?
                ORDER BY datetime(s.created_at) DESC, s.id DESC
                """,
                (int(workspace_id),),
            ).fetchall()
    records = []
    total = _default_state()["usage"].copy()
    for row in rows:
        state = _loads(row["state_json"], {})
        session_total = _loads(row["usage_json"], {})
        _add_usage_total(total, session_total)
        records.extend(_usage_records_for_session(dict(row), state))
    records.sort(key=lambda item: item["timestamp"])
    total["costs_by_currency"] = _costs_by_currency(records)
    return {"records": records, "total": total}


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


def _usage_records_for_session(session: dict, state: dict) -> list[dict]:
    if not isinstance(state, dict):
        return []
    records = []
    current_stage = {}
    for message in state.get("messages", []):
        if not isinstance(message, dict):
            continue
        for event in message.get("events", []):
            if not isinstance(event, dict):
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            event_type = event.get("type")
            if event_type == "stage":
                current_stage = {**current_stage, **data}
                continue
            if event_type != "usage":
                continue
            delta = data.get("delta") if isinstance(data.get("delta"), dict) else {}
            records.append({
                "timestamp": _event_timestamp(event, session),
                "session_id": session["id"],
                "session_name": session["name"],
                "workspace_id": session.get("workspace_id"),
                "workspace_name": str(session.get("workspace_name") or "").strip(),
                "workspace_path": str(session.get("workspace_path") or "").strip(),
                "provider": str(data.get("provider") or current_stage.get("provider") or "").strip(),
                "model": str(data.get("model") or current_stage.get("model") or "").strip(),
                "stage": str(data.get("stage") or current_stage.get("name") or current_stage.get("phase") or "").strip(),
                "input_tokens": _int(delta.get("input_tokens")),
                "output_tokens": _int(delta.get("output_tokens")),
                "cached_tokens": _int(delta.get("cached_tokens")),
                "total_tokens": _int(delta.get("total_tokens")),
                "cost": _float(delta.get("cost")),
                "currency": str(delta.get("currency") or data.get("currency") or "USD"),
                "pricing": delta.get("pricing") if isinstance(delta.get("pricing"), dict) else {},
            })
    return records


def _event_timestamp(event: dict, session: dict) -> str:
    created_at = event.get("createdAt")
    if isinstance(created_at, (int, float)):
        return datetime.fromtimestamp(float(created_at) / 1000, tz=timezone.utc).isoformat()
    if isinstance(created_at, str) and created_at.strip():
        cleaned = created_at.strip()
        if cleaned.isdigit():
            return datetime.fromtimestamp(float(cleaned) / 1000, tz=timezone.utc).isoformat()
        return cleaned
    return str(session.get("updated_at") or session.get("created_at") or "")


def _add_usage_total(total: dict, usage: dict) -> None:
    if not isinstance(usage, dict):
        return
    for key in _USAGE_TOKEN_FIELDS:
        total[key] = _int(total.get(key)) + _int(usage.get(key))
    total["cost"] = round(_float(total.get("cost")) + _float(usage.get("cost")), 6)
    total["currency"] = str(usage.get("currency") or total.get("currency") or "USD")


def _costs_by_currency(records: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for record in records:
        currency = str(record.get("currency") or "").strip() or "USD"
        cost = _float(record.get("cost"))
        if cost == 0:
            continue
        totals[currency] = round(totals.get(currency, 0.0) + cost, 6)
    return totals


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


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


def save_state(session_id: int, state: dict | None = None, increment: dict | None = None) -> None:
    """Persist session state.

    Two modes:
    - full: ``state`` replaces/merges the whole snapshot (legacy path,
      low-frequency: unload, answer submit).
    - incremental: only newly appended messages/events are sent; merge into
      the stored baseline. Keeps per-save cost bounded regardless of session
      length — the frontend previously full-serialized the whole tree on
      every streamed event, which inflated the V8 heap (old space grows,
      never shrinks).
    """
    if state is not None:
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
        return
    if increment is not None:
        _apply_increment(session_id, increment)
        return
    raise ValueError("state or increment required")


def _apply_increment(session_id: int, increment: dict) -> None:
    """Merge an event-level increment into the stored session state.

    ``increment`` shape (from the frontend chat timeline):
      { new_messages: [message, ...],   # whole new messages (incl. all events)
        appends:      [{ id, events: [...] }, ...],  # new events on existing messages
        usage:        {...} | None }
    Events are upserted by id: re-sent events (active/streaming ones that the
    frontend keeps including until they finish) overwrite in place.
    """
    if not isinstance(increment, dict):
        raise ValueError("increment must be an object")
    state = get(session_id)["state"]
    new_messages = increment.get("new_messages") or []
    appends = increment.get("appends") or []
    usage = increment.get("usage")

    msgs = state.get("messages")
    if not isinstance(msgs, list):
        msgs = []
    by_id = {m["id"]: m for m in msgs if isinstance(m, dict) and m.get("id") is not None}

    for nm in new_messages:
        if not isinstance(nm, dict):
            continue
        mid = nm.get("id")
        if mid in by_id:
            # Defensive: a whole-message resend overwrites in place.
            by_id[mid].clear()
            by_id[mid].update(nm)
        else:
            by_id[mid] = nm
            msgs.append(nm)

    for ap in appends:
        if not isinstance(ap, dict):
            continue
        msg = by_id.get(ap.get("id"))
        if not isinstance(msg, dict):
            continue
        events = msg.get("events")
        if not isinstance(events, list):
            events = []
            msg["events"] = events
        ev_by_id = {e["id"]: e for e in events if isinstance(e, dict) and e.get("id") is not None}
        for ev in ap.get("events") or []:
            if not isinstance(ev, dict):
                continue
            eid = ev.get("id")
            if eid in ev_by_id:
                ev_by_id[eid].update(ev)
            else:
                if eid is not None:
                    ev_by_id[eid] = ev
                events.append(ev)

    state["messages"] = msgs
    # 其余状态量小、增长慢：随增量全量覆盖，保证会话恢复不丢这些部分
    for key in ("evidenceRuns", "taskAnalyses", "subagentRuns", "fileContext", "activeRunId"):
        if key in increment:
            state[key] = increment[key]
    if isinstance(usage, dict):
        state["usage"] = usage
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
    from .status.task_updates import _merge_task_items

    return _merge_task_items(old, new)


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

def generate_title(session_id: int, user_message: str, ai_response: str) -> str:
    """Generate a short title (≤10 characters) for a session using the title model."""
    from . import agent_runtime, model_settings

    setting = model_settings.resolve(model_settings.TITLE_STAGE)
    if setting is None:
        raise RuntimeError("title model is not configured")
    messages = [
        {
            "role": "system",
            "content": (
                "Generate a concise title summarizing the conversation topic. "
                "The title must be 10 characters or fewer. "
                "Reply with only the title text, no quotes, no punctuation, no explanation."
            ),
        },
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": ai_response},
    ]
    assistant = agent_runtime.call_model(
        setting["provider"],
        setting["model_id"],
        messages,
        use_skills=False,
    )
    title = (assistant.get("content") or "").strip()
    if title:
        rename(session_id, title)
    return title
