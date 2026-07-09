"""MCP client: persist servers, discover tools, and expose them as local tools."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from ..db import db_session
from ..tools import registry
from ..tools.spec import ToolDef, ToolResult

PROTOCOL_VERSION = "2025-06-18"
REQUEST_TIMEOUT = 30
CODEGRAPH_MCP_TOOLS = "context,explore,node,search,callers,callees,impact,files,status,trace"
_stdio_clients: dict[int, "_StdioClient"] = {}
_stdio_clients_lock = threading.RLock()


def _ensure_table() -> None:
    with db_session() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS mcp_servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                transport TEXT NOT NULL DEFAULT 'http',
                url TEXT NOT NULL DEFAULT '',
                command TEXT NOT NULL DEFAULT '',
                args_json TEXT NOT NULL DEFAULT '[]',
                cwd TEXT NOT NULL DEFAULT '',
                env_json TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'stopped',
                status_message TEXT NOT NULL DEFAULT '',
                tools_json TEXT NOT NULL DEFAULT '[]',
                requirements_json TEXT NOT NULL DEFAULT '[]',
                source_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(mcp_servers)").fetchall()}
        for name, ddl in {
            "requirements_json": "ALTER TABLE mcp_servers ADD COLUMN requirements_json TEXT NOT NULL DEFAULT '[]'",
            "source_text": "ALTER TABLE mcp_servers ADD COLUMN source_text TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in columns:
                db.execute(ddl)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value or "").strip("_").lower()
    return cleaned or "server"


def _loads(value: str, fallback):
    try:
        parsed = json.loads(value or "")
    except json.JSONDecodeError:
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _row_to_server(row) -> dict:
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    data["args"] = _loads(data.pop("args_json"), [])
    data["env"] = _loads(data.pop("env_json"), {})
    data["tools"] = _loads(data.pop("tools_json"), [])
    data["requirements"] = _loads(data.pop("requirements_json"), [])
    data["env_keys"] = sorted(data["env"])
    data.pop("source_text", None)
    return data


def _raw_server(server_id: int) -> dict:
    _ensure_table()
    with db_session() as db:
        row = db.execute("SELECT * FROM mcp_servers WHERE id = ?", (int(server_id),)).fetchone()
    if row is None:
        raise ValueError("mcp server not found")
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    data["args"] = _loads(data.pop("args_json"), [])
    data["env"] = _loads(data.pop("env_json"), {})
    data["tools"] = _loads(data.pop("tools_json"), [])
    data["requirements"] = _loads(data.pop("requirements_json"), [])
    return data


def list_all() -> list[dict]:
    _ensure_table()
    with db_session() as db:
        rows = db.execute("SELECT * FROM mcp_servers ORDER BY name").fetchall()
    return [_row_to_server(row) for row in rows]


def _requirements(env: dict[str, str]) -> list[dict]:
    found = []
    for key, value in sorted((env or {}).items()):
        needs_value = not value or "YOUR_" in value.upper() or value.startswith("${")
        if needs_value or key.upper().endswith(("API_KEY", "TOKEN")):
            found.append({"key": key, "configured": bool(value and not needs_value)})
    return found


def save_server(config: dict) -> int:
    _ensure_table()
    name = (config.get("name") or "mcp").strip()
    transport = (config.get("transport") or config.get("type") or "http").strip().lower()
    if transport in {"remote", "streamable-http", "url"}:
        transport = "http"
    if transport not in {"http", "stdio"}:
        raise ValueError("mcp transport must be http or stdio")
    url = (config.get("url") or config.get("serverUrl") or config.get("httpUrl") or "").strip()
    command = config.get("command") or ""
    args = config.get("args") or []
    env = config.get("env") or config.get("environment") or {}
    if isinstance(command, list):
        command, args = command[0], command[1:]
    if transport == "http" and not url:
        raise ValueError("http mcp server requires url")
    if transport == "stdio" and not command:
        raise ValueError("stdio mcp server requires command")
    if not isinstance(args, list):
        raise ValueError("mcp args must be an array")
    if not isinstance(env, dict):
        raise ValueError("mcp env must be an object")
    requirements = _requirements(env)

    with db_session() as db:
        existing = db.execute("SELECT id FROM mcp_servers WHERE name = ?", (name,)).fetchone()
        if existing:
            _close_stdio_client(int(existing["id"]))
            db.execute(
                """
                UPDATE mcp_servers
                SET transport = ?, url = ?, command = ?, args_json = ?, cwd = ?,
                    env_json = ?, enabled = ?, status = 'stopped',
                    status_message = '', tools_json = '[]', requirements_json = ?,
                    source_text = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    transport,
                    url,
                    command,
                    json.dumps(args, ensure_ascii=False),
                    config.get("cwd") or "",
                    json.dumps(env, ensure_ascii=False),
                    1 if config.get("enabled", True) else 0,
                    json.dumps(requirements, ensure_ascii=False),
                    config.get("source_text") or "",
                    int(existing["id"]),
                ),
            )
            return int(existing["id"])
        cursor = db.execute(
            """
            INSERT INTO mcp_servers
                (name, transport, url, command, args_json, cwd, env_json, enabled,
                 requirements_json, source_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                transport,
                url,
                command,
                json.dumps(args, ensure_ascii=False),
                config.get("cwd") or "",
                json.dumps(env, ensure_ascii=False),
                1 if config.get("enabled", True) else 0,
                json.dumps(requirements, ensure_ascii=False),
                config.get("source_text") or "",
            ),
        )
        return int(cursor.lastrowid)


def configure(server_id: int, env: dict[str, str]) -> dict:
    server = _raw_server(server_id)
    merged = {**server["env"], **(env or {})}
    requirements = _requirements(merged)
    with db_session() as db:
        db.execute(
            """
            UPDATE mcp_servers
            SET env_json = ?, requirements_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                json.dumps(merged, ensure_ascii=False),
                json.dumps(requirements, ensure_ascii=False),
                int(server_id),
            ),
        )
    return start_server(server_id)


