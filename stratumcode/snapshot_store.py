from __future__ import annotations

import os
import time
from dataclasses import asdict
from pathlib import Path

from . import db


SCHEMA = """
CREATE TABLE IF NOT EXISTS file_snapshots (
    id TEXT PRIMARY KEY,
    workspace_root TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    encoding TEXT NOT NULL,
    newline TEXT NOT NULL,
    bom INTEGER NOT NULL,
    created_at REAL NOT NULL,
    last_used_at REAL NOT NULL
)
"""


def save(snapshot, root: Path) -> None:
    _init()
    data = asdict(snapshot)
    now = time.time()
    with db.db_session() as conn:
        conn.execute(
            """
            INSERT INTO file_snapshots (
                id, workspace_root, path, content_hash, size, mtime_ns,
                encoding, newline, bom, created_at, last_used_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                workspace_root = excluded.workspace_root,
                path = excluded.path,
                content_hash = excluded.content_hash,
                size = excluded.size,
                mtime_ns = excluded.mtime_ns,
                encoding = excluded.encoding,
                newline = excluded.newline,
                bom = excluded.bom,
                last_used_at = excluded.last_used_at
            """,
            (
                snapshot.id,
                _root_key(root),
                _path_key(snapshot.path),
                snapshot.content_hash,
                int(snapshot.size),
                int(snapshot.mtime_ns),
                snapshot.encoding,
                snapshot.newline,
                int(bool(snapshot.bom)),
                now,
                now,
            ),
        )


def get(snapshot_id: str) -> dict | None:
    _init()
    with db.db_session() as conn:
        row = conn.execute(
            "SELECT * FROM file_snapshots WHERE id = ?",
            (str(snapshot_id or "").strip(),),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE file_snapshots SET last_used_at = ? WHERE id = ?",
                (time.time(), snapshot_id),
            )
    return dict(row) if row else None


def root_matches(row: dict, root: Path) -> bool:
    return row.get("workspace_root") == _root_key(root)


def path_matches(row: dict, rel_path: str) -> bool:
    return row.get("path") == _path_key(rel_path)


def _init() -> None:
    with db.db_session() as conn:
        conn.execute(SCHEMA)


def _root_key(root: Path) -> str:
    return os.path.normcase(str(root.resolve()))


def _path_key(path: str) -> str:
    return os.path.normcase(str(path).replace("\\", "/"))
