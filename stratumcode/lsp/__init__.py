"""LSP management: auto-syncs from the Mason registry, per-server install/enable state, and probe."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from ..db import db_session

_MASON_REPO = "https://github.com/mason-org/mason-registry"
_MASON_CACHE = Path.home() / ".cache" / "stratumcode" / "mason-registry"
_SYNC_INTERVAL = 3600  # re-sync if older than 1 hour


def _ensure_table() -> None:
    with db_session() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS lsp_servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                languages_json TEXT NOT NULL DEFAULT '[]',
                homepage TEXT NOT NULL DEFAULT '',
                installed INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 0,
                install_version TEXT NOT NULL DEFAULT '',
                install_error TEXT NOT NULL DEFAULT '',
                env_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def _loads(value: str, fallback):
    try:
        return json.loads(value or "")
    except json.JSONDecodeError:
        return fallback


def _row_to_server(row) -> dict:
    data = dict(row)
    data["installed"] = bool(data["installed"])
    data["enabled"] = bool(data["enabled"])
    data["languages"] = _loads(data.pop("languages_json"), [])
    data["env"] = _loads(data.pop("env_json"), {})
    return data


def _sync_registry() -> None:
    _MASON_CACHE.parent.mkdir(parents=True, exist_ok=True)
    stamp = _MASON_CACHE / ".last-sync"
    if stamp.exists():
        try:
            age = time.time() - stamp.stat().st_mtime
            if age < _SYNC_INTERVAL:
                return
        except OSError:
            pass
    if (_MASON_CACHE / "packages").is_dir():
        subprocess.run(
            ["git", "-C", str(_MASON_CACHE), "pull", "--depth=1", "--ff-only"],
            capture_output=True,
            timeout=60,
        )
    else:
        subprocess.run(
            ["git", "clone", "--depth=1", _MASON_REPO, str(_MASON_CACHE)],
            capture_output=True,
            timeout=120,
        )
    stamp.write_text("")


def _parse_packages() -> list[dict]:
    pkg_dir = _MASON_CACHE / "packages"
    if not pkg_dir.is_dir():
        return []
    packages = []
    for entry in sorted(pkg_dir.iterdir()):
        pkg_file = entry / "package.json"
        if not pkg_file.is_file():
            pkg_file = entry / "package.yaml"
        if not pkg_file.is_file():
            continue
        try:
            raw = pkg_file.read_text(encoding="utf-8")
            if pkg_file.suffix == ".yaml":
                data = _parse_yaml(raw)
            else:
                data = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        name = str(data.get("name") or entry.name).strip()
        if not name:
            continue
        packages.append({
            "name": name,
            "display_name": str(data.get("display_name") or data.get("name") or name).strip(),
            "description": str(data.get("description") or "").strip(),
            "languages": _normalize_languages(data.get("languages") or data.get("language") or []),
            "homepage": str(data.get("homepage") or "").strip(),
        })
    return packages


def _parse_yaml(raw: str) -> dict:
    try:
        import yaml
        return yaml.safe_load(raw) or {}
    except ImportError:
        pass
    try:
        import tomllib
        return tomllib.loads(raw) or {}
    except ImportError:
        pass
    result = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if ":" in stripped and not stripped.startswith(("#", "-")):
            key, _, value = stripped.partition(":")
            result[key.strip()] = value.strip().strip("\"'")
    return result


def _normalize_languages(raw) -> list[str]:
    if isinstance(raw, str):
        return [raw.strip().casefold()] if raw.strip() else []
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(
        str(item).strip().casefold() for item in raw if str(item).strip()
    ))


def _seed_catalog() -> None:
    _ensure_table()
    _sync_registry()
    packages = _parse_packages()
    with db_session() as db:
        existing = {
            row["name"]: _row_to_server(row)
            for row in db.execute("SELECT * FROM lsp_servers").fetchall()
        }
        seen = set()
        for entry in packages:
            seen.add(entry["name"])
            prev = existing.get(entry["name"])
            if prev is not None:
                entry_display = entry["display_name"] or prev["display_name"]
                entry_desc = entry["description"] or prev["description"]
                entry_homepage = entry["homepage"] or prev["homepage"]
                db.execute(
                    """
                    UPDATE lsp_servers
                    SET display_name = ?, description = ?, languages_json = ?, homepage = ?
                    WHERE name = ?
                    """,
                    (
                        entry_display,
                        entry_desc,
                        json.dumps(entry["languages"], ensure_ascii=False),
                        entry_homepage,
                        entry["name"],
                    ),
                )
            else:
                db.execute(
                    """
                    INSERT INTO lsp_servers (name, display_name, description, languages_json, homepage)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        entry["name"],
                        entry["display_name"],
                        entry["description"],
                        json.dumps(entry["languages"], ensure_ascii=False),
                        entry["homepage"],
                    ),
                )
        for name in set(existing) - seen:
            db.execute("DELETE FROM lsp_servers WHERE name = ?", (name,))


