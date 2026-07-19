import json
import base64
import time
from functools import lru_cache
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .db import db_session

CODEX_AUTH_ISSUER = "https://auth.openai.com"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


def _models_payload(base_url: str, api_key: str) -> dict:
    """从 OpenAI 兼容端点获取可用模型 ID 列表。"""
    req = Request(
        f"{base_url.rstrip('/')}/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urlopen(req, timeout=20) as resp:
        return json.load(resp)


def list_models(base_url: str, api_key: str, account_id: str = "") -> list[str]:
    if _is_codex_url(base_url):
        return _codex_models(api_key, account_id)
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
    if _is_codex_url(base_url):
        return _test_codex_model(api_key, model_id)
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
                auth_type TEXT NOT NULL DEFAULT 'api_key',
                oauth_refresh TEXT NOT NULL DEFAULT '',
                oauth_expires_at INTEGER NOT NULL DEFAULT 0,
                oauth_account_id TEXT NOT NULL DEFAULT '',
                pricing_rules TEXT NOT NULL DEFAULT '[]'
            )
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(providers)").fetchall()}
        if "pricing_rules" not in columns:
            db.execute("ALTER TABLE providers ADD COLUMN pricing_rules TEXT NOT NULL DEFAULT '[]'")
        if "auth_type" not in columns:
            db.execute("ALTER TABLE providers ADD COLUMN auth_type TEXT NOT NULL DEFAULT 'api_key'")
        if "oauth_refresh" not in columns:
            db.execute("ALTER TABLE providers ADD COLUMN oauth_refresh TEXT NOT NULL DEFAULT ''")
        if "oauth_expires_at" not in columns:
            db.execute("ALTER TABLE providers ADD COLUMN oauth_expires_at INTEGER NOT NULL DEFAULT 0")
        if "oauth_account_id" not in columns:
            db.execute("ALTER TABLE providers ADD COLUMN oauth_account_id TEXT NOT NULL DEFAULT ''")
        db.execute("""
            CREATE TABLE IF NOT EXISTS model_pricing (
                provider_id INTEGER NOT NULL,
                model_id TEXT NOT NULL,
                pricing_rules TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (provider_id, model_id),
                FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE
            )
        """)


