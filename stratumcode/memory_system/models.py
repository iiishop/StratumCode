from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MemoryScope = Literal["turn", "session", "project"]
MemoryStatus = Literal["accepted", "reverted", "edited", "pending"]
FreshnessStatus = Literal["fresh", "stale", "unknown"]


@dataclass(slots=True)
class MemoryRecord:
    id: str
    scope: MemoryScope
    kind: str
    subject_kind: str
    subject_key: str
    statement: str
    confidence: str = "inferred"
    status: MemoryStatus = "accepted"
    freshness: FreshnessStatus = "unknown"
    session_id: int | None = None
    turn_id: str = ""
    source: str = ""
    source_record_ids: list[str] = field(default_factory=list)
    payload: dict = field(default_factory=dict)


@dataclass(slots=True)
class MemoryEvidence:
    id: str
    record_id: str
    kind: str
    path: str = ""
    excerpt: str = ""
    fingerprint: dict = field(default_factory=dict)
    payload: dict = field(default_factory=dict)


@dataclass(slots=True)
class MemoryLink:
    source_id: str
    target_id: str
    relation: str


@dataclass(slots=True)
class ConversationRef:
    id: str
    session_id: int | None
    turn_id: str
    ref_key: str
    label: str
    content: str
    target_record_id: str = ""
    payload: dict = field(default_factory=dict)


@dataclass(slots=True)
class MemoryDelta:
    records: list[MemoryRecord] = field(default_factory=list)
    evidence: list[MemoryEvidence] = field(default_factory=list)
    links: list[MemoryLink] = field(default_factory=list)
    refs: list[ConversationRef] = field(default_factory=list)


@dataclass(slots=True)
class MemorySnapshot:
    records: list[dict] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)
    stale: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    summaries: list[dict] = field(default_factory=list)
    omitted: dict = field(default_factory=dict)

    def to_legacy_context(self) -> dict:
        tasks = [
            _task_item(item)
            for item in self.records
            if item.get("kind") in {"task", "recommendation"}
        ]
        knowledge = [
            {
                "id": item.get("id", ""),
                "statement": item.get("statement", ""),
                "fresh": item.get("freshness") == "fresh",
                "observation_ids": item.get("source_record_ids", []),
            }
            for item in self.records
            if item.get("kind") in {"knowledge", "fact", "change", "decision", "summary"}
        ]
        observations = [
            {
                "id": item.get("id", ""),
                "summary": item.get("statement", ""),
                "path": item.get("payload", {}).get("path", ""),
                "fresh": item.get("freshness") == "fresh",
            }
            for item in self.records
            if item.get("kind") in {"observation", "fact", "change"}
        ]
        investigations = [
            {
                "id": item.get("id", ""),
                "request": item.get("payload", {}).get("request", ""),
                "summary": item.get("statement", ""),
            }
            for item in self.records
            if item.get("kind") == "investigation"
        ]
        return {
            "tasks": tasks,
            "goals": [item for item in tasks if item.get("kind") == "goal"],
            "recent_user_messages": [],
            "recent_turns": [],
            "observations": observations,
            "knowledge": knowledge,
            "investigations": investigations,
        }


def _task_item(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "kind": item.get("payload", {}).get("task_kind", "task"),
        "text": item.get("statement", ""),
        "status": item.get("payload", {}).get("task_status", "open"),
        "reason": item.get("source", ""),
    }
