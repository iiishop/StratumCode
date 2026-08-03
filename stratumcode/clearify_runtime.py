from __future__ import annotations

import threading
from uuid import uuid4


_PENDING: dict[str, tuple[threading.Event, dict]] = {}
_LOCK = threading.Lock()


def create_pending() -> str:
    question_id = f"clearify-{uuid4().hex[:8]}"
    with _LOCK:
        _PENDING[question_id] = (threading.Event(), {})
    return question_id


def answer(question_id: str, payload: dict) -> bool:
    with _LOCK:
        item = _PENDING.get(question_id)
        if item is None:
            return False
        event, slot = item
        slot.update(payload)
        event.set()
        return True


def wait(question_id: str) -> dict:
    with _LOCK:
        item = _PENDING.get(question_id)
    if item is None:
        raise ValueError("unknown clearify question")
    event, slot = item
    event.wait()
    with _LOCK:
        _PENDING.pop(question_id, None)
    return dict(slot)
