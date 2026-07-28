from __future__ import annotations

import json

from ... import terminal_manager
from ..spec import ToolDef, ToolResult


async def _terminal(params: dict, ctx: dict) -> ToolResult:
    result = terminal_manager.start(
        str(params.get("command") or ""),
        workspace=ctx.get("directory", "."),
        cwd=str(params.get("cwd") or ""),
        shell=str(params.get("shell") or "auto"),
        background=bool(params.get("background", False)),
        timeout_seconds=params.get("timeout_seconds"),
        max_output_chars=int(params.get("max_output_chars") or terminal_manager.MAX_OUTPUT_CHARS),
    )
    state = result.get("status")
    title = f"terminal {state}: {result.get('session_id')}"
    if result.get("background") and state == terminal_manager.RUNNING:
        title = f"terminal background: {result.get('session_id')}"
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if state in {terminal_manager.FAILED, terminal_manager.TIMEOUT}:
        return ToolResult(title="[error] " + title, output=output, metadata=result)
    return ToolResult(title=title, output=output, metadata=result)


terminal_tool = ToolDef(
    name="terminal",
    description=(
        "Run a terminal command in the workspace. Supports blocking calls and background "
        "sessions. Use background=true for long-lived servers, watchers, or slow commands."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
            "cwd": {"type": "string", "description": "Working directory relative to the workspace root"},
            "shell": {
                "type": "string",
                "enum": ["auto", "cmd", "powershell", "pwsh", "bash", "git_bash", "sh"],
                "description": "Shell runtime. auto picks a native default for the OS.",
            },
            "background": {"type": "boolean", "description": "Return immediately and keep the process running"},
            "timeout_seconds": {"type": "number", "description": "Blocking call timeout. Default 120 seconds."},
            "max_output_chars": {"type": "integer", "description": "Maximum output retained for this process"},
        },
        "required": ["command"],
    },
    execute=_terminal,
    capabilities=("investigation", "investigation.project_evidence", "implementation", "validation"),
    event_type="terminal",
)

TOOL = terminal_tool
