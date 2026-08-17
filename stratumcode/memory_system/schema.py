from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_records (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject_kind TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    statement TEXT NOT NULL,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL,
    freshness TEXT NOT NULL,
    session_id INTEGER,
    turn_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_record_ids_json TEXT NOT NULL DEFAULT '[]',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memory_evidence (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    fingerprint_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(record_id) REFERENCES memory_records(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_links (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_id, target_id, relation)
);

CREATE TABLE IF NOT EXISTS conversation_refs (
    id TEXT PRIMARY KEY,
    session_id INTEGER,
    turn_id TEXT NOT NULL,
    ref_key TEXT NOT NULL,
    label TEXT NOT NULL,
    content TEXT NOT NULL,
    target_record_id TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memory_records_scope_status
ON memory_records(scope, status, freshness);

CREATE INDEX IF NOT EXISTS idx_memory_records_subject
ON memory_records(subject_kind, subject_key);

CREATE INDEX IF NOT EXISTS idx_memory_records_session
ON memory_records(session_id, turn_id);

CREATE INDEX IF NOT EXISTS idx_conversation_refs_session
ON conversation_refs(session_id, created_at);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
USING fts5(record_id UNINDEXED, statement, subject_key);
"""
