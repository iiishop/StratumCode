from __future__ import annotations

import json

from ... import terminal_manager
from ..spec import ToolDef, ToolResult


async def _read_terminal(params: dict, ctx: dict) -> ToolResult:
    result = terminal_manager.read(
        str(params.get("session_id") or "").strip(),
        max_output_chars=int(params.get("max_output_chars") or 12_000),
    )
    return ToolResult(
        title=f"read_terminal {result.get('session_id')}",
        output=json.dumps(result, ensure_ascii=False, indent=2),
        metadata=result,
    )


read_terminal_tool = ToolDef(
    name="read_terminal",
    description="Read recent output from a terminal background session without waiting for it to finish.",
    parameters={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Terminal session id returned by terminal"},
            "max_output_chars": {"type": "integer", "description": "Maximum output tail to return"},
        },
        "required": ["session_id"],
    },
    execute=_read_terminal,
    capabilities=("investigation", "investigation.project_evidence", "implementation", "validation"),
    event_type="terminal",
)

TOOL = read_terminal_tool
