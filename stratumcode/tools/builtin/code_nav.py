from __future__ import annotations

import json

from ... import lsp
from ..spec import ToolDef, ToolResult


_OPS = {
    "symbols": "document_symbols",
    "definition": "definition",
    "references": "references",
    "hover": "hover",
}


async def _code_nav(params: dict, ctx: dict) -> ToolResult:
    operation = str(params.get("operation") or "").strip()
    if operation not in _OPS:
        return ToolResult.err("code_nav", f"unsupported operation: {operation}")
    request = {
        "operation": _OPS[operation],
        "path": params.get("path") or "",
    }
    if operation != "symbols":
        request["line"] = int(params.get("line") or 1)
        request["character"] = int(params.get("character") or params.get("column") or 1)
    try:
        result = lsp.query(request, str(ctx.get("directory", ".")))
    except Exception as exc:
        return ToolResult.err("code_nav", str(exc), operation=operation)
    if not _has_result(result.get("result")):
        return ToolResult.err(
            "code_nav",
            f"No {operation} result returned. The LSP may not support this operation at that position.",
            operation=operation,
            server=result.get("server", ""),
            path=result.get("path", ""),
        )
    return ToolResult.ok(
        f"code_nav {operation}",
        json.dumps(result, ensure_ascii=False, indent=2),
        operation=operation,
        server=result.get("server", ""),
        path=result.get("path", ""),
    )


def _has_result(value) -> bool:
    if value is None or value == []:
        return False
    if isinstance(value, dict) and "contents" in value:
        return bool(value.get("contents"))
    return True


code_nav_tool = ToolDef(
    name="code_nav",
    description=(
        "Use LSP semantic navigation for a workspace file: document symbols, "
        "definition, references, or hover. Use this when grep/read is not enough "
        "and you need language-aware symbol locations."
    ),
    parameters={
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["symbols", "definition", "references", "hover"],
                "description": "Semantic navigation operation.",
            },
            "path": {"type": "string", "description": "File path relative to the workspace root."},
            "line": {"type": "integer", "description": "1-based line number for definition/references/hover."},
            "character": {"type": "integer", "description": "1-based character offset for definition/references/hover."},
        },
        "required": ["operation", "path"],
    },
    execute=_code_nav,
)

TOOL = code_nav_tool
