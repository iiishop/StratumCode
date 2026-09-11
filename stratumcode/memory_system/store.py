from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import asdict
from uuid import uuid4

from . import db
from .freshness import freshness_status
from .llm import call_memory_json
from .models import ConversationRef, MemoryDelta, MemoryEvidence, MemoryLink, MemoryRecord
from .normalization import graph_relation, normalize_evidence, normalize_payload, normalize_records


CONFLICT_CANDIDATE_KINDS = {"fact", "change", "validation", "decision"}


def list_records(workspace_dir: str, *, include_reverted: bool = False, limit: int = 500,
                 scoped: bool = False, session_id: int | None = None) -> list[dict]:
    with db.db_session(workspace_dir) as conn:
        rows = conn.execute(
            """
            SELECT * FROM memory_records
            WHERE (? OR status != 'reverted')
              AND (? = 0 OR scope = 'project' OR (? IS NOT NULL AND session_id = ?))
            ORDER BY updated_at DESC, created_at DESC, rowid DESC
            LIMIT ?
            """,
            (1 if include_reverted else 0, int(scoped), session_id, session_id, int(limit)),
        ).fetchall()
        supported_ids = _supported_record_ids(conn)
        freshness, relations, superseded = _dependency_state(workspace_dir, conn)
    result = [_row_record(workspace_dir, row, supported_ids) for row in rows]
    for item in result:
        item["freshness"] = freshness.get(item["id"], item["freshness"])
        item["relations"] = relations.get(item["id"], [])
        item["superseded_by"] = superseded.get(item["id"], [])
    return result