def list_all(language: str | None = None, query: str | None = None) -> list[dict]:
    _seed_catalog()
    with db_session() as db:
        rows = db.execute("SELECT * FROM lsp_servers ORDER BY display_name").fetchall()
    servers = [_row_to_server(row) for row in rows]
    result = []
    for server in servers:
        if language and language not in server["languages"]:
            continue
        if query:
            q = query.casefold()
            if (
                q not in server["name"].casefold()
                and q not in server["display_name"].casefold()
                and not any(q in lang for lang in server["languages"])
            ):
                continue
        result.append(server)
    return result


def languages() -> list[str]:
    _seed_catalog()
    with db_session() as db:
        rows = db.execute("SELECT languages_json FROM lsp_servers").fetchall()
    langs = set()
    for row in rows:
        langs.update(_loads(row["languages_json"], []))
    return sorted(langs)


def install(name: str) -> dict:
    _seed_catalog()
    _ensure_table()
    with db_session() as db:
        row = db.execute("SELECT * FROM lsp_servers WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"lsp server not found: {name}")
        server = _row_to_server(row)
        if server["installed"]:
            raise ValueError(f"{name} is already installed")
        try:
            result = subprocess.run(
                ["mason", "install", name],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                server["installed"] = True
                server["enabled"] = True
                server["install_version"] = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "installed"
            else:
                raise ValueError(result.stderr.strip() or result.stdout.strip() or "mason install failed")
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            server["install_error"] = str(exc)
            db.execute(
                "UPDATE lsp_servers SET install_error = ? WHERE name = ?",
                (server["install_error"], name),
            )
            raise ValueError(server["install_error"]) from exc
        db.execute(
            """
            UPDATE lsp_servers
            SET installed = ?, enabled = ?, install_version = ?, install_error = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE name = ?
            """,
            (
                int(server["installed"]),
                int(server["enabled"]),
                server["install_version"],
                name,
            ),
        )
    return get(name)


def uninstall(name: str) -> dict:
    _seed_catalog()
    _ensure_table()
    with db_session() as db:
        row = db.execute("SELECT * FROM lsp_servers WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"lsp server not found: {name}")
        server = _row_to_server(row)
        if not server["installed"]:
            raise ValueError(f"{name} is not installed")
        try:
            subprocess.run(
                ["mason", "uninstall", name],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
        db.execute(
            """
            UPDATE lsp_servers
            SET installed = 0, enabled = 0, install_version = '', install_error = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE name = ?
            """,
            (name,),
        )
    return get(name)


def enable(name: str, value: bool) -> dict:
    _seed_catalog()
    _ensure_table()
    with db_session() as db:
        row = db.execute("SELECT * FROM lsp_servers WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"lsp server not found: {name}")
        server = _row_to_server(row)
        if not server["installed"]:
            raise ValueError(f"cannot enable {name}: not installed")
        db.execute(
            "UPDATE lsp_servers SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
            (int(value), name),
        )
    return get(name)


def configure(name: str, env: dict) -> dict:
    _seed_catalog()
    _ensure_table()
    with db_session() as db:
        row = db.execute("SELECT * FROM lsp_servers WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"lsp server not found: {name}")
        current_env = _row_to_server(row).get("env", {})
        current_env.update(env)
        db.execute(
            "UPDATE lsp_servers SET env_json = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
            (json.dumps(current_env, ensure_ascii=False), name),
        )
    return get(name)


def probe(name: str) -> dict:
    _seed_catalog()
    _ensure_table()
    with db_session() as db:
        row = db.execute("SELECT * FROM lsp_servers WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"lsp server not found: {name}")
        server = _row_to_server(row)
        if not server["installed"]:
            raise ValueError(f"cannot probe {name}: not installed")
        result = {"available": False, "version": "", "error": ""}
        try:
            proc = subprocess.run(
                [name, "--version"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                result["available"] = True
                result["version"] = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "unknown"
            else:
                result["error"] = proc.stderr.strip() or proc.stdout.strip() or "non-zero exit"
        except (subprocess.TimeoutExpired, OSError) as exc:
            result["error"] = str(exc)
    return {"name": name, **server, "probe": result}


def get(name: str) -> dict:
    _seed_catalog()
    with db_session() as db:
        row = db.execute("SELECT * FROM lsp_servers WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise ValueError(f"lsp server not found: {name}")
    return _row_to_server(row)


def sync_now() -> dict:
    stamp = _MASON_CACHE / ".last-sync"
    if stamp.exists():
        stamp.unlink()
    _seed_catalog()
    return {"ok": True}
