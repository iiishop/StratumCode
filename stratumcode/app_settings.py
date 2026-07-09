from __future__ import annotations

import time

from . import db

LANGUAGES = {
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
}
DEFAULT_OUTPUT_LANGUAGE = "zh"


def _init() -> None:
    with db.db_session() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )


def get_output_language() -> str:
    _init()
    with db.db_session() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'output_language'"
        ).fetchone()
    value = row["value"] if row else DEFAULT_OUTPUT_LANGUAGE
    return value if value in LANGUAGES else DEFAULT_OUTPUT_LANGUAGE


def save_output_language(language: str) -> str:
    value = str(language or "").strip().casefold()
    if value not in LANGUAGES:
        raise ValueError("output_language must be en, zh, or ja")
    _init()
    with db.db_session() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES ('output_language', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (value, time.time()),
        )
    return value


def to_json() -> dict:
    language = get_output_language()
    return {
        "output_language": language,
        "languages": [
            {"id": key, "label": label}
            for key, label in LANGUAGES.items()
        ],
    }
