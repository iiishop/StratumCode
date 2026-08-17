from __future__ import annotations

from uuid import uuid4

from .models import MemoryRecord


def summaries_for_records(records: list[MemoryRecord], *, session_id: int | None, turn_id: str) -> list[MemoryRecord]:
    groups: dict[str, list[MemoryRecord]] = {}
    for record in records:
        if record.scope != "project":
            continue
        key = record.subject_key or record.subject_kind or "project"
        groups.setdefault(key, []).append(record)
    summaries = []
    for subject_key, items in groups.items():
        if len(items) < 2:
            continue
        source_ids = [item.id for item in items if item.id]
        statement = "; ".join(item.statement for item in items[:4] if item.statement)
        summaries.append(MemoryRecord(
            id=f"summary-{uuid4().hex[:12]}",
            scope="project",
            kind="summary",
            subject_kind=items[0].subject_kind or "subject",
            subject_key=subject_key,
            statement=statement,
            confidence="verified" if all(item.confidence == "verified" for item in items) else "inferred",
            freshness="fresh",
            session_id=session_id,
            turn_id=turn_id,
            source="memory_compressor",
            source_record_ids=source_ids,
            payload={"derived": True},
        ))
    return summaries
