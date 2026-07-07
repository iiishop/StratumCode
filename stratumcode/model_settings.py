from . import providers
from .db import db_session

DEFAULT_STAGE = "default"
EVIDENCE_STAGE = "evidence"
VALID_STAGES = {DEFAULT_STAGE, EVIDENCE_STAGE}


def _ensure_table() -> None:
    with db_session() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS model_settings (
                stage TEXT PRIMARY KEY,
                provider_id INTEGER NOT NULL,
                model_id TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def list_all() -> list[dict]:
    _ensure_table()
    with db_session() as db:
        rows = db.execute(
            "SELECT stage, provider_id, model_id, updated_at FROM model_settings"
        ).fetchall()
    return [dict(row) for row in rows]


def save(stage: str, provider_id: int, model_id: str) -> None:
    _ensure_table()
    if stage not in VALID_STAGES:
        raise ValueError(f"unknown model stage: {stage}")
    if providers.get_saved(provider_id) is None:
        raise ValueError("provider not found")
    if not model_id.strip():
        raise ValueError("model_id is required")
    with db_session() as db:
        db.execute(
            """
            INSERT INTO model_settings (stage, provider_id, model_id, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(stage) DO UPDATE SET
                provider_id = excluded.provider_id,
                model_id = excluded.model_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (stage, provider_id, model_id.strip()),
        )


def delete(stage: str) -> None:
    if stage not in VALID_STAGES:
        raise ValueError(f"unknown model stage: {stage}")
    _ensure_table()
    with db_session() as db:
        db.execute("DELETE FROM model_settings WHERE stage = ?", (stage,))


def resolve(stage: str) -> dict | None:
    if stage not in VALID_STAGES:
        raise ValueError(f"unknown model stage: {stage}")
    _ensure_table()
    with db_session() as db:
        row = db.execute(
            "SELECT stage, provider_id, model_id FROM model_settings WHERE stage = ?",
            (stage,),
        ).fetchone()
        inherited = False
        if row is None and stage != DEFAULT_STAGE:
            row = db.execute(
                "SELECT stage, provider_id, model_id FROM model_settings WHERE stage = ?",
                (DEFAULT_STAGE,),
            ).fetchone()
            inherited = row is not None
    if row is None:
        return None
    provider = providers.get_saved(row["provider_id"])
    if provider is None:
        return None
    return {
        "requested_stage": stage,
        "configured_stage": row["stage"],
        "inherited": inherited,
        "provider_id": row["provider_id"],
        "model_id": row["model_id"],
        "provider": provider,
    }
