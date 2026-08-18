from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, cast

MemoryScope = Literal["turn", "session", "project"]
MemoryStatus = Literal["accepted", "reverted", "edited", "pending"]
FreshnessStatus = Literal["fresh", "stale", "unknown"]
MemoryImportance = Literal["low", "medium", "high", "critical", "unknown"]


@dataclass(slots=True)
class MemoryPayload:
    predicate: str = ""
    objects: list[str] = field(default_factory=list)
    affected_paths: list[str] = field(default_factory=list)
    applies_when: str = ""
    invalidated_by: list[str] = field(default_factory=list)
    importance: MemoryImportance = "unknown"
    canonical_subject_key: str = ""
    fingerprint: dict = field(default_factory=dict)
    audit: dict = field(default_factory=dict)
    task_kind: str = ""
    task_status: str = ""
    request: str = ""
    path: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: object) -> MemoryPayload:
        data = value if isinstance(value, dict) else {}
        known = {
            "predicate",
            "objects",
            "affected_paths",
            "applies_when",
            "invalidated_by",
            "importance",
            "canonical_subject_key",
            "fingerprint",
            "audit",
            "task_kind",
            "task_status",
            "request",
            "path",
            "extra",
        }
        extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
        extra = {**{key: item for key, item in data.items() if key not in known}, **extra}
        return cls(
            predicate=_text(data.get("predicate")),
            objects=_string_list(data.get("objects")),
            affected_paths=_string_list(data.get("affected_paths")),
            applies_when=_text(data.get("applies_when")),
            invalidated_by=_string_list(data.get("invalidated_by")),
            importance=_importance(data.get("importance")),
            canonical_subject_key=_text(data.get("canonical_subject_key")),
            fingerprint=data.get("fingerprint") if isinstance(data.get("fingerprint"), dict) else {},
            audit=data.get("audit") if isinstance(data.get("audit"), dict) else {},
            task_kind=_text(data.get("task_kind")),
            task_status=_text(data.get("task_status")),
            request=_text(data.get("request")),
            path=_text(data.get("path")),
            extra=extra,
        )

    def to_dict(self) -> dict:
        return {
            "predicate": self.predicate,
            "objects": self.objects,
            "affected_paths": self.affected_paths,
            "applies_when": self.applies_when,
            "invalidated_by": self.invalidated_by,
            "importance": self.importance,
            "canonical_subject_key": self.canonical_subject_key,
            "fingerprint": self.fingerprint,
            "audit": self.audit,
            "task_kind": self.task_kind,
            "task_status": self.task_status,
            "request": self.request,
            "path": self.path,
            "extra": self.extra,
        }


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


def _text(value: object) -> str:
    return str(value or "").strip()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]


def _importance(value: object) -> MemoryImportance:
    text = _text(value).casefold()
    if text in {"low", "medium", "high", "critical"}:
        return cast(MemoryImportance, text)
    return "unknown"
