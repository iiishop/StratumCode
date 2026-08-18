from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .models import MemoryEvidence, MemoryPayload, MemoryRecord


VERIFIED_FACT_KINDS = {"fact", "change", "validation"}
DERIVED_RELATIONS = {"derived_from", "summarizes", "supersedes", "resolved_by"}


def normalize_records(
    workspace_dir: str,
    records: list[MemoryRecord],
    evidence: list[MemoryEvidence],
) -> list[MemoryRecord]:
    supported_ids = {item.record_id for item in evidence if item.record_id}
    return [_normalize_record(workspace_dir, record, record.id in supported_ids) for record in records]


def normalize_evidence(workspace_dir: str, evidence: list[MemoryEvidence]) -> list[MemoryEvidence]:
    return [
        MemoryEvidence(
            **{
                **asdict(item),
                "path": canonical_file_key(workspace_dir, item.path) if item.path else "",
            }
        )
        for item in evidence
    ]


def canonical_subject_key(workspace_dir: str, subject_kind: str, subject_key: str) -> str:
    kind = str(subject_kind or "").casefold()
    key = str(subject_key or "").strip()
    if kind == "project":
        return "project"
    if kind == "file":
        return canonical_file_key(workspace_dir, key)
    return key


def canonical_file_key(workspace_dir: str, path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    root = Path(workspace_dir or ".").expanduser().resolve()
    candidate = Path(text)
    absolute = candidate if candidate.is_absolute() else root / candidate
    try:
        relative = absolute.resolve().relative_to(root)
    except ValueError:
        return text.replace("\\", "/")
    return relative.as_posix()


def graph_relation(relation: str) -> str:
    text = str(relation or "").strip().casefold()
    if text in {"supports", "depends_on", "supersedes", "conflicts", "mentions", "derived_from", "summarizes", "resolved_by"}:
        return text
    return "mentions"


def normalize_payload(workspace_dir: str, subject_kind: str, subject_key: str, value: object) -> dict:
    payload = MemoryPayload.from_mapping(value)
    payload.canonical_subject_key = payload.canonical_subject_key or canonical_subject_key(workspace_dir, subject_kind, subject_key)
    payload.affected_paths = [canonical_file_key(workspace_dir, item) for item in payload.affected_paths]
    if payload.path:
        payload.path = canonical_file_key(workspace_dir, payload.path)
    return payload.to_dict()


def _normalize_record(workspace_dir: str, record: MemoryRecord, has_evidence: bool) -> MemoryRecord:
    confidence = record.confidence
    status = record.status
    if confidence == "verified" and not _verified_is_supported(record, has_evidence):
        confidence = "inferred"
        if record.kind not in VERIFIED_FACT_KINDS:
            status = "pending"
    payload = normalize_payload(workspace_dir, record.subject_kind, record.subject_key, record.payload)
    if record.confidence == "verified" and confidence != "verified":
        payload["audit"] = {
            **(payload.get("audit") if isinstance(payload.get("audit"), dict) else {}),
            "downgraded_reason": "verified_without_evidence",
        }
    return MemoryRecord(
        **{
            **asdict(record),
            "subject_key": payload["canonical_subject_key"],
            "confidence": confidence,
            "status": status,
            "payload": payload,
        }
    )


def _verified_is_supported(record: MemoryRecord, has_evidence: bool) -> bool:
    if has_evidence:
        return record.kind in VERIFIED_FACT_KINDS
    return False
