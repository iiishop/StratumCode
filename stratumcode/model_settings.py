from . import providers
from .db import db_session

DEFAULT_STAGE = "default"
LIGHT_AGENT = "light_agent"
EVIDENCE_STAGE = "evidence"
GIT_COMMIT_STAGE = "git_commit"
TITLE_STAGE = "title"
SUMMARY_STAGE = "summary"
MEMORY_STAGE = "memory"

# 单一事实来源：前端 stage 列表、顺序与文案都由这里下发，加 stage 只改这一份。
# 顺序即数组顺序；编号由前端按数组下标生成，不在此维护。
STAGE_META = [
    {"id": DEFAULT_STAGE,     "title": "Global default",        "detail": "Fallback for every stage"},
    {"id": LIGHT_AGENT,       "title": "Light agent",           "detail": "Lightweight agent for simple tasks"},
    {"id": EVIDENCE_STAGE,    "title": "Gather evidence",       "detail": "Hypothesis verification loop"},
    {"id": GIT_COMMIT_STAGE,  "title": "Git commit message",    "detail": "Generate commit title and description"},
    {"id": TITLE_STAGE,       "title": "Session title",         "detail": "Generate session title from conversation"},
    {"id": SUMMARY_STAGE,     "title": "Investigation summary", "detail": "Summarize investigation findings"},
    {"id": MEMORY_STAGE,      "title": "Memory extraction",     "detail": "Extract conversation references and memory candidates"},
]
VALID_STAGES = {item["id"] for item in STAGE_META}


def stage_meta() -> list[dict]:
    """Stage list and display metadata for the frontend providers page."""
    return [dict(item) for item in STAGE_META]


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
    output_limit = providers.model_output_limit(
        provider["base_url"],
        provider["api_key"],
        row["model_id"],
    )
    if output_limit:
        provider["model_output_tokens"] = output_limit
    return {
        "requested_stage": stage,
        "configured_stage": row["stage"],
        "inherited": inherited,
        "provider_id": row["provider_id"],
        "model_id": row["model_id"],
        "provider": provider,
    }
