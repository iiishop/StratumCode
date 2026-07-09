import json
from functools import lru_cache
from urllib.request import Request, urlopen
from urllib.error import URLError

from .db import db_session


def _models_payload(base_url: str, api_key: str) -> dict:
    """从 OpenAI 兼容端点获取可用模型 ID 列表。"""
    req = Request(
        f"{base_url.rstrip('/')}/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urlopen(req, timeout=20) as resp:
        return json.load(resp)


def list_models(base_url: str, api_key: str) -> list[str]:
    data = _models_payload(base_url, api_key)
    return [m["id"] for m in data["data"]]


def model_context_length(base_url: str, api_key: str, model_id: str) -> int | None:
    try:
        data = _models_payload(base_url, api_key)
    except Exception:
        return None
    model = next((item for item in data.get("data", []) if item.get("id") == model_id), {})
    for key in ("context_length", "context_window", "max_context_length", "max_input_tokens"):
        try:
            value = int(model.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return _models_dev_context_length(model_id) or _litellm_context_length(model_id)


@lru_cache(maxsize=1)
def _models_dev_payload() -> dict:
    req = Request(
        "https://models.dev/api.json",
        headers={"User-Agent": "StratumCode/0.1"},
    )
    with urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    return data if isinstance(data, dict) else {}


def _models_dev_context_length(model_id: str) -> int | None:
    try:
        providers_payload = _models_dev_payload()
    except Exception:
        return None
    for provider in providers_payload.values():
        models = provider.get("models", {}) if isinstance(provider, dict) else {}
        model = models.get(model_id)
        if not model:
            model = next((value for key, value in models.items() if key.endswith("/" + model_id)), None)
        if isinstance(model, dict):
            limit = model.get("limit", {})
            try:
                context = int(limit.get("context") or 0)
            except (TypeError, ValueError):
                context = 0
            if context > 0:
                return context
    return None


@lru_cache(maxsize=1)
def _litellm_payload() -> dict:
    req = Request(
        "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
        headers={"User-Agent": "StratumCode/0.1"},
    )
    with urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    return data if isinstance(data, dict) else {}


def _litellm_context_length(model_id: str) -> int | None:
    try:
        model = _litellm_payload().get(model_id) or {}
    except Exception:
        return None
    for key in ("max_input_tokens", "max_tokens"):
        try:
            value = int(model.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return None


def test_connection(base_url: str, api_key: str) -> tuple[bool, str]:
    """测试 base_url 和 api_key 是否有效。返回 (是否成功, 详情)。"""
    try:
        models = list_models(base_url, api_key)
        return True, f"OK — {len(models)} models available"
    except URLError as e:
        return False, str(e)


def test_model(base_url: str, api_key: str, model_id: str) -> tuple[bool, str]:
    """发送一条聊天请求并返回回复内容。返回 (是否成功, 详情)。"""
    req = Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps({
            "model": model_id,
            "messages": [{"role": "user", "content": "Say hello in one word."}],
            "max_tokens": 10,
        }).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req) as resp:
            reply = json.load(resp)["choices"][0]["message"]["content"].strip()
        return True, reply
    except URLError as e:
        return False, str(e)


# --- 持久化 ---

def _ensure_table():
    with db_session() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT NOT NULL,
                pricing_rules TEXT NOT NULL DEFAULT '[]'
            )
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(providers)").fetchall()}
        if "pricing_rules" not in columns:
            db.execute("ALTER TABLE providers ADD COLUMN pricing_rules TEXT NOT NULL DEFAULT '[]'")
        db.execute("""
            CREATE TABLE IF NOT EXISTS model_pricing (
                provider_id INTEGER NOT NULL,
                model_id TEXT NOT NULL,
                pricing_rules TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (provider_id, model_id),
                FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE
            )
        """)


def save(name: str, base_url: str, api_key: str, pricing_rules=None) -> int:
    _ensure_table()
    pricing = json.dumps(pricing_rules or [], ensure_ascii=False)
    with db_session() as db:
        cur = db.execute(
            "INSERT INTO providers (name, base_url, api_key, pricing_rules) VALUES (?, ?, ?, ?)",
            (name, base_url, api_key, pricing),
        )
        return cur.lastrowid


def list_saved() -> list[dict]:
    _ensure_table()
    with db_session() as db:
        rows = db.execute("SELECT * FROM providers ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_saved(provider_id: int) -> dict | None:
    _ensure_table()
    with db_session() as db:
        row = db.execute(
            "SELECT * FROM providers WHERE id = ?", (provider_id,)
        ).fetchone()
    return dict(row) if row else None


def save_model_pricing(provider_id: int, model_id: str, pricing_rules) -> None:
    _ensure_table()
    if not model_id:
        raise ValueError("model_id is required")
    pricing = json.dumps(pricing_rules or [], ensure_ascii=False)
    with db_session() as db:
        db.execute(
            """
            INSERT INTO model_pricing (provider_id, model_id, pricing_rules)
            VALUES (?, ?, ?)
            ON CONFLICT(provider_id, model_id)
            DO UPDATE SET pricing_rules = excluded.pricing_rules
            """,
            (provider_id, model_id, pricing),
        )


def get_model_pricing(provider_id: int, model_id: str) -> list[dict]:
    _ensure_table()
    with db_session() as db:
        row = db.execute(
            "SELECT pricing_rules FROM model_pricing WHERE provider_id = ? AND model_id = ?",
            (provider_id, model_id),
        ).fetchone()
    if not row:
        return []
    try:
        rules = json.loads(row["pricing_rules"] or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(rules, dict):
        return [rules]
    return rules if isinstance(rules, list) else []


def delete(provider_id: int):
    _ensure_table()
    with db_session() as db:
        has_settings = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'model_settings'"
        ).fetchone()
        if has_settings:
            db.execute("DELETE FROM model_settings WHERE provider_id = ?", (provider_id,))
        db.execute("DELETE FROM model_pricing WHERE provider_id = ?", (provider_id,))
        db.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
