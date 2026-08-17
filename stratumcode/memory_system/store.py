from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from uuid import uuid4

from . import db
from .freshness import freshness_status
from .models import ConversationRef, MemoryDelta, MemoryEvidence, MemoryLink, MemoryRecord


def list_records(workspace_dir: str, *, include_reverted: bool = False, limit: int = 500) -> list[dict]:
    with db.db_session(workspace_dir) as conn:
        rows = conn.execute(
            """
            SELECT * FROM memory_records
            WHERE (? OR status != 'reverted')
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """,
            (1 if include_reverted else 0, int(limit)),
        ).fetchall()
    return [_row_record(workspace_dir, row) for row in rows]


def list_refs(workspace_dir: str, session_id: int | None, *, limit: int = 80) -> list[dict]:
    with db.db_session(workspace_dir) as conn:
        rows = conn.execute(
            """
            SELECT * FROM conversation_refs
            WHERE (? IS NULL OR session_id = ?)
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, session_id, int(limit)),
        ).fetchall()
    return [_row_ref(row) for row in rows]


def record_delta(workspace_dir: str, delta: MemoryDelta) -> dict:
    if not delta.records and not delta.evidence and not delta.links and not delta.refs:
        return {"records": [], "refs": [], "links": []}
    with db.db_session(workspace_dir) as conn:
        existing = _existing_statements(conn)
        conflicts = []
        for record in delta.records:
            record_id = record.id or f"mem-{uuid4().hex[:12]}"
            record = MemoryRecord(**{**asdict(record), "id": record_id})
            conflict_id = _conflicting_record(existing, record)
            _upsert_record(conn, record)
            _upsert_fts(conn, record)
            if conflict_id:
                conflicts.append({"source_id": record.id, "target_id": conflict_id, "relation": "conflicts"})
                conn.execute(
                    "INSERT OR IGNORE INTO memory_links (source_id, target_id, relation) VALUES (?, ?, ?)",
                    (record.id, conflict_id, "conflicts"),
                )
        for evidence in delta.evidence:
            _insert_evidence(conn, evidence)
        for link in delta.links:
            _insert_link(conn, link)
        for ref in delta.refs:
            _insert_ref(conn, ref)
    return {
        "records": [asdict(record) for record in delta.records],
        "refs": [asdict(ref) for ref in delta.refs],
        "links": [asdict(link) for link in delta.links] + conflicts,
    }


def update_record(workspace_dir: str, record_id: str, patch: dict) -> dict:
    allowed = {"statement", "status", "confidence", "freshness", "subject_kind", "subject_key", "kind"}
    updates = {key: value for key, value in patch.items() if key in allowed}
    if not updates:
        raise ValueError("no supported memory fields to update")
    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [record_id]
    with db.db_session(workspace_dir) as conn:
        conn.execute(
            f"UPDATE memory_records SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values,
        )
        row = conn.execute("SELECT * FROM memory_records WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise ValueError("memory record not found")
    return _row_record(workspace_dir, row)


def revert_record(workspace_dir: str, record_id: str) -> dict:
    return update_record(workspace_dir, record_id, {"status": "reverted"})


def graph(workspace_dir: str) -> dict:
    records = list_records(workspace_dir, include_reverted=True, limit=1000)
    with db.db_session(workspace_dir) as conn:
        evidence = [dict(row) for row in conn.execute("SELECT * FROM memory_evidence ORDER BY created_at DESC LIMIT 1000")]
        links = [dict(row) for row in conn.execute("SELECT source_id, target_id, relation FROM memory_links")]
    return {"records": records, "evidence": [_decode_json_fields(item) for item in evidence], "links": links}


def _upsert_record(conn, record: MemoryRecord) -> None:
    conn.execute(
        """
        INSERT INTO memory_records (
            id, scope, kind, subject_kind, subject_key, statement, confidence,
            status, freshness, session_id, turn_id, source, source_record_ids_json, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            scope=excluded.scope,
            kind=excluded.kind,
            subject_kind=excluded.subject_kind,
            subject_key=excluded.subject_key,
            statement=excluded.statement,
            confidence=excluded.confidence,
            status=excluded.status,
            freshness=excluded.freshness,
            session_id=excluded.session_id,
            turn_id=excluded.turn_id,
            source=excluded.source,
            source_record_ids_json=excluded.source_record_ids_json,
            payload_json=excluded.payload_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            record.id,
            record.scope,
            record.kind,
            record.subject_kind,
            record.subject_key,
            record.statement,
            record.confidence,
            record.status,
            record.freshness,
            record.session_id,
            record.turn_id,
            record.source,
            json.dumps(record.source_record_ids, ensure_ascii=False),
            json.dumps(record.payload, ensure_ascii=False),
        ),
    )