def delete_server(server_id: int) -> None:
    server = _raw_server(server_id)
    _close_stdio_client(int(server_id))
    registry.unregister_prefix(f"mcp_{_slug(server['name'])}_")
    with db_session() as db:
        db.execute("DELETE FROM mcp_servers WHERE id = ?", (int(server_id),))


def _set_status(server_id: int, status: str, message: str = "", tools: list[dict] | None = None) -> None:
    with db_session() as db:
        db.execute(
            """
            UPDATE mcp_servers
            SET status = ?, status_message = ?, tools_json = COALESCE(?, tools_json),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                status,
                message[:1200],
                json.dumps(tools, ensure_ascii=False) if tools is not None else None,
                int(server_id),
            ),
        )


def start_server(server_id: int) -> dict:
    server = _raw_server(server_id)
    prefix = f"mcp_{_slug(server['name'])}_"
    registry.unregister_prefix(prefix)
    _close_stdio_client(int(server_id))
    if not server["enabled"]:
        _set_status(server_id, "disabled", "server is disabled", [])
        return _raw_server(server_id)
    try:
        tools = _client(server).list_tools()
        _register_tools(server, tools)
        _set_status(server_id, "running", f"{len(tools)} tools ready", tools)
    except Exception as exc:
        _close_stdio_client(int(server_id))
        _set_status(server_id, "error", str(exc), [])
    return _raw_server(server_id)


def load_enabled() -> None:
    for server in list_all():
        if server["enabled"]:
            start_server(server["id"])


def _register_tools(server: dict, tools: list[dict]) -> None:
    prefix = f"mcp_{_slug(server['name'])}_"
    for remote_tool in tools:
        remote_name = remote_tool.get("name") or "tool"
        local_name = prefix + _slug(remote_name)
        schema = remote_tool.get("inputSchema") or {"type": "object", "properties": {}}
        description = remote_tool.get("description") or remote_tool.get("title") or remote_name

        async def execute(params: dict, ctx: dict, sid=server["id"], tool_name=remote_name) -> ToolResult:
            current = _raw_server(sid)
            result = _client(current).call_tool(tool_name, params or {})
            output = _tool_result_text(result)
            title = f"{current['name']}:{tool_name}"
            if result.get("isError"):
                return ToolResult.err(title, output)
            return ToolResult.ok(title, output, server=current["name"], tool=tool_name)

        registry.register(
            ToolDef(
                name=local_name,
                description=f"MCP {server['name']} tool: {description}",
                parameters=schema,
                execute=execute,
            ),
            replace=True,
        )


def _tool_result_text(result: dict) -> str:
    chunks = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            chunks.append(str(item.get("text", "")))
        else:
            chunks.append(json.dumps(item, ensure_ascii=False))
    if result.get("structuredContent") is not None:
        chunks.append(json.dumps(result["structuredContent"], ensure_ascii=False, indent=2))
    return "\n".join(part for part in chunks if part).strip() or json.dumps(result, ensure_ascii=False)


def search(query: str, limit: int = 5) -> ToolResult | None:
    candidates = [
        tool for tool in registry.list_all()
        if tool.name.startswith("mcp_") and "search" in tool.name
    ]
    for tool in candidates:
        schema_props = tool.parameters.get("properties", {}) if isinstance(tool.parameters, dict) else {}
        params = {"query": query}
        for name in ("numResults", "num_results", "limit", "maxResults"):
            if name in schema_props:
                params[name] = limit
        try:
            import asyncio

            result = asyncio.run(tool.execute(params, {"directory": "."}))
        except Exception:
            continue
        if not result.title.startswith("[error]") and result.output.strip():
            return ToolResult.ok(
                f"mcp fallback {tool.name}",
                result.output,
                source_tool=tool.name,
                query=query,
                count=limit,
            )
    return None


def install_from_hint(hint: str, page_text: str = "") -> dict:
    config = discover_config(hint, page_text)
    config["source_text"] = hint
    server_id = save_server(config)
    return start_server(server_id)


def discover_config(hint: str, page_text: str = "") -> dict:
    text = "\n".join(part for part in (hint, page_text) if part)
    parsed = _config_from_json(text)
    if parsed:
        return parsed

    if "colbymchenry/codegraph" in text or "@colbymchenry/codegraph" in text:
        return {
            "name": "codegraph",
            "transport": "stdio",
            "command": "codegraph",
            "args": ["serve", "--mcp"],
            "env": {
                "CODEGRAPH_MCP_TOOLS": CODEGRAPH_MCP_TOOLS,
            },
            "enabled": True,
        }

    if "mcp.exa.ai/mcp" in text or "exa.ai/mcp" in text:
        return {"name": "exa", "transport": "http", "url": "https://mcp.exa.ai/mcp", "enabled": True}

    urls = [
        url.rstrip("`'\" )]")
        for url in re.findall(r"https?://[^\s<>'\")]+", text)
        if "/mcp" in urlsplit(url).path or "mcp" in urlsplit(url).netloc
    ]
    if urls:
        host = urlsplit(urls[0]).netloc.split(":")[0].split(".")
        name = host[-2] if len(host) > 1 else host[0]
        return {"name": _slug(name), "transport": "http", "url": urls[0], "enabled": True}

    command = _command_from_text(text)
    if command:
        return command
    raise ValueError("could not infer an MCP server config from the supplied information")


def _config_from_json(text: str) -> dict | None:
    candidates = []
    for match in re.finditer(r"```(?:json|jsonc)?\s*(\{[\s\S]*?\})\s*```", text):
        candidates.append(match.group(1))
    if "{" in text and "}" in text:
        candidates.append(text[text.find("{"): text.rfind("}") + 1])
    for candidate in candidates:
        try:
            data = json.loads(_strip_json_comments(candidate))
        except json.JSONDecodeError:
            continue
        for key in ("mcpServers", "mcp", "servers", "context_servers"):
            servers = data.get(key)
            if isinstance(servers, dict) and servers:
                name, cfg = next(iter(servers.items()))
                if isinstance(cfg, dict):
                    return _normalize_config(name, cfg)
    return None


def _strip_json_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        lines.append(re.sub(r"(?<!:)//.*$", "", line))
    return "\n".join(lines)


def _normalize_config(name: str, cfg: dict) -> dict:
    url = cfg.get("url") or cfg.get("serverUrl") or cfg.get("httpUrl")
    command = cfg.get("command")
    if url:
        return {"name": name, "transport": "http", "url": url, "enabled": cfg.get("enabled", True)}
    if command:
        args = cfg.get("args") or []
        if isinstance(command, list):
            command, args = command[0], command[1:]
        return {
            "name": name,
            "transport": "stdio",
            "command": command,
            "args": args,
            "cwd": cfg.get("cwd") or "",
            "env": cfg.get("env") or cfg.get("environment") or {},
            "enabled": cfg.get("enabled", True),
        }
    return {"name": name, **cfg}


def _command_from_text(text: str) -> dict | None:
    match = re.search(r"(?:npx|bun|node)\s+[^\n`]+", text)
    if not match:
        return None
    parts = match.group(0).split()
    env = {}
    for key in sorted(set(re.findall(r"\b[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN)\b", text))):
        env[key] = ""
    return {
        "name": _slug(parts[-1].split("/")[-1].replace("@", "")),
        "transport": "stdio",
        "command": parts[0],
        "args": parts[1:],
        "env": env,
        "enabled": True,
    }


def _client(server: dict):
    if server["transport"] == "http":
        return _HttpClient(server)
    server_id = int(server["id"])
    with _stdio_clients_lock:
        client = _stdio_clients.get(server_id)
        if client is None:
            client = _StdioClient(server)
            _stdio_clients[server_id] = client
        return client


def _close_stdio_client(server_id: int) -> None:
    with _stdio_clients_lock:
        client = _stdio_clients.pop(int(server_id), None)
    if client:
        client.close()


@dataclass
class _HttpClient:
    server: dict
    session_id: str = ""
    _next_id: int = 1

    def list_tools(self) -> list[dict]:
        self.initialize()
        tools: list[dict] = []
        cursor = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params)
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.initialize()
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def initialize(self) -> None:
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "StratumCode", "version": "0.1.0"},
        })
        if not result.get("protocolVersion"):
            raise ValueError("MCP initialize returned no protocol version")
        self.notify("notifications/initialized", {})

    def request(self, method: str, params: dict) -> dict:
        request_id = self._next_id
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        response = self._post(message)
        if "error" in response:
            raise ValueError(response["error"].get("message") or json.dumps(response["error"]))
        if response.get("id") != request_id:
            raise ValueError(f"MCP response id mismatch for {method}")
        return response.get("result") or {}

    def notify(self, method: str, params: dict) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params})

    def _post(self, message: dict) -> dict:
        url = _expand_env(self.server["url"], self.server["env"])
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "User-Agent": "StratumCode/0.1",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = Request(url, data=json.dumps(message).encode(), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                if response.headers.get("Mcp-Session-Id"):
                    self.session_id = response.headers["Mcp-Session-Id"]
                content_type = response.headers.get("Content-Type", "")
                if response.status == 202:
                    return {}
                if "text/event-stream" in content_type:
                    return _read_sse_response(response, message.get("id"))
                body = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise ValueError(f"HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ValueError(str(exc)) from exc
        return json.loads(body) if body.strip() else {}


def _expand_env(value: str, env: dict[str, str]) -> str:
    def replace(match):
        key = match.group(1)
        resolved = (env or {}).get(key) or os.environ.get(key)
        if not resolved:
            raise ValueError(f"missing MCP environment value: {key}")
        return resolved

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)


def _resolve_command(command: str, env: dict[str, str]) -> str:
    if not command:
        return command
    if Path(command).name != command:
        return command
    return shutil.which(command, path=env.get("PATH")) or command


def _parse_sse_response(body: str, request_id: int | None) -> dict:
    event_lines: list[str] = []
    for line in body.splitlines() + [""]:
        if line.startswith("data:"):
            event_lines.append(line[5:].strip())
        elif not line.strip() and event_lines:
            payload = "\n".join(event_lines)
            event_lines = []
            try:
                message = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if request_id is None or message.get("id") == request_id:
                return message
    raise ValueError("SSE stream ended without a matching MCP response")


def _read_sse_response(response, request_id: int | None) -> dict:
    event_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if line.startswith("data:"):
            event_lines.append(line[5:].strip())
        elif not line.strip() and event_lines:
            payload = "\n".join(event_lines)
            event_lines = []
            try:
                message = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if request_id is None or message.get("id") == request_id:
                return message
    raise ValueError("SSE stream ended without a matching MCP response")


class _StdioClient:
    def __init__(self, server: dict):
        self.server = server
        self.process: subprocess.Popen | None = None
        self.messages: queue.Queue[dict] = queue.Queue()
        self.next_id = 1
        self.initialized = False
        self.lock = threading.RLock()

    def list_tools(self) -> list[dict]:
        self.initialize()
        result = self.request("tools/list", {})
        return result.get("tools") or []

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.initialize()
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        self.messages = queue.Queue()
        env = {**os.environ, **{k: v for k, v in self.server["env"].items() if v}}
        cwd = self.server.get("cwd") or None
        if cwd:
            cwd = str(Path(cwd).expanduser().resolve())
        command = _resolve_command(self.server["command"], env)
        self.process = subprocess.Popen(
            [command, *self.server["args"]],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        threading.Thread(target=self._reader, daemon=True).start()

    def close(self) -> None:
        self.initialized = False
        if not self.process:
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=2)
        except Exception:
            self.process.kill()
        finally:
            self.process = None

    def _reader(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    def initialize(self) -> None:
        with self.lock:
            if self.initialized and self.process and self.process.poll() is None:
                return
            self.start()
            self.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "StratumCode", "version": "0.1.0"},
            })
            self.notify("notifications/initialized", {})
            self.initialized = True

    def request(self, method: str, params: dict) -> dict:
        with self.lock:
            request_id = self.next_id
            self.next_id += 1
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            deadline = time.time() + REQUEST_TIMEOUT
            while time.time() < deadline:
                try:
                    message = self.messages.get(timeout=0.2)
                except queue.Empty:
                    continue
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise ValueError(message["error"].get("message") or json.dumps(message["error"]))
                return message.get("result") or {}
            raise TimeoutError(f"MCP stdio request timed out: {method}")

    def notify(self, method: str, params: dict) -> None:
        with self.lock:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, message: dict) -> None:
        if not self.process or not self.process.stdin:
            raise ValueError("MCP process is not running")
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()


def load_from_config(mcp_configs: list[dict]) -> None:
    for cfg in mcp_configs:
        server_id = save_server(cfg)
        start_server(server_id)


def _register_mcp_stub(name: str) -> None:
    async def ping(params: dict, ctx: dict) -> ToolResult:
        return ToolResult.ok("mcp-ping", f"MCP server '{name}' stub")

    registry.register(
        ToolDef(
            name=f"mcp_{name}_ping",
            description=f"MCP stub server '{name}' ping tool.",
            parameters={"type": "object", "properties": {}},
            execute=ping,
        ),
        replace=True,
    )
