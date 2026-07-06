import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "stratumcode.db"


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db
