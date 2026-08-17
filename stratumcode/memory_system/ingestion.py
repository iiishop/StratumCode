from __future__ import annotations

from uuid import uuid4

from .compressor import summaries_for_records
from .freshness import file_fingerprint
from .llm import call_memory_json
from .models import ConversationRef, MemoryDelta, MemoryEvidence, MemoryLink, MemoryRecord


def delta_from_events(
    *,
    workspace_dir: str,
    session_id: int | None,
    turn_id: str,
    events: list[dict],
    assistant_output: str = "",
) -> MemoryDelta:
    delta = _llm_delta(
        workspace_dir=workspace_dir,
        session_id=session_id,
        turn_id=turn_id,
        source="events",
        payload={
            "events": _memory_event_payload(events),
            "assistant_output": assistant_output,
        },
    )
    delta.records.extend(summaries_for_records(delta.records, session_id=session_id, turn_id=turn_id))
    return delta


def delta_from_output(*, session_id: int | None, turn_id: str, output: str) -> MemoryDelta:
    return _llm_delta(
        workspace_dir=".",
        session_id=session_id,
        turn_id=turn_id,
        source="assistant_output",
        payload={"assistant_output": output},
    )


def _llm_delta(
    *,
    workspace_dir: str,
    session_id: int | None,
    turn_id: str,
    source: str,
    payload: dict,
) -> MemoryDelta:
    data = call_memory_json("extract_delta", {
        "source": source,
        "turn_id": turn_id,
        **payload,
    })
    if not isinstance(data, dict):
        return MemoryDelta()
    records, index = _records_from_llm(data.get("records"), session_id=session_id, turn_id=turn_id)
    return MemoryDelta(
        records=records,
        evidence=_evidence_from_llm(data.get("evidence"), records, index, workspace_dir),
        links=_links_from_llm(data.get("links"), index),
        refs=_refs_from_llm(data.get("refs"), session_id=session_id, turn_id=turn_id, record_index=index),
    )


def _memory_event_payload(events: list[dict]) -> list[dict]:
    payload = []
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        item = {
            "op": event.get("op", ""),
            "event": event.get("event", ""),
        }
        for key in ("investigation", "validation_result", "implementation", "patch_plan", "design_plan"):
            if isinstance(event.get(key), dict):
                item[key] = event[key]
        if data:
            item["data"] = {
                key: data.get(key)
                for key in ("name", "summary", "content", "status", "phase", "verdict", "path", "output")
                if key in data
            }
        payload.append(item)
    return payload


def _records_from_llm(value: object, *, session_id: int | None, turn_id: str) -> tuple[list[MemoryRecord], dict[int, str]]:
    records = []
    index = {}
    for position, item in enumerate(value if isinstance(value, list) else [], start=1):
        if not isinstance(item, dict):
            continue
        record = _record_from_llm(item, session_id=session_id, turn_id=turn_id)
        if record is None:
            continue
        records.append(record)
        index[position] = record.id
    return records, index


def _record_from_llm(item: dict, *, session_id: int | None, turn_id: str) -> MemoryRecord | None:
    scope = _enum(item.get("scope"), {"turn", "session", "project"})
    kind = _text(item.get("kind"))
    subject_kind = _text(item.get("subject_kind"))
    subject_key = _text(item.get("subject_key"))
    statement = _text(item.get("statement"))
    confidence = _enum(item.get("confidence"), {"verified", "inferred", "uncertain"})
    status = _enum(item.get("status"), {"accepted", "pending"})
    freshness = _enum(item.get("freshness"), {"fresh", "unknown"})
    if not all((scope, kind, subject_kind, subject_key, statement, confidence, status, freshness)):
        return None
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return MemoryRecord(
        id=f"mem-{uuid4().hex[:12]}",
        scope=scope,
        kind=kind,
        subject_kind=subject_kind,
        subject_key=subject_key,
        statement=statement,
        confidence=confidence,
        status=status,
        freshness=freshness,
        session_id=session_id,
        turn_id=turn_id,
        source=_text(item.get("source")) or "memory_llm",
        source_record_ids=_string_list(item.get("source_record_ids")),
        payload=payload,
    )


def _evidence_from_llm(value: object, records: list[MemoryRecord], index: dict[int, str], workspace_dir: str) -> list[MemoryEvidence]:
    evidence = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        record_id = _record_id_from_index(item.get("record_index"), records, index)
        kind = _text(item.get("kind"))
        excerpt = _text(item.get("excerpt"))
        if not record_id or not kind or not excerpt:
            continue
        path = _text(item.get("path"))
        evidence.append(MemoryEvidence(
            id=f"evidence-{uuid4().hex[:12]}",
            record_id=record_id,
            kind=kind,
            path=path,
            excerpt=excerpt,
            fingerprint=file_fingerprint(workspace_dir, path) if path else {},
            payload=item.get("payload") if isinstance(item.get("payload"), dict) else {},
        ))
    return evidence


def _links_from_llm(value: object, index: dict[int, str]) -> list[MemoryLink]:
    links = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        source_id = index.get(_positive_int(item.get("source_record_index")))
        target_id = index.get(_positive_int(item.get("target_record_index")))
        relation = _text(item.get("relation"))
        if source_id and target_id and relation:
            links.append(MemoryLink(source_id=source_id, target_id=target_id, relation=relation))
    return links


def _refs_from_llm(
    value: object,
    *,
    session_id: int | None,
    turn_id: str,
    record_index: dict[int, str],
) -> list[ConversationRef]:
    refs = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        kind = _enum(item.get("kind"), {"phase", "item", "risk", "action", "decision", "section"})
        index = _positive_int(item.get("index"))
        label = _text(item.get("label"))
        content = _text(item.get("content"))
        if not kind or index <= 0 or not label or not content:
            continue
        refs.append(ConversationRef(
            id=f"ref-{uuid4().hex[:12]}",
            session_id=session_id,
            turn_id=turn_id,
            ref_key=f"assistant:{turn_id}:{kind}:{index}",
            label=label,
            content=content,
            target_record_id=record_index.get(_positive_int(item.get("target_record_index")), ""),
            payload={"index": index, "kind": kind, **(item.get("payload") if isinstance(item.get("payload"), dict) else {})},
        ))
    return refs


def _record_id_from_index(value: object, records: list[MemoryRecord], index: dict[int, str]) -> str:
    position = _positive_int(value)
    if position in index:
        return index[position]
    if len(records) == 1:
        return records[0].id
    return ""


def _text(value: object) -> str:
    return str(value or "").strip()


def _enum(value: object, allowed: set[str]) -> str:
    text = _text(value).casefold()
    return text if text in allowed else ""


def _positive_int(value: object) -> int:
    if isinstance(value, int):
        return value if value > 0 else 0
    text = _text(value)
    return int(text) if text.isdigit() and int(text) > 0 else 0


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]
