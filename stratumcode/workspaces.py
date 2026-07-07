from pathlib import Path

from .db import db_session


def _ensure_table() -> None:
    with db_session() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def _normalize_path(path: str) -> str:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"workspace directory does not exist: {root}")
    return str(root)


def ensure_default(fallback_path: str) -> None:
    _ensure_table()
    path = _normalize_path(fallback_path)
    with db_session() as db:
        rows = db.execute("SELECT id, path, is_active FROM workspaces").fetchall()
        for row in rows:
            if not Path(row["path"]).is_dir():
                db.execute("DELETE FROM workspaces WHERE id = ?", (row["id"],))
        remaining = db.execute(
            "SELECT id, is_active FROM workspaces ORDER BY id"
        ).fetchall()
        if not remaining:
            db.execute(
                "INSERT INTO workspaces (name, path, is_active) VALUES (?, ?, 1)",
                (Path(path).name or path, path),
            )
        elif not any(row["is_active"] for row in remaining):
            db.execute(
                "UPDATE workspaces SET is_active = 1 WHERE id = ?",
                (remaining[0]["id"],),
            )


def list_all(fallback_path: str) -> list[dict]:
    ensure_default(fallback_path)
    with db_session() as db:
        rows = db.execute(
            "SELECT id, name, path, is_active, created_at FROM workspaces ORDER BY is_active DESC, id"
        ).fetchall()
    return [{**dict(row), "is_active": bool(row["is_active"])} for row in rows]


def active(fallback_path: str) -> dict:
    ensure_default(fallback_path)
    with db_session() as db:
        row = db.execute(
            "SELECT id, name, path, is_active, created_at FROM workspaces "
            "WHERE is_active = 1 ORDER BY id LIMIT 1"
        ).fetchone()
    if row is None:
        activate(list_all(fallback_path)[0]["id"])
        return active(fallback_path)
    return {**dict(row), "is_active": True}


def save(name: str, path: str) -> int:
    _ensure_table()
    normalized = _normalize_path(path)
    display_name = name.strip() or Path(normalized).name or normalized
    with db_session() as db:
        existing = db.execute(
            "SELECT id FROM workspaces WHERE path = ?", (normalized,)
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE workspaces SET name = ? WHERE id = ?",
                (display_name, existing["id"]),
            )
            return int(existing["id"])
        has_active = db.execute(
            "SELECT 1 FROM workspaces WHERE is_active = 1"
        ).fetchone()
        cursor = db.execute(
            "INSERT INTO workspaces (name, path, is_active) VALUES (?, ?, ?)",
            (display_name, normalized, 0 if has_active else 1),
        )
        return int(cursor.lastrowid)


def activate(workspace_id: int) -> None:
    _ensure_table()
    with db_session() as db:
        exists = db.execute(
            "SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
        if not exists:
            raise ValueError("workspace not found")
        db.execute("UPDATE workspaces SET is_active = 0")
        db.execute(
            "UPDATE workspaces SET is_active = 1 WHERE id = ?", (workspace_id,)
        )


def activate_path(path: str) -> dict:
    workspace_id = save("", path)
    activate(workspace_id)
    with db_session() as db:
        row = db.execute(
            "SELECT id, name, path, is_active, created_at FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
    return {**dict(row), "is_active": True}


def delete(workspace_id: int, fallback_path: str) -> None:
    ensure_default(fallback_path)
    with db_session() as db:
        row = db.execute(
            "SELECT is_active FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
        if row is None:
            return
        count = db.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
        if count <= 1:
            raise ValueError("cannot delete the last workspace")
        db.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        if row["is_active"]:
            replacement = db.execute(
                "SELECT id FROM workspaces ORDER BY id LIMIT 1"
            ).fetchone()
            db.execute(
                "UPDATE workspaces SET is_active = 1 WHERE id = ?",
                (replacement["id"],),
            )
