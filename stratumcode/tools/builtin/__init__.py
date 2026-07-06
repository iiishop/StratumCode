"""Builtin read-only tools exposed by the local tool registry."""

from __future__ import annotations

import ipaddress
import socket
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from ..spec import ToolDef, ToolResult


_WEBFETCH_LIMIT = 1_000_000


# ── helpers ────────────────────────────────────────────────────

def _resolve(path: str, ctx: dict) -> Path:
    d = Path(ctx.get("directory", ".")).resolve()
    p = (d / path).resolve()
    if not p.is_relative_to(d):
        raise PermissionError(f"path escapes worktree: {path}")
    return p


# ── read ───────────────────────────────────────────────────────

async def _read(params: dict, ctx: dict) -> ToolResult:
    p = _resolve(params["path"], ctx)
    if not p.is_file():
        return ToolResult.err("read", f"not a file: {params['path']}")
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = max(0, params.get("start_line", 1) - 1)
    end = params.get("end_line") or len(lines)
    selection = lines[start:end]
    return ToolResult.ok(
        f"read {params['path']} L{start+1}-{min(end, len(lines))}",
        "\n".join(selection),
        path=str(p), total_lines=len(lines),
    )

read_tool = ToolDef(
    name="read",
    description="Read a file from the local filesystem. Path is relative to the workspace root.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace root"},
            "start_line": {"type": "integer", "description": "First line to read (1-based, default 1)"},
            "end_line": {"type": "integer", "description": "Last line to read (inclusive, default EOF)"},
        },
        "required": ["path"],
    },
    execute=_read,
)


# ── glob ───────────────────────────────────────────────────────

async def _glob(params: dict, ctx: dict) -> ToolResult:
    d = Path(ctx.get("directory", ".")).resolve()
    pattern = params["pattern"]
    matches = sorted(str(p.relative_to(d)) for p in d.rglob(pattern) if p.is_file())
    return ToolResult.ok(
        f"glob {pattern}",
        "\n".join(matches) if matches else "(no matches)",
        count=len(matches),
    )

glob_tool = ToolDef(
    name="glob",
    description="Find files matching a glob pattern (e.g. **/*.py, src/**/*.ts).",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern relative to workspace root"},
        },
        "required": ["pattern"],
    },
    execute=_glob,
)


# ── grep ───────────────────────────────────────────────────────

async def _grep(params: dict, ctx: dict) -> ToolResult:
    pattern = params["pattern"]
    include = params.get("include")
    d = Path(ctx.get("directory", ".")).resolve()

    cmd = ["rg", "--no-heading", "--line-number", "--color", "never", pattern, str(d)]
    if include:
        cmd.insert(5, "--glob")
        cmd.insert(6, include)

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, cwd=str(d))
        lines = out.stdout.strip().splitlines()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ToolResult.err("grep", "rg not found; install ripgrep for grep support")

    if not lines:
        return ToolResult.ok("grep", "(no matches)", count=0)

    max_lines = 100
    trunc = len(lines) > max_lines
    selected = lines[:max_lines]
    return ToolResult.ok(
        f"grep '{pattern}'",
        "\n".join(selected) + ("\n... (truncated)" if trunc else ""),
        count=len(lines), truncated=trunc,
    )

grep_tool = ToolDef(
    name="grep",
    description="Search file contents with regex. Requires ripgrep (rg) installed.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "include": {"type": "string", "description": "Glob pattern to filter files (e.g. *.py)"},
        },
        "required": ["pattern"],
    },
    execute=_grep,
)


# ── webfetch ───────────────────────────────────────────────────

def _validate_web_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("webfetch only supports http and https URLs")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid webfetch URL")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve host: {parsed.hostname}") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        if not ip.is_global:
            raise PermissionError(f"webfetch blocks non-public address: {ip}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_web_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


async def _webfetch(params: dict, ctx: dict) -> ToolResult:
    url = params["url"]
    try:
        _validate_web_url(url)
        req = urllib.request.Request(url, headers={"User-Agent": "StratumCode/0.1"})
        opener = urllib.request.build_opener(_SafeRedirectHandler)
        with opener.open(req, timeout=15) as resp:
            raw = resp.read(_WEBFETCH_LIMIT + 1)
    except Exception as e:
        return ToolResult.err("webfetch", str(e))
    if len(raw) > _WEBFETCH_LIMIT:
        return ToolResult.err("webfetch", f"response exceeds {_WEBFETCH_LIMIT} bytes")
    body = raw.decode("utf-8", errors="replace")
    return ToolResult.ok(
        f"fetch {url}",
        body[:8000] + ("\n... (truncated)" if len(body) > 8000 else ""),
        url=url, bytes=len(raw),
    )

webfetch_tool = ToolDef(
    name="webfetch",
    description="Fetch content from a URL and return it as text.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
        },
        "required": ["url"],
    },
    execute=_webfetch,
)


# ── todo ───────────────────────────────────────────────────────

_todos: list[dict] = []

async def _todo_read(params: dict, ctx: dict) -> ToolResult:
    if not _todos:
        return ToolResult.ok("todo", "(empty)")
    lines = []
    for t in _todos:
        status = "✓" if t.get("done") else "○"
        lines.append(f"{status} {t.get('content', '')}")
    return ToolResult.ok("todo", "\n".join(lines))

todo_read_tool = ToolDef(
    name="todoread",
    description="Read the current task list.",
    parameters={"type": "object", "properties": {}},
    execute=_todo_read,
)


# ── invalid ────────────────────────────────────────────────────

async def _invalid(params: dict, ctx: dict) -> ToolResult:
    return ToolResult.err("invalid-tool-call", params.get("message", "Invalid tool call"))

invalid_tool = ToolDef(
    name="invalid",
    description="Called when the previous tool call had invalid arguments. Rewrite with correct parameters.",
    parameters={
        "type": "object",
        "properties": {
            "tool": {"type": "string", "description": "The tool name that failed"},
            "message": {"type": "string", "description": "Error message"},
        },
    },
    execute=_invalid,
)


# ── export ─────────────────────────────────────────────────────

BUILTIN: list[ToolDef] = [
    read_tool,
    glob_tool,
    grep_tool,
    webfetch_tool,
    todo_read_tool,
    invalid_tool,
]
