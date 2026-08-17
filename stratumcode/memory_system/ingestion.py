from __future__ import annotations

import re
from uuid import uuid4

from .compressor import summaries_for_records
from .freshness import file_fingerprint
from .models import ConversationRef, MemoryDelta, MemoryEvidence, MemoryRecord


def delta_from_events(
    *,
    workspace_dir: str,
    session_id: int | None,
    turn_id: str,
    events: list[dict],
    assistant_output: str = "",
) -> MemoryDelta:
    delta = MemoryDelta()
    for event in events:
        if event.get("op") != "done":
            continue
        if isinstance(event.get("investigation"), dict):
            _add_investigation(delta, workspace_dir, session_id, turn_id, event["investigation"])
        if isinstance(event.get("validation_result"), dict):
            _add_validation(delta, workspace_dir, session_id, turn_id, event["validation_result"])
        if isinstance(event.get("implementation"), dict):
            _add_implementation(delta, workspace_dir, session_id, turn_id, event["implementation"])
    _add_refs(delta, session_id, turn_id, assistant_output)
    delta.records.extend(summaries_for_records(delta.records, session_id=session_id, turn_id=turn_id))
    return delta


def delta_from_output(*, session_id: int | None, turn_id: str, output: str) -> MemoryDelta:
    delta = MemoryDelta()
    _add_refs(delta, session_id, turn_id, output)
    return delta


def _add_investigation(delta: MemoryDelta, workspace_dir: str, session_id: int | None, turn_id: str, investigation: dict) -> None:
    investigation_id = str(investigation.get("id") or turn_id or uuid4().hex[:8])
    summary = str(investigation.get("summary") or "").strip()
    if summary:
        record_id = f"investigation-{uuid4().hex[:12]}"
        delta.records.append(MemoryRecord(
            id=record_id,
            scope="session",
            kind="investigation",
            subject_kind="task",
            subject_key=investigation_id,
            statement=summary,
            confidence="verified",
            freshness="fresh",
            session_id=session_id,
            turn_id=turn_id,
            source="investigation",
            payload={"request": investigation.get("request", "")},
        ))
    for index, belief in enumerate(investigation.get("beliefs", []) if isinstance(investigation.get("beliefs"), list) else [], start=1):
        statement = str((belief or {}).get("statement") or "").strip()
        if not statement:
            continue
        record_id = f"fact-{uuid4().hex[:12]}"
        delta.records.append(MemoryRecord(
            id=record_id,
            scope="project",
            kind="fact",
            subject_kind="project",
            subject_key=_subject_from_text(statement),
            statement=statement,
            confidence="verified" if str(belief.get("status") or "supported") == "supported" else "inferred",
            freshness="fresh",
            session_id=session_id,
            turn_id=turn_id,
            source="investigation_belief",
            source_record_ids=[str(item) for item in belief.get("evidence", []) if str(item).strip()] if isinstance(belief.get("evidence"), list) else [],
            payload={"belief_index": index},
        ))
    for obs in investigation.get("observations", []) if isinstance(investigation.get("observations"), list) else []:
        if not isinstance(obs, dict):
            continue
        path = str(obs.get("path") or "").strip()
        statement = str(obs.get("summary") or obs.get("title") or obs.get("tool") or "").strip()
        if not statement:
            continue
        record_id = f"observation-{uuid4().hex[:12]}"
        fingerprint = file_fingerprint(workspace_dir, path) if path else {}
        delta.records.append(MemoryRecord(
            id=record_id,
            scope="session",
            kind="observation",
            subject_kind="file" if path else "project",
            subject_key=path or _subject_from_text(statement),
            statement=statement,
            confidence="verified",
            freshness="fresh" if fingerprint.get("exists", True) else "stale",
            session_id=session_id,
            turn_id=turn_id,
            source="investigation_observation",
            payload={"path": path, "fingerprint": fingerprint},
        ))
        delta.evidence.append(MemoryEvidence(
            id=f"evidence-{uuid4().hex[:12]}",
            record_id=record_id,
            kind="tool_observation",
            path=path,
            excerpt=statement,
            fingerprint=fingerprint,
            payload={"source_observation_id": obs.get("id", "")},
        ))


def _add_validation(delta: MemoryDelta, workspace_dir: str, session_id: int | None, turn_id: str, result: dict) -> None:
    summary = str(result.get("summary") or "").strip()
    verdict = str(result.get("verdict") or "").strip()
    if not summary and not verdict:
        return
    changed_files = [str(item) for item in result.get("changed_files", []) if str(item).strip()] if isinstance(result.get("changed_files"), list) else []
    record_id = f"validation-{uuid4().hex[:12]}"
    delta.records.append(MemoryRecord(
        id=record_id,
        scope="project",
        kind="validation",
        subject_kind="change",
        subject_key=", ".join(changed_files[:3]) or "validation",
        statement=summary or f"Validation verdict: {verdict}",
        confidence="verified",
        freshness="fresh",
        session_id=session_id,
        turn_id=turn_id,
        source="validation",
        payload={"verdict": verdict, "changed_files": changed_files},
    ))
    for path in changed_files:
        delta.evidence.append(MemoryEvidence(
            id=f"evidence-{uuid4().hex[:12]}",
            record_id=record_id,
            kind="validation_file",
            path=path,
            excerpt=summary,
            fingerprint=file_fingerprint(workspace_dir, path),
        ))


def _add_implementation(delta: MemoryDelta, workspace_dir: str, session_id: int | None, turn_id: str, implementation: dict) -> None:
    changed_files = [str(item) for item in implementation.get("changed_files", []) if str(item).strip()] if isinstance(implementation.get("changed_files"), list) else []
    if not changed_files:
        return
    record_id = f"change-{uuid4().hex[:12]}"
    delta.records.append(MemoryRecord(
        id=record_id,
        scope="project",
        kind="change",
        subject_kind="file",
        subject_key=", ".join(changed_files[:3]),
        statement="Changed files: " + ", ".join(changed_files),
        confidence="verified",
        freshness="fresh",
        session_id=session_id,
        turn_id=turn_id,
        source="implementation",
        payload={"changed_files": changed_files},
    ))
    for path in changed_files:
        delta.evidence.append(MemoryEvidence(
            id=f"evidence-{uuid4().hex[:12]}",
            record_id=record_id,
            kind="file_snapshot",
            path=path,
            excerpt=f"Implementation changed {path}",
            fingerprint=file_fingerprint(workspace_dir, path),
        ))


def _add_refs(delta: MemoryDelta, session_id: int | None, turn_id: str, output: str) -> None:
    for index, content in _numbered_items(output)[:20]:
        delta.refs.append(ConversationRef(
            id=f"ref-{uuid4().hex[:12]}",
            session_id=session_id,
            turn_id=turn_id,
            ref_key=f"assistant:{turn_id}:{index}",
            label=f"Item {index}",
            content=content,
            payload={"index": index},
        ))


def _numbered_items(text: str) -> list[tuple[int, str]]:
    items = []
    for line in (text or "").splitlines():
        match = re.match(r"\s*(?:[-*]\s*)?([0-9]+)[.)、]\s+(.+)", line)
        if match:
            items.append((int(match.group(1)), match.group(2).strip()))
    return items


def _subject_from_text(value: str) -> str:
    words = re.findall(r"[\w./\\-]+", value)
    return " ".join(words[:6]) or "project"
