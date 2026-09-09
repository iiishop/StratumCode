"""Ordered, lossless session history, independent of semantic extraction."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from .models import MemoryDelta, MemoryEvidence, MemoryRecord

_TURN: ContextVar[str] = ContextVar("memory_turn", default="")


@contextmanager
def turn_scope(turn_id: str):
    token = _TURN.set(turn_id)
    try:
        yield
    finally:
        _TURN.reset(token)


def current_turn_id() -> str:
    return _TURN.get()


def conversation_id(session_id: int | None, turn_id: str) -> str:
    return "conversation-" + hashlib.sha256(f"{session_id}:{turn_id}".encode()).hexdigest()[:24]


def conversation_delta(session_id: int | None, turn_id: str, request: str, output: str) -> MemoryDelta:
    if session_id is None or not output.strip():
        return MemoryDelta()
    record_id = conversation_id(session_id, turn_id)
    record = MemoryRecord(
        id=record_id, scope="session", kind="conversation", subject_kind="turn",
        subject_key=f"session:{session_id}:{turn_id}", statement=output,
        confidence="verified", status="accepted", freshness="fresh",
        session_id=session_id, turn_id=turn_id, source="conversation_runtime",
        payload={"request": request, "role": "assistant", "verbatim": True,
                 "captured_at": datetime.now(timezone.utc).isoformat()},
    )
    return MemoryDelta(records=[record], evidence=[MemoryEvidence(
        id=f"{record_id}-quote", record_id=record_id, kind="conversation",
        excerpt=output, payload={"validated_source": True, "role": "assistant"},
    )])


def turns_from_messages(messages: list[dict]) -> list[dict]:
    result = []
    request = ""
    for message in messages:
        if message.get("role") == "user":
            request = str(message.get("content") or "")
        elif message.get("role") == "assistant":
            outputs = [e.get("data", {}).get("content", "") for e in message.get("events", [])
                       if e.get("type") == "output" and e.get("data", {}).get("visibility") != "diagnostic"
                       and not e.get("data", {}).get("streaming")]
            answer = str(message.get("content") or (outputs[-1] if outputs else ""))
            if request and answer.strip():
                result.append({"turn_id": f"message-{message.get('id', '')}",
                               "user": request, "assistant": answer,
                               "created_at": message.get("time", ""),
                               "order": int(str(message.get("id"))) / 1000 if str(message.get("id", "")).isdigit() else len(result)})
                request = ""
    return result


def recent_turns(workspace_dir: str, session_id: int | None, *, limit: int = 4) -> list[dict]:
    if session_id is None:
        return []
    from .. import sessions, workspaces
    from . import store
    try:
        session = sessions.get(session_id)
        owner = next((w for w in workspaces.list_all(workspace_dir) if w["id"] == session.get("workspace_id")), None)
        if owner and Path(owner["path"]).resolve() != Path(workspace_dir).resolve():
            return []
        saved = turns_from_messages(session["state"].get("messages", []))
    except (ValueError, KeyError):
        saved = []
    records = store.list_conversations(workspace_dir, session_id, limit=limit)
    # The server writes an exchange before semantic extraction, possibly before the UI saves it.
    result = saved[-limit:]
    seen = {(t["user"], t["assistant"]) for t in result}
    for record in reversed(records):
        request = record.get("payload", {}).get("request", "")
        answer = record.get("statement", "")
        if (request, answer) in seen:
            for turn in result:
                if turn["user"] == request and turn["assistant"] == answer:
                    turn.update(record_id=record["id"], turn_id=record["turn_id"])
            continue
        # Avoid appending old server history after a newer UI snapshot.
        if saved and any(t["user"] == request and t["assistant"] == answer for t in saved):
            continue
        captured = record.get("payload", {}).get("extra", {}).get("captured_at") or record.get("created_at", "")
        try:
            order = datetime.fromisoformat(captured).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            order = 0
        result.append({"turn_id": record["turn_id"], "user": request, "assistant": answer,
                       "record_id": record["id"], "created_at": captured, "order": order})
        seen.add((request, answer))
    return sorted(result, key=lambda turn: turn.get("order", 0))[-limit:]