def list_refs(workspace_dir: str, session_id: int | None, *, limit: int = 80) -> list[dict]:
    if session_id is None:
        return []
    with db.db_session(workspace_dir) as conn:
        rows = conn.execute(
            """
            SELECT * FROM conversation_refs
            WHERE (? IS NULL OR session_id = ?)
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (session_id, session_id, int(limit)),
        ).fetchall()
    return [_row_ref(row) for row in rows]


def list_conversations(workspace_dir: str, session_id: int, *, limit: int = 4) -> list[dict]:
    with db.db_session(workspace_dir) as conn:
        rows = conn.execute(
            "SELECT * FROM memory_records WHERE session_id = ? AND kind = 'conversation' "
            "AND status != 'reverted' ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [{**dict(row), "payload": _loads(row["payload_json"], {})} for row in rows]


def record_delta(workspace_dir: str, delta: MemoryDelta) -> dict:
    if not delta.records and not delta.evidence and not delta.links and not delta.refs:
        return {"records": [], "refs": [], "links": []}
    records = _records_with_ids(delta.records)
    evidence = normalize_evidence(workspace_dir, delta.evidence)
    records = normalize_records(workspace_dir, records, evidence)
    with db.db_session(workspace_dir) as conn:
        existing = _existing_statements(conn)
    aliases = {}
    for record in records:
        key = _record_key(record)
        known = existing.get(key)
        if record.source not in {"conversation_runtime", "event_runtime"}:
            canonical_id = known[0] if known else "mem-" + hashlib.sha256(repr(key).encode()).hexdigest()[:24]
            aliases[record.id] = canonical_id
            record.id = canonical_id
        existing[key] = (record.id, record.statement)
    for record in records:
        record.source_record_ids = list(dict.fromkeys(aliases.get(i, i) for i in record.source_record_ids if aliases.get(i, i) != record.id))
    for item in evidence:
        item.record_id = aliases.get(item.record_id, item.record_id)
    links = [MemoryLink(aliases.get(l.source_id, l.source_id), aliases.get(l.target_id, l.target_id), graph_relation(l.relation)) for l in delta.links]
    for ref in delta.refs:
        ref.target_record_id = aliases.get(ref.target_record_id, ref.target_record_id)
    # LLM work must not hold a SQLite writer transaction and block the next turn's exact history.
    semantic_links = []
    for record in records:
        relation, target_id = _semantic_relation(existing, record)
        if target_id and relation != "none" and target_id != record.id:
            semantic_links.append({"source_id": record.id, "target_id": target_id, "relation": relation})
    with db.db_session(workspace_dir) as conn:
        for record in records:
            old = conn.execute("SELECT source_record_ids_json, confidence, status FROM memory_records WHERE id = ?", (record.id,)).fetchone()
            if old:
                record.source_record_ids = list(dict.fromkeys([*_loads(old["source_record_ids_json"], []), *record.source_record_ids]))
                if old["status"] in {"reverted", "edited"}:
                    continue
            _upsert_record(conn, record)
            _upsert_fts(conn, record)
        for link in semantic_links:
            _insert_link(conn, MemoryLink(**link))
        for item in evidence:
            _insert_evidence(conn, item)
        for link in links:
            if link.source_id != link.target_id:
                _insert_link(conn, link)
        for ref in delta.refs:
            _insert_ref(conn, ref)
    return {
        "records": [asdict(record) for record in records],
        "refs": [asdict(ref) for ref in delta.refs],
        "links": [asdict(link) for link in links] + semantic_links,
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
    with db.db_session(workspace_dir) as conn:
        supported_ids = _supported_record_ids(conn)
    return _row_record(workspace_dir, row, supported_ids)


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


def _row_record(workspace_dir: str, row, supported_ids: set[str]) -> dict:
    item = dict(row)
    item["source_record_ids"] = _loads(item.pop("source_record_ids_json", "[]"), [])
    item["payload"] = normalize_payload(
        workspace_dir,
        str(item.get("subject_kind") or ""),
        str(item.get("subject_key") or ""),
        _loads(item.pop("payload_json", "{}"), {}),
    )
    item["freshness"] = _computed_freshness(workspace_dir, item)
    if item.get("source") == "assistant_output" and not item["payload"].get("audit", {}).get("supported_by_observation"):
        # Old prose-derived records cannot remain verified merely because the LLM invented an excerpt.
        item["confidence"] = "inferred"
        item["freshness"] = "unknown"
        if item.get("kind") in CONFLICT_CANDIDATE_KINDS:
            item["status"] = "pending"
        if item.get("scope") == "project":
            item["scope"] = "session"
    if item.get("confidence") == "verified" and item.get("id") not in supported_ids:
        item["confidence"] = "inferred"
        item.setdefault("payload", {})["audit"] = {
            **(item.get("payload", {}).get("audit") if isinstance(item.get("payload", {}).get("audit"), dict) else {}),
            "downgraded_reason": "verified_without_evidence",
        }
    return item


def _row_ref(row) -> dict:
    item = dict(row)
    item["payload"] = _loads(item.pop("payload_json", "{}"), {})
    return item


def _decode_json_fields(item: dict) -> dict:
    result = dict(item)
    for key, default_value in (("fingerprint_json", {}), ("payload_json", {})):
        if key in result:
            result[key[:-5]] = _loads(result.pop(key), default_value)
    return result


def _supported_record_ids(conn) -> set[str]:
    rows = conn.execute("SELECT record_id, payload_json FROM memory_evidence").fetchall()
    return {str(row["record_id"]) for row in rows if _loads(row["payload_json"], {}).get("validated_source")}


def _dependency_state(workspace_dir: str, conn) -> tuple[dict, dict, dict]:
    records = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM memory_records")}
    evidence = {}
    fingerprint_cache = {}
    latest_evidence = {}
    for row in conn.execute("SELECT record_id, fingerprint_json, payload_json FROM memory_evidence ORDER BY created_at, rowid"):
        if not _loads(row["payload_json"], {}).get("validated_source"):
            continue
        fp = _loads(row["fingerprint_json"], {})
        if fp:
            key = row["fingerprint_json"]
            if key not in fingerprint_cache:
                try:
                    fingerprint_cache[key] = freshness_status(workspace_dir, fp)
                except OSError:
                    fingerprint_cache[key] = "unknown"
            latest_evidence[(row["record_id"], fp.get("path", ""))] = fingerprint_cache[key]
        else:
            latest_evidence[(row["record_id"], "")] = "fresh"
    for (record_id, _), state in latest_evidence.items():
        evidence.setdefault(record_id, []).append(state)
    relations = {}
    superseded = {}
    for link in conn.execute("SELECT source_id, target_id, relation FROM memory_links"):
        data = dict(link)
        relations.setdefault(link["source_id"], []).append(data)
        relations.setdefault(link["target_id"], []).append(data)
        source = records.get(link["source_id"], {})
        if link["relation"] == "supersedes" and source.get("status") in {"accepted", "edited"}:
            superseded.setdefault(link["target_id"], []).append(link["source_id"])
    result = {}
    visiting = set()
    def resolve(record_id: str) -> str:
        if record_id in result:
            return result[record_id]
        record = records.get(record_id)
        if not record or record_id in visiting or record["status"] == "reverted":
            return "stale"
        visiting.add(record_id)
        states = evidence.get(record_id, [])
        payload = _loads(record["payload_json"], {})
        if not states and payload.get("fingerprint"):
            try:
                states = [freshness_status(workspace_dir, payload["fingerprint"])]
            except OSError:
                states = ["unknown"]
        base = "stale" if "stale" in states else "unknown" if "unknown" in states else "fresh" if states else "unknown"
        source_ids = _loads(record["source_record_ids_json"], [])
        source_states = [resolve(i) for i in source_ids]
        if "stale" in source_states:
            base = "stale"
        elif record["kind"] == "summary":
            base = "fresh" if source_states and all(s == "fresh" for s in source_states) else "unknown"
        visiting.remove(record_id)
        result[record_id] = base
        return base
    for record_id in records:
        resolve(record_id)
    superseded = {target: [source for source in sources if result.get(source) == "fresh"]
                  for target, sources in superseded.items()}
    return result, relations, superseded


def _loads(value: str, default_value):
    try:
        return json.loads(value or "")
    except json.JSONDecodeError:
        return default_value


def _computed_freshness(workspace_dir: str, item: dict) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    fingerprint = payload.get("fingerprint") if isinstance(payload.get("fingerprint"), dict) else {}
    if fingerprint:
        return freshness_status(workspace_dir, fingerprint)
    return str(item.get("freshness") or "unknown")


def _records_with_ids(records: list[MemoryRecord]) -> list[MemoryRecord]:
    return [
        MemoryRecord(**{**asdict(record), "id": record.id or f"mem-{uuid4().hex[:12]}"})
        for record in records
    ]


def _record_key(record: MemoryRecord) -> tuple:
    owner = "" if record.scope == "project" else str(record.session_id) + (":" + record.turn_id if record.scope == "turn" else "")
    return (record.scope, owner, record.subject_kind.casefold(), record.subject_key.casefold(),
            record.kind.casefold(), _statement_key(record.statement),
            str(record.payload.get("predicate", "")), str(record.payload.get("applies_when", "")))


def _existing_statements(conn) -> dict[tuple, tuple[str, str]]:
    rows = conn.execute(
        "SELECT * FROM memory_records ORDER BY created_at, rowid"
    ).fetchall()
    return {
        _record_key(MemoryRecord(id=row["id"], scope=row["scope"], kind=row["kind"],
                                subject_kind=row["subject_kind"], subject_key=row["subject_key"],
                                statement=row["statement"], session_id=row["session_id"], turn_id=row["turn_id"],
                                payload=_loads(row["payload_json"], {}))): (
            str(row["id"]),
            str(row["statement"]),
        )
        for row in rows
    }


def _semantic_relation(existing: dict, record: MemoryRecord) -> tuple[str, str]:
    if record.kind not in CONFLICT_CANDIDATE_KINDS:
        return "none", ""
    subject_kind = str(record.subject_kind).casefold()
    subject_key = str(record.subject_key).casefold()
    kind = str(record.kind).casefold()
    statement_key = _statement_key(record.statement)
    candidates = []
    own_key = _record_key(record)
    for key, (known_id, known_statement) in existing.items():
        scope, owner, known_subject_kind, known_subject_key, known_kind, known_statement_key, _, _ = key
        if (
            scope != own_key[0] or owner != own_key[1] or known_id == record.id
            or known_subject_kind != subject_kind
            or known_subject_key != subject_key
            or known_kind != kind
            or known_statement_key == statement_key
        ):
            continue
        candidates.append({"id": known_id, "statement": known_statement})
    if not candidates:
        return "none", ""
    data = call_memory_json("detect_conflict", {
        "new_record": {
            "id": record.id,
            "subject_kind": record.subject_kind,
            "subject_key": record.subject_key,
            "kind": record.kind,
            "statement": record.statement,
            "confidence": record.confidence,
        },
        "candidates": candidates[:20],
    })
    conflict_id = str(data.get("conflict_id") or "").strip() if isinstance(data, dict) else ""
    relation = graph_relation(data.get("relation") if isinstance(data, dict) else "")
    if conflict_id in {item["id"] for item in candidates} and relation in {"conflicts", "supersedes", "resolved_by"}:
        return relation, conflict_id
    return "none", ""


def _statement_key(value: str) -> str:
    return " ".join(value.casefold().split())
