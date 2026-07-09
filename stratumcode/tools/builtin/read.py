from __future__ import annotations

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
    return ToolResult.ok(
        f"read {params['path']} L{start+1}-{min(end, len(lines))}",
        "\n".join(selection),
        path=str(p),
        total_lines=len(lines),
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

TOOL = read_tool
