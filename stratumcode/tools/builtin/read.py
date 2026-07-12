from __future__ import annotations

from ... import lsp
from ...patch_engine import snapshot_file
from ..spec import ToolDef, ToolResult
from .common import _resolve


async def _read(params: dict, ctx: dict) -> ToolResult:
    p = _resolve(params["path"], ctx)
    if not p.is_file():
        return ToolResult.err("read", f"not a file: {params['path']}")
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = max(0, params.get("start_line", 1) - 1)
    end = params.get("end_line") or len(lines)
    selection = lines[start:end]
    stat = p.stat()
    snapshot = snapshot_file(p, _resolve(".", ctx))
    lsp_status = lsp.touch_file(str(p.relative_to(_resolve(".", ctx))), str(ctx.get("directory", ".")))
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
