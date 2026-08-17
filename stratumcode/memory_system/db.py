from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .schema import FTS_SCHEMA, SCHEMA

MEMORY_DIR = ".StratumCode"
MEMORY_DB = "memory.sqlite"


def memory_root(workspace_dir: str) -> Path:
    return Path(workspace_dir or ".").expanduser().resolve() / MEMORY_DIR


def memory_db_path(workspace_dir: str) -> Path:
    return memory_root(workspace_dir) / MEMORY_DB


def initialize(workspace_dir: str) -> Path:
    root = memory_root(workspace_dir)
    root.mkdir(parents=True, exist_ok=True)
    _ensure_gitignore(Path(workspace_dir or ".").expanduser().resolve())
    path = root / MEMORY_DB
    with _connect(path) as conn:
        conn.executescript(SCHEMA)
        if _fts5_available(conn):
            conn.executescript(FTS_SCHEMA)
        conn.commit()
    return path


@contextmanager
def db_session(workspace_dir: str):
    path = initialize(workspace_dir)
    conn = _connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp._memory_fts_probe USING fts5(value)")
        conn.execute("DROP TABLE IF EXISTS temp._memory_fts_probe")
        return True
    except sqlite3.DatabaseError:
        return False


def _ensure_gitignore(workspace: Path) -> None:
    gitignore = workspace / ".gitignore"
    marker = f"{MEMORY_DIR}/"
    if gitignore.exists():
        text = gitignore.read_text(encoding="utf-8", errors="ignore")
        if any(line.strip() == marker for line in text.splitlines()):
            return
        suffix = "" if text.endswith(("\n", "\r")) else "\n"
        gitignore.write_text(text + suffix + marker + "\n", encoding="utf-8")
        return
    gitignore.write_text(marker + "\n", encoding="utf-8")
