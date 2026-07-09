"""Builtin read-only tools exposed by the local tool registry."""

from __future__ import annotations

import html
import ipaddress
import socket
import subprocess
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from ..spec import ToolDef, ToolResult


_WEBFETCH_LIMIT = 1_000_000
_IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
}


# ── helpers ────────────────────────────────────────────────────

def _resolve(path: str, ctx: dict) -> Path:
    d = Path(ctx.get("directory", ".")).resolve()
    path = path or "."
    p = (d / path).resolve()
    if not p.is_relative_to(d):
        raise PermissionError(f"path escapes worktree: {path}")
    return p


def _ignored(path: Path, root: Path) -> bool:
    try:
        return any(part in _IGNORED_DIRS for part in path.relative_to(root).parts)
    except ValueError:
        return False


def _expand_braces(pattern: str) -> list[str]:
    start = pattern.find("{")
    end = pattern.find("}", start + 1)
    if start == -1 or end == -1:
        return [pattern]
    prefix, suffix = pattern[:start], pattern[end + 1:]
    return [
        expanded
        for option in pattern[start + 1:end].split(",")
        for expanded in _expand_braces(prefix + option + suffix)
    ]


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
    pattern = params.get("pattern") or "**/*"
    matches = sorted({
        p.relative_to(d).as_posix()
        for expanded in _expand_braces(pattern)
        for p in d.rglob(expanded)
        if p.is_file() and not _ignored(p, d)
    })
    max_matches = 100
    selected = matches[:max_matches]
    truncated = len(matches) > max_matches
    return ToolResult.ok(
        f"glob {pattern}",
        (
            "\n".join(selected) + ("\n... (truncated)" if truncated else "")
            if matches
            else "(no matches)"
        ),
        count=len(matches),
        truncated=truncated,
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
    pattern = params.get("pattern") or ""
    if not pattern:
        return ToolResult.err("grep", "pattern is required")
    include = params.get("include") or ""
    d = Path(ctx.get("directory", ".")).resolve()
    target = _resolve(params.get("path") or ".", ctx)
    if _ignored(target, d):
        return ToolResult.ok("grep", "(no matches)", count=0)
    target_arg = "." if target == d else str(target.relative_to(d))

    cmd = ["rg", "--no-heading", "--line-number", "--color", "never"]
    for ignored in sorted(_IGNORED_DIRS):
        cmd.extend(["--glob", f"!{ignored}/**"])
    if include:
        for expanded in _expand_braces(include):
            cmd.extend(["--glob", expanded])
    cmd.extend([pattern, target_arg])

    try:
        out = subprocess.run(cmd, capture_output=True, timeout=15, cwd=str(d))
        stdout = out.stdout.decode("utf-8", errors="replace")
        lines = stdout.strip().splitlines()
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
            "path": {"type": "string", "description": "Optional file or directory path to search within"},
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

class _DuckDuckGoParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture = ""

    def _push_current(self) -> None:
        if self._current is None:
            return
        self._current = {key: value.strip() for key, value in self._current.items()}
        if self._current["title"] and self._current["url"]:
            self.results.append(self._current)
        self._current = None
        self._capture = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "a" and "result__a" in classes:
            self._push_current()
            self._current = {
                "title": "",
                "url": _decode_ddg_url(attrs.get("href", "")),
                "snippet": "",
            }
            self._capture = "title"
        elif self._current is not None and "result__snippet" in classes:
            self._capture = "snippet"

    def handle_endtag(self, tag):
        if tag == "a" and self._capture == "title":
            self._capture = ""
        elif self._current is not None and self._capture == "snippet" and tag in {"a", "div"}:
            self._push_current()

    def close(self):
        self._push_current()
        super().close()

    def handle_data(self, data):
        if self._current is not None and self._capture:
            self._current[self._capture] += html.unescape(data)


def _decode_ddg_url(url: str) -> str:
    query = parse_qs(urlsplit(url).query)
    return unquote(query.get("uddg", [url])[0])


async def _websearch(params: dict, ctx: dict) -> ToolResult:
    query = (params.get("query") or "").strip()
    limit = min(8, max(1, int(params.get("limit", 5))))
    if not query:
        return ToolResult.err("websearch", "query is required")
    url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
    failure = ""
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; StratumCode/0.1)"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(500_000).decode("utf-8", errors="replace")
        parser = _DuckDuckGoParser()
        parser.feed(body)
        parser.close()
        results = parser.results[:limit]
    except Exception as exc:
        results = []
        failure = str(exc)
    if not results:
        fallback = await _mcp_websearch(query, limit, failure or "search provider returned no results")
        if fallback:
            return fallback
        return ToolResult.err(
            f"search {query}",
            (failure or "search provider returned no results")
            + "; websearch is unavailable and no MCP search fallback is available",
        )
    output = "\n\n".join(
        f"{index}. {item['title']}\n{item['url']}\n{item['snippet']}"
        for index, item in enumerate(results, 1)
    )
    return ToolResult.ok(f"search {query}", output, count=len(results), query=query)


websearch_tool = ToolDef(
    name="websearch",
    description="Search the public web and return result titles, URLs, and snippets.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8,
                "description": "Maximum results",
            },
        },
        "required": ["query"],
    },
    execute=_websearch,
)


async def _mcp_websearch(query: str, limit: int, reason: str) -> ToolResult | None:
    from .. import registry as tool_registry

    for tool in tool_registry.list_all():
        if not tool.name.startswith("mcp_") or "search" not in tool.name:
            continue
        props = tool.parameters.get("properties", {}) if isinstance(tool.parameters, dict) else {}
        tool_params = {"query": query}
        for name in ("numResults", "num_results", "limit", "maxResults"):
            if name in props:
                tool_params[name] = limit
        try:
            result = await tool.execute(tool_params, {"directory": "."})
        except Exception:
            continue
        if not result.title.startswith("[error]") and result.output.strip():
            return ToolResult.ok(
                f"search {query}",
                result.output,
                provider="mcp",
                source_tool=tool.name,
                fallback_reason=reason,
            )
    return None


async def _subagent_mcp_install(params: dict, ctx: dict) -> ToolResult:
    hint = (params.get("hint") or params.get("url") or "").strip()
    if not hint:
        return ToolResult.err("subagent_mcp_install", "hint is required")
    from ... import subagents

    packets = list(subagents.mcp_install_stream(hint, ctx.get("directory", ".")))
    done = next((packet for packet in reversed(packets) if packet.get("op") == "done"), {})
    if done.get("error"):
        return ToolResult.err("subagent_mcp_install", done["error"])
    server = done.get("server") or {}
    output = (
        f"Installed {server.get('name')} MCP server with "
        f"{len(server.get('tools') or [])} tools. Status: {server.get('status')}."
    )
    return ToolResult.ok(output, output, events=packets, server=server)


subagent_mcp_install_tool = ToolDef(
    name="subagent_mcp_install",
    description="Run the MCP installer subagent on a URL, docs page, or MCP config hint.",
    parameters={
        "type": "object",
        "properties": {
            "hint": {"type": "string", "description": "MCP docs URL, raw URL, or config information"},
        },
        "required": ["hint"],
    },
    execute=_subagent_mcp_install,
)


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
    websearch_tool,
    subagent_mcp_install_tool,
    todo_read_tool,
    invalid_tool,
]
