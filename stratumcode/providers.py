import json
from urllib.request import Request, urlopen
from urllib.error import URLError

from .db import get_db


def list_models(base_url: str, api_key: str) -> list[str]:
    """从 OpenAI 兼容端点获取可用模型 ID 列表。"""
    req = Request(
        f"{base_url.rstrip('/')}/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urlopen(req) as resp:
        data = json.load(resp)
    return [m["id"] for m in data["data"]]


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
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            api_key TEXT NOT NULL
        )
    """)
    db.commit()


def save(name: str, base_url: str, api_key: str) -> int:
    _ensure_table()
    db = get_db()
    cur = db.execute(
        "INSERT INTO providers (name, base_url, api_key) VALUES (?, ?, ?)",
        (name, base_url, api_key),
    )
    db.commit()
    return cur.lastrowid


def list_saved() -> list[dict]:
    _ensure_table()
    db = get_db()
    rows = db.execute("SELECT * FROM providers ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_saved(provider_id: int) -> dict | None:
    _ensure_table()
    row = get_db().execute("SELECT * FROM providers WHERE id = ?", (provider_id,)).fetchone()
    return dict(row) if row else None


def delete(provider_id: int):
    db = get_db()
    db.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
    db.commit()
