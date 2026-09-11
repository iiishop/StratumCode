from __future__ import annotations

from uuid import uuid4
import json
from pathlib import Path

from .compressor import summaries_for_records
from .freshness import file_fingerprint
from .llm import call_memory_json
from .models import ConversationRef, MemoryDelta, MemoryEvidence, MemoryLink, MemoryRecord
from .conversation import conversation_delta, conversation_id

MEMORY_KINDS = {"fact", "observation", "investigation", "validation", "change", "decision", "recommendation", "risk", "task", "summary", "knowledge"}


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
    summaries = summaries_for_records(delta.records, session_id=session_id, turn_id=turn_id)
    delta.records.extend(summaries)
    delta.links.extend(MemoryLink(source_id, summary.id, "summarizes")
                       for summary in summaries for source_id in summary.source_record_ids)
    return delta


def delta_from_output(*, session_id: int | None, turn_id: str, output: str,
                      workspace_dir: str = ".", user_request: str = "") -> MemoryDelta:
    delta = _llm_delta(
        workspace_dir=workspace_dir,
        session_id=session_id,
        turn_id=turn_id,
        source="assistant_output",
        payload={"assistant_output": output, "user_request": user_request},
    )
    short = conversation_delta(session_id, turn_id, user_request, output)
    delta.records = short.records + delta.records
    delta.evidence = short.evidence + delta.evidence
    # Follow-ups are interpreted from ordered complete exchanges, not extracted references.
    delta.refs = []
    return delta


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
    evidence = _evidence_from_llm(data.get("evidence"), records, index, workspace_dir)
    sources = _observed_sources(payload.get("events", [])) if source == "events" else []
    if payload.get("user_request"):
        sources.append({"id": f"{turn_id}:user", "text": payload["user_request"], "path": "",
                        "fingerprint": {}, "kind": "user_statement"})
    evidence = _grounded_evidence(evidence, sources, workspace_dir)
    user_decisions = {e.record_id for e in evidence if e.kind == "user_statement"}
    supported = {e.record_id for e in evidence if e.kind == "tool_observation"}
    supported |= {r.id for r in records if r.id in user_decisions and r.kind == "decision"}
    anchor_id = conversation_id(session_id, turn_id) if source == "assistant_output" else f"batch-{conversation_id(session_id, turn_id)}"
    for record in records:
        record.source = source
        record.source_record_ids = [anchor_id]
        record.payload["audit"] = {"provenance_checked": True, "supported_by_observation": record.id in supported}
        if record.id not in supported:
            record.confidence = "uncertain" if record.confidence == "uncertain" else "inferred"
            record.freshness = "unknown"
            if record.kind in {"fact", "change", "validation", "decision"}:
                record.status = "pending"
            if record.scope == "project":
                record.scope = "session" if session_id is not None else "turn"
        if record.kind in {"task", "recommendation", "observation", "investigation"} and record.scope == "project":
            record.scope = "session" if session_id is not None else "turn"
        if record.scope == "project" and not record.payload.get("applies_when"):
            record.scope = "session" if session_id is not None else "turn"
    links = _links_from_llm(data.get("links"), index)
    links.extend(MemoryLink(anchor_id, record.id, "derived_from") for record in records)
    if source == "events" and records:
        records.insert(0, MemoryRecord(
            id=anchor_id, scope="turn", kind="investigation", subject_kind="turn",
            subject_key=f"session:{session_id}:{turn_id}", statement="Investigation evidence captured in this turn.",
            session_id=session_id, turn_id=turn_id, source="event_runtime",
            payload={"source_event_ids": [s["id"] for s in sources]},
        ))
        if session_id is not None:
            links.append(MemoryLink(conversation_id(session_id, turn_id), anchor_id, "mentions"))
    return MemoryDelta(
        records=records,
        evidence=evidence, links=links, refs=[],
    )


def _memory_event_payload(events: list[dict]) -> list[dict]:
    materialized = []
    by_id = {}
    for event in events:
        target = by_id.get(event.get("id"))
        if target is not None and event.get("op") == "update":
            target.setdefault("data", {}).update(event.get("patch") or {})
        elif target is not None and event.get("op") == "delta":
            field = event.get("field", "output")
            target["data"][field] = str(target["data"].get(field, "")) + str(event.get("value", ""))
        else:
            item = {**event, "data": dict(event.get("data") or {})}
            materialized.append(item)
            if event.get("id"):
                by_id[event["id"]] = item
    payload = []
    for event in materialized:
        if event.get("event") in {"thinking", "usage", "memory_status", "memory_write", "memory_reference"}:
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        item = {
            "id": event.get("id", ""),
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
    kind = _enum(item.get("kind"), MEMORY_KINDS)
    subject_kind = _enum(item.get("subject_kind"), {"project", "file", "symbol", "task", "change", "decision", "other"})
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
            fingerprint={},
            payload=item.get("payload") if isinstance(item.get("payload"), dict) else {},
        ))
    return evidence


def _observed_sources(events: list[dict]) -> list[dict]:
    sources = []
    for event in events:
        data = event.get("data") or {}
        if event.get("event") not in {"output", "thinking"} and data.get("output") and data.get("status") in {"done", "cached"}:
            raw = str(data["output"])
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                parsed = {}
            parsed = parsed if isinstance(parsed, dict) else {}
            metadata = parsed.get("metadata") or {}
            sources.append({"id": event.get("id", ""), "text": str(parsed.get("output") or raw),
                            "path": metadata.get("path", data.get("path", "")), "fingerprint": metadata})
        investigation = event.get("investigation") or {}
        for obs in investigation.get("observations", []):
            sources.append({"id": obs.get("id", ""), "text": obs.get("_grounding_evidence") or obs.get("evidence_excerpt") or obs.get("summary", ""),
                            "path": obs.get("path", ""), "fingerprint": obs})
    return sources


def _grounded_evidence(evidence: list[MemoryEvidence], sources: list[dict], workspace_dir: str) -> list[MemoryEvidence]:
    from .normalization import canonical_file_key
    result = []
    root = Path(workspace_dir).resolve()
    for item in evidence:
        excerpt = " ".join(item.excerpt.split())
        if len(excerpt) < 16:
            continue
        match = next((source for source in sources if excerpt in " ".join(str(source["text"]).split())
                      and (not item.path or canonical_file_key(workspace_dir, source["path"]) == canonical_file_key(workspace_dir, item.path))), None)
        if not match:
            continue
        item.kind = match.get("kind", "tool_observation")
        item.path = match["path"]
        if item.path:
            target = (root / item.path).resolve()
            if not target.is_relative_to(root):
                continue
            fingerprint = match["fingerprint"]
            item.fingerprint = {k: fingerprint[k] for k in ("mtime_ns", "size", "sha256") if fingerprint.get(k) is not None}
            item.fingerprint = {"path": item.path, "exists": True, **item.fingerprint} if item.fingerprint else file_fingerprint(workspace_dir, item.path, include_hash=True)
        item.payload = {"validated_source": True, "source_event_id": match["id"]}
        result.append(item)
    return result


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
