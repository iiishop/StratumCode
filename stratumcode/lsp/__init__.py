"""LSP management: auto-syncs from the Mason registry, per-server install/enable state, and probe."""

from __future__ import annotations

import json
import os
import queue
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from shutil import which
from urllib.parse import quote

from ..db import db_session

_MASON_REPO = "https://github.com/mason-org/mason-registry"
_MASON_CACHE = Path.home() / ".cache" / "stratumcode" / "mason-registry"
_SYNC_INTERVAL = 3600  # re-sync if older than 1 hour
LSP_ROOT = Path.home() / ".local" / "share" / "stratumcode" / "lsp"
_MASON_PLUGIN_REPO = "https://github.com/mason-org/mason.nvim"
_clients: dict[tuple[str, str], "_LspClient"] = {}
_clients_lock = threading.RLock()
_diagnostics: dict[str, list[dict]] = {}
_diagnostics_lock = threading.RLock()
_NPM_LSP = {
    "astro-language-server": {
        "languages": ["astro"],
        "display_name": "Astro Language Server",
        "packages": ["typescript", "@astrojs/language-server"],
        "command": "astro-ls",
        "args": ["--stdio"],
    },
    "bash-language-server": {
        "languages": ["shell"],
        "display_name": "Bash Language Server",
        "packages": ["bash-language-server"],
        "command": "bash-language-server",
        "args": ["start"],
    },
    "vue-language-server": {
        "languages": ["vue", "typescript", "javascript"],
        "display_name": "Vue Language Server",
        "packages": ["typescript", "@vue/language-server"],
        "command": "vue-language-server",
        "args": ["--stdio"],
    },
    "css-lsp": {
        "languages": ["css", "scss", "less"],
        "display_name": "CSS Language Server",
        "packages": ["vscode-langservers-extracted"],
        "command": "vscode-css-language-server",
        "args": ["--stdio"],
    },
    "dockerfile-language-server": {
        "languages": ["dockerfile"],
        "display_name": "Dockerfile Language Server",
        "packages": ["dockerfile-language-server-nodejs"],
        "command": "docker-langserver",
        "args": ["--stdio"],
    },
    "html-lsp": {
        "languages": ["html"],
        "display_name": "HTML Language Server",
        "packages": ["vscode-langservers-extracted"],
        "command": "vscode-html-language-server",
        "args": ["--stdio"],
    },
    "json-lsp": {
        "languages": ["json"],
        "display_name": "JSON Language Server",
        "packages": ["vscode-langservers-extracted"],
        "command": "vscode-json-language-server",
        "args": ["--stdio"],
    },
    "prisma-language-server": {
        "languages": ["prisma"],
        "display_name": "Prisma Language Server",
        "packages": ["@prisma/language-server"],
        "command": "prisma-language-server",
        "args": ["--stdio"],
    },
    "svelte-language-server": {
        "languages": ["svelte"],
        "display_name": "Svelte Language Server",
        "packages": ["typescript", "svelte-language-server"],
        "command": "svelteserver",
        "args": ["--stdio"],
    },
    "tailwindcss-language-server": {
        "languages": ["html", "css", "javascript", "typescript"],
        "display_name": "Tailwind CSS Language Server",
        "packages": ["@tailwindcss/language-server"],
        "command": "tailwindcss-language-server",
        "args": ["--stdio"],
    },
    "typescript-language-server": {
        "languages": ["typescript", "javascript", "typescriptreact", "javascriptreact"],
        "display_name": "TypeScript Language Server",
        "packages": ["typescript", "typescript-language-server"],
        "command": "typescript-language-server",
        "args": ["--stdio"],
    },
    "yaml-language-server": {
        "languages": ["yaml"],
        "display_name": "YAML Language Server",
        "packages": ["yaml-language-server"],
        "command": "yaml-language-server",
        "args": ["--stdio"],
    },
    "pyright": {
        "languages": ["python"],
        "display_name": "Pyright",
        "packages": ["pyright"],
        "command": "pyright-langserver",
        "args": ["--stdio"],
    },
    "basedpyright": {
        "languages": ["python"],
        "display_name": "BasedPyright",
        "packages": ["basedpyright"],
        "command": "basedpyright-langserver",
        "args": ["--stdio"],
    },
}


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
                executable TEXT NOT NULL DEFAULT '',
                args_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(lsp_servers)").fetchall()}
        if "executable" not in columns:
            db.execute("ALTER TABLE lsp_servers ADD COLUMN executable TEXT NOT NULL DEFAULT ''")
        if "args_json" not in columns:
            db.execute("ALTER TABLE lsp_servers ADD COLUMN args_json TEXT NOT NULL DEFAULT '[]'")


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
    data["args"] = _loads(data.pop("args_json", "[]"), [])
    data["available"] = bool(data.get("executable") and _command_available(data["executable"]))
    data["status"] = "ready" if data["enabled"] and data["available"] else "missing" if data["enabled"] else "disabled"
    return data