def _upsert_fts(conn, record: MemoryRecord) -> None:
    try:
        conn.execute("DELETE FROM memory_fts WHERE record_id = ?", (record.id,))
        conn.execute(
            "INSERT INTO memory_fts (record_id, statement, subject_key) VALUES (?, ?, ?)",
            (record.id, record.statement, record.subject_key),
        )
    except sqlite3.DatabaseError:
        return


def _insert_evidence(conn, evidence: MemoryEvidence) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO memory_evidence (
            id, record_id, kind, path, excerpt, fingerprint_json, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence.id,
            evidence.record_id,
            evidence.kind,
            evidence.path,
            evidence.excerpt,
            json.dumps(evidence.fingerprint, ensure_ascii=False),
            json.dumps(evidence.payload, ensure_ascii=False),
        ),
    )


def _insert_link(conn, link: MemoryLink) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO memory_links (source_id, target_id, relation) VALUES (?, ?, ?)",
        (link.source_id, link.target_id, link.relation),
    )


def _insert_ref(conn, ref: ConversationRef) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO conversation_refs (
            id, session_id, turn_id, ref_key, label, content, target_record_id, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ref.id,
            ref.session_id,
            ref.turn_id,
            ref.ref_key,
            ref.label,
            ref.content,
            ref.target_record_id,
            json.dumps(ref.payload, ensure_ascii=False),
        ),
    )


def _row_record(workspace_dir: str, row) -> dict:
    item = dict(row)
    item["source_record_ids"] = _loads(item.pop("source_record_ids_json", "[]"), [])
    item["payload"] = _loads(item.pop("payload_json", "{}"), {})
    item["freshness"] = _computed_freshness(workspace_dir, item)
    return item


def _row_ref(row) -> dict:
    item = dict(row)
    item["payload"] = _loads(item.pop("payload_json", "{}"), {})
    return item


def _decode_json_fields(item: dict) -> dict:
    result = dict(item)
    for key, fallback in (("fingerprint_json", {}), ("payload_json", {})):
        if key in result:
            result[key[:-5]] = _loads(result.pop(key), fallback)
    return result


def _loads(value: str, fallback):
    try:
        return json.loads(value or "")
    except json.JSONDecodeError:
        return fallback


def _computed_freshness(workspace_dir: str, item: dict) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    fingerprint = payload.get("fingerprint") if isinstance(payload.get("fingerprint"), dict) else {}
    if fingerprint:
        return freshness_status(workspace_dir, fingerprint)
    return str(item.get("freshness") or "unknown")


def _existing_statements(conn) -> dict[tuple[str, str], tuple[str, str]]:
    rows = conn.execute(
        "SELECT id, subject_key, statement FROM memory_records WHERE status != 'reverted'"
    ).fetchall()
    return {
        (str(row["subject_key"]).casefold(), _statement_key(str(row["statement"]))): (
            str(row["id"]),
            str(row["statement"]),
        )
        for row in rows
    }


def _conflicting_record(existing: dict, record: MemoryRecord) -> str:
    key_prefix = str(record.subject_key).casefold()
    statement_key = _statement_key(record.statement)
    for (subject_key, known_statement_key), (known_id, known_statement) in existing.items():
        if subject_key != key_prefix or known_statement_key == statement_key:
            continue
        if _looks_conflicting(known_statement, record.statement):
            return known_id
    return ""


def _statement_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _looks_conflicting(left: str, right: str) -> bool:
    left_text = left.casefold()
    right_text = right.casefold()
    pairs = ((" is ", " is not "), (" enabled", " disabled"), (" true", " false"), (" yes", " no"))
    return any(a in left_text and b in right_text or b in left_text and a in right_text for a, b in pairs)
