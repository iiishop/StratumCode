from __future__ import annotations

from difflib import get_close_matches

from ... import lsp
from ...patch_engine import snapshot_file
from ..spec import ToolDef, ToolResult
from .common import _ignored, _resolve


async def _read(params: dict, ctx: dict) -> ToolResult:
    root = _resolve(".", ctx)
    p = _resolve(params["path"], ctx)
    if p.is_dir():
        entries = _directory_entries(p, root)
        rel = p.relative_to(root).as_posix() if p != root else "."
        return ToolResult.err(
            "read",
            f"path is a directory: {params['path']}\n\nDirectory entries for {rel}:\n" + "\n".join(entries),
            error_code="PATH_IS_DIRECTORY",
            suggestions=entries,
        )
    if not p.is_file():
        suggestions = _similar_files(str(params["path"]), root)
        output = f"not a file: {params['path']}"
        if suggestions:
            output += "\n\nPossible files:\n" + "\n".join(f"- {item}" for item in suggestions)
        else:
            output += "\n\nNo similar files found. Use glob before read."
        return ToolResult.err(
            "read",
            output,
            error_code="FILE_NOT_FOUND",
            suggestions=suggestions,
        )
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = max(0, params.get("start_line", 1) - 1)
    end = params.get("end_line") or len(lines)
    selection = lines[start:end]
    stat = p.stat()
    snapshot = snapshot_file(p, root)
    lsp_status = lsp.touch_file(str(p.relative_to(root)), str(ctx.get("directory", ".")))
    diagnostics = _format_diagnostics(p, lsp.diagnostics_for(p))
    output = "\n".join(selection)
    if diagnostics:
        output += "\n\n<lsp-diagnostics>\n" + diagnostics + "\n</lsp-diagnostics>"
    return ToolResult.ok(
        f"read {params['path']} L{start+1}-{min(end, len(lines))}",
        output,
        path=str(p),
        snapshot_id=snapshot.id,
        content_hash=snapshot.content_hash,
        encoding=snapshot.encoding,
        newline=snapshot.newline,
        bom=snapshot.bom,
        mtime_ns=stat.st_mtime_ns,
        size=stat.st_size,
        total_lines=len(lines),
        diagnostics=len(lsp.diagnostics_for(p)),
        lsp_checked=bool(lsp_status.get("checked")),
        lsp_server=lsp_status.get("server", ""),
        lsp_error=lsp_status.get("error", ""),
    )


def _directory_entries(path, root) -> list[str]:
    rows = []
    for item in sorted(path.iterdir(), key=lambda value: (value.is_file(), value.name.casefold()))[:40]:
        if item.name.startswith(".") or _ignored(item, root):
            continue
        suffix = "/" if item.is_dir() else ""
        rows.append(item.relative_to(root).as_posix() + suffix)
    return rows or ["(empty directory)"]


def _similar_files(query: str, root) -> list[str]:
    query_text = str(query or "").replace("\\", "/").strip("/")
    query_name = query_text.rsplit("/", 1)[-1].casefold()
    files = []
    for index, item in enumerate(root.rglob("*")):
        if index > 1200:
            break
        if item.name.startswith(".") or _ignored(item, root) or not item.is_file():
            continue
        rel = item.relative_to(root).as_posix()
        name = item.name.casefold()
        if query_name and query_name in name:
            files.append(rel)
        elif query_name and name in query_name:
            files.append(rel)
        if len(files) >= 10:
            return files
    if files:
        return files
    rels = [
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file() and not item.name.startswith(".") and not _ignored(item, root)
    ][:500]
    return get_close_matches(query_text, rels, n=8, cutoff=0.35)


def _format_diagnostics(path, diagnostics: list[dict]) -> str:
    labels = {1: "ERROR", 2: "WARNING", 3: "INFO", 4: "HINT"}
    rows = []
    for item in diagnostics[:20]:
        line = int(item.get("range", {}).get("start", {}).get("line", 0)) + 1
        severity = labels.get(item.get("severity"), "DIAGNOSTIC")
        rows.append(f"{path.name} (line {line}): {severity} - {item.get('message', '')}")
    return "\n".join(rows)


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

TOOL = read_tool