def _command_available(command: str) -> bool:
    if not command:
        return False
    if command == "mason":
        return bool(_mason_command())
    if command == "git":
        return bool(_tool_command("git"))
    if command == "nvim":
        return bool(_tool_command("nvim"))
    path = Path(command)
    if path.is_absolute() or any(sep in command for sep in ("/", "\\")):
        return path.exists()
    return which(command) is not None


def _tool_command(command: str) -> str:
    found = which(command)
    if found:
        return found
    if os.name == "nt":
        common = {
            "git": [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "cmd" / "git.exe"],
            "nvim": [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Neovim" / "bin" / "nvim.exe"],
        }
        return next((str(path) for path in common.get(command, []) if path.exists()), "")
    return ""


def _mason_bin() -> Path:
    return LSP_ROOT / "bin" / ("mason.cmd" if os.name == "nt" else "mason")


def _mason_package_executable(name: str) -> str:
    command = _NPM_LSP.get(name, {}).get("command", name)
    suffixes = [".cmd", ".exe", ""] if os.name == "nt" else [""]
    for suffix in suffixes:
        path = LSP_ROOT / "mason" / "bin" / f"{command}{suffix}"
        if path.exists():
            return str(path)
    return str(LSP_ROOT / "mason" / "bin" / (command + (".cmd" if os.name == "nt" else "")))


def _mason_command() -> str:
    found = which("mason")
    if found:
        return found
    return str(_mason_bin()) if _mason_bin().exists() and _tool_command("nvim") else ""


def mason_status() -> dict:
    command = _mason_command()
    return {"available": bool(command), "command": command}


def bootstrap_status() -> dict:
    git = _tool_command("git")
    nvim = _tool_command("nvim")
    mason = _mason_command()
    return {
        "git": {"available": bool(git), "command": git},
        "nvim": {"available": bool(nvim), "command": nvim},
        "mason": {"available": bool(mason), "command": mason},
    }


def bootstrap_mason_events():
    yield {"op": "status", "status": bootstrap_status()}
    for name, installer in (("git", _install_git), ("nvim", _install_nvim), ("mason", install_mason)):
        status = bootstrap_status()[name]
        if status["available"]:
            yield {"op": "step", "name": name, "status": "done", "message": status["command"]}
            continue
        yield {"op": "step", "name": name, "status": "running", "message": "installing"}
        try:
            installer()
            status = bootstrap_status()[name]
            if not status["available"]:
                raise ValueError(f"{name} installed but is not available in this process")
            yield {"op": "step", "name": name, "status": "done", "message": status["command"]}
        except ValueError as exc:
            yield {"op": "step", "name": name, "status": "error", "message": str(exc)}
            return
    yield {"op": "done", "status": bootstrap_status()}


def _install_git() -> None:
    _winget_install("Git.Git", "Git")


def _install_nvim() -> None:
    _winget_install("Neovim.Neovim", "Neovim")


def _winget_install(package_id: str, label: str) -> None:
    winget = which("winget")
    if os.name != "nt" or not winget:
        raise ValueError(f"{label} is required. Automatic install currently needs winget on Windows.")
    _run_checked([
        winget, "install", "--id", package_id, "--exact",
        "--accept-package-agreements", "--accept-source-agreements",
    ], timeout=600)


def _run_checked(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ValueError(str(exc)) from exc
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or "install failed")
    return result


def install_mason() -> dict:
    if not _tool_command("git"):
        raise ValueError("git is required to install mason.nvim")
    if not _tool_command("nvim"):
        raise ValueError("Neovim (nvim >= 0.10) is required to run mason.nvim")
    plugin = LSP_ROOT / "mason.nvim"
    if not plugin.exists():
        try:
            subprocess.run(
                [_tool_command("git"), "clone", "--depth=1", _MASON_PLUGIN_REPO, str(plugin)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise ValueError(f"failed to install mason.nvim: {detail}") from exc
    _write_mason_shim(plugin)
    return mason_status()


def _write_mason_shim(plugin: Path) -> None:
    bin_path = _mason_bin()
    script_path = LSP_ROOT / "mason-shim.lua"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(_mason_lua(), encoding="utf-8")
    root = str((LSP_ROOT / "mason").resolve())
    plugin_arg = str(plugin.resolve()).replace("\\", "/")
    script_arg = str(script_path.resolve()).replace("\\", "/")
    nvim = _tool_command("nvim")
    if not nvim:
        raise ValueError("Neovim (nvim >= 0.10) is required to run mason.nvim")
    nvim_arg = nvim.replace("\\", "/")
    if os.name == "nt":
        bin_path.write_text(
            "@echo off\r\n"
            "setlocal\r\n"
            f"set \"STRATUM_MASON_ROOT={root}\"\r\n"
            "set \"STRATUM_MASON_ACTION=%~1\"\r\n"
            "set \"STRATUM_MASON_PACKAGE=%~2\"\r\n"
            f"\"{nvim_arg}\" --headless -u NONE -n --cmd \"set rtp^={plugin_arg}\" -c \"luafile {script_arg}\"\r\n"
            "exit /b %ERRORLEVEL%\r\n",
            encoding="utf-8",
        )
    else:
        bin_path.write_text(
            "#!/usr/bin/env sh\n"
            f"STRATUM_MASON_ROOT={shlex.quote(root)} "
            "STRATUM_MASON_ACTION=\"$1\" STRATUM_MASON_PACKAGE=\"$2\" "
            f"{shlex.quote(nvim)} --headless -u NONE -n --cmd {shlex.quote('set rtp^=' + plugin_arg)} "
            f"-c {shlex.quote('luafile ' + script_arg)}\n",
            encoding="utf-8",
        )
        bin_path.chmod(0o755)


def _mason_lua() -> str:
    return r'''
local action = vim.env.STRATUM_MASON_ACTION
local name = vim.env.STRATUM_MASON_PACKAGE
local root = vim.env.STRATUM_MASON_ROOT

local function finish(ok, msg)
  if msg and msg ~= "" then print(msg) end
  vim.defer_fn(function()
    if ok then vim.cmd("qa") else vim.cmd("cquit") end
  end, 50)
end

if (action ~= "install" and action ~= "uninstall") or not name or name == "" then
  finish(false, "usage: mason install|uninstall <package>")
  return
end

require("mason").setup({ install_root_dir = root, PATH = "skip" })
local registry = require("mason-registry")
registry.refresh(function()
  local ok, pkg = pcall(registry.get_package, name)
  if not ok then finish(false, tostring(pkg)); return end
  if action == "uninstall" then
    if pkg:is_installed() then pkg:uninstall() end
    finish(true, "uninstalled")
    return
  end
  if pkg:is_installed() then finish(true, "already installed"); return end
  pkg:install():once("closed", function()
    finish(pkg:is_installed(), pkg:is_installed() and "installed" or "install failed")
  end)
end)
vim.defer_fn(function() finish(false, "mason timed out") end, 120000)
'''


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


def _fallback_packages() -> list[dict]:
    return [{
        "name": name,
        "display_name": info.get("display_name", name),
        "description": f"Built-in installer via npx: {', '.join(info['packages'])}",
        "languages": info.get("languages", []),
        "homepage": "",
    } for name, info in _NPM_LSP.items()]


def _seed_catalog() -> None:
    _ensure_table()
    use_mason = bool(_mason_command())
    if use_mason:
        _sync_registry()
    packages = _parse_packages() if use_mason else _fallback_packages()
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
        if use_mason:
            for name in set(existing) - seen:
                db.execute("DELETE FROM lsp_servers WHERE name = ?", (name,))


def list_all(language: str | None = None, query: str | None = None) -> list[dict]:
    _seed_catalog()
    allowed = None if _mason_command() else set(_NPM_LSP)
    with db_session() as db:
        rows = db.execute("SELECT * FROM lsp_servers ORDER BY display_name").fetchall()
    servers = [_row_to_server(row) for row in rows]
    result = []
    for server in servers:
        if allowed is not None and server["name"] not in allowed:
            continue
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
    allowed = None if _mason_command() else set(_NPM_LSP)
    with db_session() as db:
        rows = db.execute("SELECT name, languages_json FROM lsp_servers").fetchall()
    langs = set()
    for row in rows:
        if allowed is not None and row["name"] not in allowed:
            continue
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
    mason = _mason_command()
    if not mason:
        return _install_npm_fallback(name)
    if Path(mason) == _mason_bin():
        _write_mason_shim(LSP_ROOT / "mason.nvim")
    with db_session() as db:
        try:
            result = subprocess.run(
                [mason, "install", name],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                server["installed"] = True
                server["enabled"] = True
                server["install_version"] = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "installed"
                server["executable"] = _mason_package_executable(name)
                server["args"] = _NPM_LSP.get(name, {}).get("args", [])
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
                executable = ?, args_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE name = ?
            """,
            (
                int(server["installed"]),
                int(server["enabled"]),
                server["install_version"],
                server["executable"],
                json.dumps(server["args"], ensure_ascii=False),
                name,
            ),
        )
    return get(name)


def _install_npm_fallback(name: str) -> dict:
    info = _NPM_LSP.get(name)
    if not info:
        known = ", ".join(sorted(_NPM_LSP))
        raise ValueError(f"mason CLI not found and no built-in installer is known for {name}. Known installers: {known}")
    npx = _npx_command()
    if not npx:
        raise ValueError("mason CLI not found and npx is not available")
    args = ["--yes"]
    for package in info["packages"]:
        args.extend(["--package", package])
    args.append(info["command"])
    args.extend(info["args"])
    server = configure(name, npx, args, True)
    with db_session() as db:
        db.execute(
            "UPDATE lsp_servers SET install_version = ?, install_error = '', updated_at = CURRENT_TIMESTAMP WHERE name = ?",
            ("npx " + ", ".join(info["packages"]), name),
        )
    return get(name)


def _npx_command() -> str:
    candidates = ["npx.cmd", "npx"] if os.name == "nt" else ["npx", "npx.cmd"]
    return next((command for command in candidates if _command_available(command)), "")


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
            mason = _mason_command()
            if mason:
                if Path(mason) == _mason_bin():
                    _write_mason_shim(LSP_ROOT / "mason.nvim")
                subprocess.run(
                    [mason, "uninstall", name],
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
                executable = '', args_json = '[]',
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


def configure(name: str, executable_or_env, args: list[str] | None = None, enabled: bool = True) -> dict:
    _ensure_table()
    executable = ""
    env = {}
    if isinstance(executable_or_env, dict):
        env = executable_or_env
    else:
        executable = str(executable_or_env or "").strip()
    with db_session() as db:
        row = db.execute("SELECT * FROM lsp_servers WHERE name = ?", (name,)).fetchone()
        if row is None and executable:
            db.execute(
                """
                INSERT INTO lsp_servers
                    (name, display_name, installed, enabled, executable, args_json)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (name, name, int(enabled), executable, json.dumps(args or [], ensure_ascii=False)),
            )
            row = db.execute("SELECT * FROM lsp_servers WHERE name = ?", (name,)).fetchone()
        if row is None:
            _seed_catalog()
            row = db.execute("SELECT * FROM lsp_servers WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"lsp server not found: {name}")
        current_env = _row_to_server(row).get("env", {})
        current_env.update(env)
        if executable:
            db.execute(
                """
                UPDATE lsp_servers
                SET installed = 1, enabled = ?, executable = ?, args_json = ?,
                    env_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE name = ?
                """,
                (
                    int(enabled),
                    executable,
                    json.dumps(args or [], ensure_ascii=False),
                    json.dumps(current_env, ensure_ascii=False),
                    name,
                ),
            )
        else:
            db.execute(
                "UPDATE lsp_servers SET env_json = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (json.dumps(current_env, ensure_ascii=False), name),
            )
    return _get_local(name)


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
    return _get_local(name)


def _get_local(name: str) -> dict:
    _ensure_table()
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


def query(params: dict, workspace_dir: str) -> dict:
    operation = str(params.get("operation") or "").strip()
    if operation not in {"document_symbols", "definition", "references", "hover"}:
        raise ValueError(f"unsupported lsp operation: {operation}")
    path = _workspace_path(str(params.get("path") or ""), workspace_dir)
    server = _server_for_query(params, path)
    init_options = _initialization_options(server, params)
    timeout = float(params.get("timeout") or 30)
    root = _server_root(path, workspace_dir)
    client = _client(server, str(root), init_options, timeout)
    try:
        client.open_document(path)
        if operation == "document_symbols":
            result = client.request("textDocument/documentSymbol", {
                "textDocument": {"uri": _path_uri(path)},
            })
        else:
            method = {
                "definition": "textDocument/definition",
                "references": "textDocument/references",
                "hover": "textDocument/hover",
            }[operation]
            result = None
            used_position = None
            candidates = _position_candidates(
                path,
                max(0, int(params.get("line") or 1) - 1),
                max(0, int(params.get("character") or params.get("column") or 1) - 1),
            )
            for position in candidates:
                payload = {"textDocument": {"uri": _path_uri(path)}, "position": position}
                if operation == "references":
                    payload["context"] = {"includeDeclaration": bool(params.get("include_declaration", True))}
                result = client.request(method, payload)
                used_position = position
                if _has_lsp_result(result):
                    break
    except Exception:
        _drop_client(server["name"], str(root))
        raise
    response = {"server": server["name"], "operation": operation, "path": str(path), "result": result}
    if operation != "document_symbols" and used_position is not None:
        response["position"] = {
            "line": used_position["line"] + 1,
            "character": used_position["character"] + 1,
        }
    return response


def touch_file(path: str, workspace_dir: str, timeout: float = 3) -> dict:
    try:
        target = _workspace_path(path, workspace_dir)
        server = _server_for_query({}, target)
        root = str(_server_root(target, workspace_dir))
        client = _client(server, root, _initialization_options(server, {}), timeout)
    except Exception as exc:
        return {"checked": False, "server": "", "error": str(exc)}
    old_timeout = client.timeout
    client.timeout = timeout
    try:
        client.open_document(target)
        client.request("textDocument/documentSymbol", {"textDocument": {"uri": _path_uri(target)}})
        return {"checked": True, "server": server["name"], "error": ""}
    except Exception as exc:
        _drop_client(server["name"], root)
        return {"checked": False, "server": server["name"], "error": str(exc)}
    finally:
        client.timeout = old_timeout


def diagnostics_for(path: str | Path) -> list[dict]:
    uri = _path_uri(Path(path))
    with _diagnostics_lock:
        return list(_diagnostics.get(uri, []))


def _has_lsp_result(result) -> bool:
    if not result:
        return False
    if isinstance(result, dict) and "contents" in result:
        return bool(result.get("contents"))
    return True


def _server_root(path: Path, workspace_dir: str) -> Path:
    root = Path(workspace_dir or ".").resolve()
    suffix = path.suffix.lower()
    include = ["package-lock.json", "bun.lockb", "bun.lock", "pnpm-lock.yaml", "yarn.lock"]
    exclude: list[str] = []
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"}:
        exclude = ["deno.json", "deno.jsonc"]
    current = path.parent
    while root in (current, *current.parents):
        if any((current / item).exists() for item in exclude):
            return root
        if any((current / item).exists() for item in include):
            return current
        if current == root:
            break
        current = current.parent
    return root


def _position_candidates(path: Path, line: int, character: int) -> list[dict]:
    candidates = [{"line": line, "character": character}]
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return candidates
    if line >= len(lines):
        return candidates
    text = lines[line]
    spans = list(re.finditer(r"[A-Za-z_$][A-Za-z0-9_$]*", text))
    ranked = sorted(
        spans,
        key=lambda match: 0 if match.start() <= character <= match.end() else min(
            abs(character - match.start()),
            abs(character - max(match.end() - 1, match.start())),
        ),
    )
    seen = {(line, character)}
    for match in ranked[:6]:
        probe = {"line": line, "character": match.start() + min(1, max(0, len(match.group(0)) - 1))}
        key = (probe["line"], probe["character"])
        if key not in seen:
            candidates.append(probe)
            seen.add(key)
    return candidates


def close_server(server_id: str, workspace_dir: str | None = None) -> None:
    with _clients_lock:
        keys = [
            key for key in _clients
            if key[0] == server_id and (workspace_dir is None or key[1] == str(Path(workspace_dir).resolve()))
        ]
        for key in keys:
            _clients.pop(key).close()


def _drop_client(server_id: str, workspace_dir: str) -> None:
    root = str(Path(workspace_dir or ".").resolve())
    with _clients_lock:
        client = _clients.pop((server_id, root), None)
    if client:
        client.kill()


def _workspace_path(path: str, workspace_dir: str) -> Path:
    root = Path(workspace_dir or ".").resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"path escapes the workspace: {path}")
    if not target.is_file():
        raise ValueError(f"not a file: {path}")
    return target


def _server_for_query(params: dict, path: Path) -> dict:
    requested = str(params.get("server_id") or params.get("server") or "").strip()
    if requested:
        server = _resolve_local_server(requested, path)
    else:
        candidates = [item for item in _local_servers() if item["enabled"] and item["available"]]
        server = _best_server(candidates, "", path)
        if server is None:
            raise ValueError(f"no enabled available LSP server matches {path.suffix or path.name}")
    if not server["enabled"]:
        raise ValueError(f"lsp server is disabled: {server['name']}")
    if not server["available"]:
        raise ValueError(f"lsp server executable is unavailable: {server['name']}")
    return server


def _language_alias(suffix: str) -> str:
    return {
        "c": "c",
        "cc": "cpp",
        "cpp": "cpp",
        "cs": "csharp",
        "css": "css",
        "go": "go",
        "h": "c",
        "hpp": "cpp",
        "html": "html",
        "java": "java",
        "js": "javascript",
        "jsx": "javascriptreact",
        "json": "json",
        "kt": "kotlin",
        "kts": "kotlin",
        "lua": "lua",
        "php": "php",
        "py": "python",
        "rs": "rust",
        "scss": "scss",
        "sh": "shell",
        "svelte": "svelte",
        "swift": "swift",
        "ts": "typescript",
        "tsx": "typescriptreact",
        "vue": "vue",
    }.get(suffix, suffix)


def _local_servers() -> list[dict]:
    _ensure_table()
    with db_session() as db:
        rows = db.execute("SELECT * FROM lsp_servers ORDER BY display_name").fetchall()
    return [_row_to_server(row) for row in rows]


def _resolve_local_server(requested: str, path: Path) -> dict:
    servers = _local_servers()
    exact = next((server for server in servers if server["name"] == requested), None)
    if exact:
        return exact
    candidates = [server for server in servers if server["enabled"] and server["available"]]
    server = _best_server(candidates, requested, path)
    if server:
        return server
    available = ", ".join(server["name"] for server in candidates) or "none"
    raise ValueError(f"lsp server not found: {requested}. Available enabled servers: {available}")


def _best_server(candidates: list[dict], requested: str, path: Path) -> dict | None:
    if not candidates:
        return None
    requested = _language_alias(requested.casefold())
    suffix = path.suffix.lower().lstrip(".")
    file_language = _language_alias(suffix)
    for server in candidates:
        if requested and _server_supports(server, requested):
            return server
    for server in candidates:
        if file_language and _server_supports(server, file_language):
            return server
    return candidates[0] if requested else None


def _server_supports(server: dict, language: str) -> bool:
    haystack = {
        str(server.get("name") or "").casefold(),
        str(server.get("display_name") or "").casefold(),
        *_loads(json.dumps(server.get("languages") or []), []),
    }
    if language in haystack:
        return True
    if language in {"javascript", "javascriptreact"} and any("vue" in item for item in haystack):
        return True
    if language in {"typescript", "typescriptreact"} and any("vue" in item for item in haystack):
        return True
    if language in {"javascript", "javascriptreact"} and any("typescript" in item for item in haystack):
        return True
    if language in {"typescript", "typescriptreact"} and any("typescript" in item for item in haystack):
        return True
    return any(language and language in item for item in haystack)


def _initialization_options(server: dict, params: dict) -> dict:
    options = params.get("initialization_options") if isinstance(params.get("initialization_options"), dict) else {}
    if _server_supports(server, "vue"):
        return _deep_merge({"vue": {"hybridMode": False}}, options)
    return dict(options)


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _client(
    server: dict,
    workspace_dir: str,
    initialization_options: dict | None = None,
    timeout: float = 30,
) -> "_LspClient":
    root = str(Path(workspace_dir or ".").resolve())
    key = (server["name"], root)
    with _clients_lock:
        client = _clients.get(key)
        if client and client.alive:
            return client
        client = _LspClient(server, Path(root), initialization_options or {}, timeout)
        try:
            client.start()
        except Exception:
            client.kill()
            raise
        _clients[key] = client
        return client


def _path_uri(path: Path) -> str:
    return "file:///" + quote(str(path.resolve()).replace("\\", "/"), safe="/:")


class _LspClient:
    def __init__(self, server: dict, root: Path, initialization_options: dict, timeout: float):
        self.server = server
        self.root = root
        self.initialization_options = initialization_options
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None
        self.next_id = 1
        self.opened: set[str] = set()
        self.pending: dict[int, queue.Queue] = {}
        self.request_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.reader_thread: threading.Thread | None = None
        self.reader_error: Exception | None = None

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        command = [self.server["executable"], *self.server.get("args", [])]
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in self.server.get("env", {}).items()})
        self.proc = subprocess.Popen(
            command,
            cwd=str(self.root),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()
        self.request("initialize", {
            "processId": os.getpid(),
            "rootUri": _path_uri(self.root),
            "capabilities": {},
            "initializationOptions": self.initialization_options,
        })
        self.notify("initialized", {})

    def request(self, method: str, params: dict):
        with self.request_lock:
            message_id = self.next_id
            self.next_id += 1
        result: queue.Queue = queue.Queue(maxsize=1)
        self.pending[message_id] = result
        self._send({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
        try:
            try:
                message, error = result.get(timeout=self.timeout)
            except queue.Empty as exc:
                self.kill()
                raise TimeoutError(f"lsp request timed out after {self.timeout:g}s") from exc
            if error:
                raise error
            if message.get("error"):
                raise ValueError(json.dumps(message["error"], ensure_ascii=False))
            return message.get("result")
        finally:
            self.pending.pop(message_id, None)

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _handle_server_message(self, message: dict) -> None:
        message_id = message.get("id")
        if message_id in self.pending and "method" not in message:
            self.pending[message_id].put((message, None))
            return
        if message.get("method") == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            uri = params.get("uri")
            if uri:
                with _diagnostics_lock:
                    _diagnostics[uri] = params.get("diagnostics") or []
            return
        if message.get("method") != "tsserver/request":
            if "id" in message and message.get("method"):
                self._respond_to_server_request(message)
            return
        params = message.get("params") or []
        if params:
            payload = params[0] if isinstance(params[0], list) else params
            if payload:
                self.notify("tsserver/response", [[payload[0], None]])

    def _respond_to_server_request(self, message: dict) -> None:
        method = message.get("method")
        if method == "workspace/configuration":
            result = []
        elif method == "workspace/workspaceFolders":
            result = [{"uri": _path_uri(self.root), "name": self.root.name}]
        else:
            result = None
        self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})

    def open_document(self, path: Path) -> None:
        uri = _path_uri(path)
        if uri in self.opened:
            return
        self.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": _language_alias(path.suffix.lower().lstrip(".")),
                "version": 1,
                "text": path.read_text(encoding="utf-8", errors="replace"),
            }
        })
        self.opened.add(uri)

    def close(self) -> None:
        if not self.alive:
            return
        old_timeout = self.timeout
        self.timeout = min(self.timeout, 1)
        try:
            self.request("shutdown", {})
            self.notify("exit", {})
        except Exception:
            pass
        finally:
            self.timeout = old_timeout
        try:
            self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self._terminate_tree()
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream:
                stream.close()

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self._terminate_tree()
        if self.proc:
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                if stream:
                    stream.close()

    def _terminate_tree(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
            )
        else:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _send(self, payload: dict) -> None:
        if not self.proc or not self.proc.stdin:
            raise ValueError("lsp server is not running")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        with self.write_lock:
            self.proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
            self.proc.stdin.flush()

    def _reader_loop(self) -> None:
        try:
            while self.alive:
                self._handle_server_message(self._read_message())
        except Exception as exc:
            self.reader_error = exc
            for pending in list(self.pending.values()):
                pending.put((None, exc))

    def _read_message(self) -> dict:
        if not self.proc or not self.proc.stdout:
            raise ValueError("lsp server is not running")
        headers = {}
        while True:
            line = self.proc.stdout.readline()
            if not line:
                detail = ""
                if self.proc.stderr:
                    detail = self.proc.stderr.read().decode("utf-8", errors="replace").strip()
                raise ValueError(f"lsp server stopped{': ' + detail if detail else ''}")
            if line in (b"\r\n", b"\n"):
                break
            key, _, value = line.decode("ascii", errors="replace").partition(":")
            headers[key.lower()] = value.strip()
        return json.loads(self.proc.stdout.read(int(headers["content-length"])))