def save(name: str, base_url: str, api_key: str, pricing_rules=None, *, auth_type: str = "api_key", oauth_refresh: str = "", oauth_expires_at: int = 0, oauth_account_id: str = "") -> int:
    _ensure_table()
    pricing = json.dumps(pricing_rules or [], ensure_ascii=False)
    with db_session() as db:
        cur = db.execute(
            """
            INSERT INTO providers
                (name, base_url, api_key, auth_type, oauth_refresh, oauth_expires_at, oauth_account_id, pricing_rules)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, base_url, api_key, auth_type, oauth_refresh, int(oauth_expires_at or 0), oauth_account_id, pricing),
        )
        return cur.lastrowid


def start_codex_oauth() -> dict:
    req = Request(
        f"{CODEX_AUTH_ISSUER}/api/accounts/deviceauth/usercode",
        data=json.dumps({"client_id": CODEX_CLIENT_ID}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "StratumCode/0.1"},
    )
    with urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    return {
        "device_auth_id": data["device_auth_id"],
        "user_code": data["user_code"],
        "interval": int(data.get("interval") or 5),
        "verification_uri": f"{CODEX_AUTH_ISSUER}/codex/device",
    }


def finish_codex_oauth(device_auth_id: str, user_code: str) -> dict:
    req = Request(
        f"{CODEX_AUTH_ISSUER}/api/accounts/deviceauth/token",
        data=json.dumps({"device_auth_id": device_auth_id, "user_code": user_code}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "StratumCode/0.1"},
    )
    try:
        with urlopen(req, timeout=20) as resp:
            device = json.load(resp)
    except HTTPError as exc:
        if exc.code in {403, 404}:
            return {"pending": True}
        raise
    tokens = _codex_exchange_code(device["authorization_code"], device["code_verifier"])
    provider_id = save(
        "OpenAI Codex",
        CODEX_BASE_URL,
        tokens["access_token"],
        auth_type="codex_oauth",
        oauth_refresh=tokens["refresh_token"],
        oauth_expires_at=_expires_at(tokens),
        oauth_account_id=_extract_account_id(tokens),
    )
    return {"pending": False, "id": provider_id}


def codex_access_token(provider: dict) -> tuple[str, str]:
    expires = int(provider.get("oauth_expires_at") or 0)
    if provider.get("api_key") and expires > int(time.time()) + 120:
        return provider["api_key"], provider.get("oauth_account_id", "")
    refresh = provider.get("oauth_refresh") or ""
    if not refresh:
        raise ValueError("Codex OAuth refresh token is missing")
    tokens = _codex_refresh(refresh)
    access = tokens["access_token"]
    account_id = _extract_account_id(tokens) or provider.get("oauth_account_id", "")
    with db_session() as db:
        db.execute(
            """
            UPDATE providers
            SET api_key = ?, oauth_refresh = ?, oauth_expires_at = ?, oauth_account_id = ?
            WHERE id = ?
            """,
            (
                access,
                tokens.get("refresh_token") or refresh,
                _expires_at(tokens),
                account_id,
                int(provider["id"]),
            ),
        )
    return access, account_id


def list_saved() -> list[dict]:
    _ensure_table()
    with db_session() as db:
        rows = db.execute("SELECT * FROM providers ORDER BY id").fetchall()
    items = [dict(r) for r in rows]
    for item in items:
        item["oauth_refresh"] = ""
        if item.get("auth_type") == "codex_oauth":
            item["api_key"] = ""
    return items


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


def _is_codex_url(base_url: str) -> bool:
    return str(base_url or "").rstrip("/") == CODEX_BASE_URL


def _codex_models(api_key: str, account_id: str = "") -> list[str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "StratumCode/0.1",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    req = Request(f"{CODEX_BASE_URL}/models?client_version=1.0.0", headers=headers)
    with urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    items = data.get("models") or data.get("data") or []
    ranked = []
    for item in items:
        model = _model_id(item)
        if not model:
            continue
        visibility = str(item.get("visibility") or "").strip().lower() if isinstance(item, dict) else ""
        if visibility in {"hide", "hidden"}:
            continue
        priority = item.get("priority") if isinstance(item, dict) else None
        rank = int(priority) if isinstance(priority, (int, float)) else 10_000
        ranked.append((rank, model))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return _dedupe_models([model for _, model in ranked])


def _model_id(item) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return ""
    api = item.get("api") if isinstance(item.get("api"), dict) else {}
    value = item.get("slug") or item.get("id") or item.get("name") or item.get("model") or item.get("model_slug") or api.get("id")
    return str(value) if value else ""


def _dedupe_models(models: list[str]) -> list[str]:
    result = []
    seen = set()
    for model in models:
        if model in seen:
            continue
        seen.add(model)
        result.append(model)
    return result


def _test_codex_model(api_key: str, model_id: str) -> tuple[bool, str]:
    req = Request(
        f"{CODEX_BASE_URL}/responses",
        data=json.dumps({
            "model": model_id,
            "input": [{"role": "user", "content": "Say hello in one word."}],
            "max_output_tokens": 10,
        }).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "StratumCode/0.1",
            "originator": "stratumcode",
        },
    )
    try:
        with urlopen(req, timeout=90) as resp:
            data = json.load(resp)
        text = []
        for item in data.get("output") or []:
            if isinstance(item, dict) and item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("text"):
                        text.append(str(part["text"]))
        return True, "\n".join(text).strip() or "OK"
    except URLError as e:
        return False, str(e)


def _codex_exchange_code(code: str, verifier: str) -> dict:
    return _codex_token({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": f"{CODEX_AUTH_ISSUER}/deviceauth/callback",
        "client_id": CODEX_CLIENT_ID,
        "code_verifier": verifier,
    })


def _codex_refresh(refresh_token: str) -> dict:
    return _codex_token({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CODEX_CLIENT_ID,
    })


def _codex_token(params: dict) -> dict:
    req = Request(
        f"{CODEX_AUTH_ISSUER}/oauth/token",
        data=urlencode(params).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "StratumCode/0.1"},
    )
    with urlopen(req, timeout=20) as resp:
        return json.load(resp)


def _expires_at(tokens: dict) -> int:
    return int(time.time()) + int(tokens.get("expires_in") or 3600)


def _extract_account_id(tokens: dict) -> str:
    for key in ("id_token", "access_token"):
        claims = _jwt_claims(tokens.get(key, ""))
        if not claims:
            continue
        account_id = (
            claims.get("chatgpt_account_id")
            or (claims.get("https://api.openai.com/auth") or {}).get("chatgpt_account_id")
            or ((claims.get("organizations") or [{}])[0] or {}).get("id")
        )
        if account_id:
            return str(account_id)
    return ""


def _jwt_claims(token: str) -> dict:
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode()))
    except Exception:
        return {}
