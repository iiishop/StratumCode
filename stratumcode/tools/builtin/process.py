from __future__ import annotations

import json

from ... import terminal_manager
from ..spec import ToolDef, ToolResult


async def _process(params: dict, ctx: dict) -> ToolResult:
    action = str(params.get("action") or "list").strip().casefold()
    session_id = str(params.get("session_id") or "").strip()
    if action == "list":
        result = {
            "items": terminal_manager.list_sessions(
                include_finished=bool(params.get("include_finished", True)),
                background_only=bool(params.get("background_only", False)),
            )
        }
    elif action == "poll":
        result = terminal_manager.poll(session_id)
    elif action == "wait":
        result = terminal_manager.wait(session_id, params.get("timeout_seconds"))
    elif action == "kill":
        result = terminal_manager.terminate(session_id)
    else:
        raise ValueError(f"unknown process action: {action}")
    return ToolResult.ok(f"process {action}", json.dumps(result, ensure_ascii=False, indent=2), action=action, result=result)


process_tool = ToolDef(
    name="process",
    description="List, poll, wait for, or kill terminal sessions started by the terminal tool.",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "poll", "wait", "kill"]},
            "session_id": {"type": "string", "description": "Terminal session id for poll, wait, or kill"},
            "timeout_seconds": {"type": "number", "description": "Maximum wait time for action=wait"},
            "include_finished": {"type": "boolean", "description": "Include completed sessions for action=list"},
            "background_only": {"type": "boolean", "description": "Only list background terminal sessions"},
        },
        "required": ["action"],
    },
    execute=_process,
    capabilities=("investigation", "investigation.project_evidence", "implementation", "validation"),
    event_type="terminal",
)

TOOL = process_tool
