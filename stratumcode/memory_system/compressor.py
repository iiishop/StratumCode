from __future__ import annotations

from uuid import uuid4

from .llm import call_memory_json
from .models import MemoryRecord


def summaries_for_records(records: list[MemoryRecord], *, session_id: int | None, turn_id: str) -> list[MemoryRecord]:
    project_records = [record for record in records if record.scope == "project"]
    if len(project_records) < 2:
        return []
    data = call_memory_json("compress_records", {
        "records": [_record_payload(record) for record in project_records],
    })
    summaries = data.get("summaries") if isinstance(data, dict) else []
    result = []
    for item in summaries if isinstance(summaries, list) else []:
        if not isinstance(item, dict):
            continue
        subject_kind = _text(item.get("subject_kind"))
        subject_key = _text(item.get("subject_key"))
        statement = _text(item.get("statement"))
        confidence = _enum(item.get("confidence"), {"verified", "inferred", "uncertain"})
        source_ids = _valid_source_ids(item.get("source_record_ids"), project_records)
        if not subject_kind or not subject_key or not statement or not confidence or not source_ids:
            continue
        result.append(MemoryRecord(
            id=f"summary-{uuid4().hex[:12]}",
            scope="project",
            kind="summary",
            subject_kind=subject_kind,
            subject_key=subject_key,
            statement=statement,
            confidence="inferred" if confidence == "verified" else confidence,
            freshness="fresh",
            session_id=session_id,
            turn_id=turn_id,
            source="memory_compressor",
            source_record_ids=source_ids,
            payload={"derived": True, **(item.get("payload") if isinstance(item.get("payload"), dict) else {})},
        ))
    return result


def _record_payload(record: MemoryRecord) -> dict:
    return {
        "id": record.id,
        "scope": record.scope,
        "kind": record.kind,
        "subject_kind": record.subject_kind,
        "subject_key": record.subject_key,
        "statement": record.statement,
        "confidence": record.confidence,
        "source_record_ids": record.source_record_ids,
    }


def _valid_source_ids(value: object, records: list[MemoryRecord]) -> list[str]:
    allowed = {record.id for record in records}
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item) in allowed]


def _text(value: object) -> str:
    return str(value or "").strip()


def _enum(value: object, allowed: set[str]) -> str:
    text = _text(value).casefold()
    return text if text in allowed else ""
