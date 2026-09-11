import json

from ... import application_manager
from ..spec import ToolDef, ToolResult


async def _application(params, ctx):
    try:
        result = application_manager.action(ctx.get("directory", "."), params)
        failed = result.get("status") == "failed"
        metadata = {**result, "validation_observation": params.get("action") == "read" and
                    bool(result.get("output") or result.get("exit_code") is not None)}
        return ToolResult(title=("[error] " if failed else "") + "Application " + params["action"],
                          output=json.dumps(result, ensure_ascii=False), metadata=metadata)
    except (ValueError, OSError, LookupError) as exc:
        return ToolResult(title="[error] Application", output=json.dumps({"error": str(exc)}))


TOOL = ToolDef(
    name="application",
    description="Use when validation or implementation needs repeatable application startup, lifecycle control, "
                "process output or exit results. Manage workspace launch profiles and owned processes for CLI, desktop (Unity/WPF), or services. "
                "Prefer reusing an existing matching profile/run; list before creating duplicates. "
                "Save a profile, then start; read logs/status, send input, stop or restart. "
                "No browser or URL required. Prefer executable+args; shell commands require explicit shell. "
                "visible=true explicitly shows a desktop application. kind=task uses exit code; service/desktop "
                "running is NOT ready or validated. ready_text is an optional literal log marker. "
                "confirm_ready requires observed evidence. stdin supports pipe input, not PTY/TUI. "
                "This tool does not inspect windows, click controls, issue HTTP checks, or judge acceptance; "
                "use other available observation capabilities for those claims. Save/start/confirm_ready "
                "are lifecycle bookkeeping, not validation evidence by themselves. "
                "Profiles persist; run ownership/logs last for this StratumCode process only. "
                "Stop/restart terminate the owned process tree; save application data first. "
                "Do not launch detached processes or assume a launcher exit means its application is tracked.",
    parameters={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["list", "save", "delete", "start", "read", "stop", "restart", "input", "confirm_ready"]},
        "max_output_chars": {"type": "integer", "minimum": 0, "maximum": 100000},
        "profile_id": {"type": "string"}, "run_id": {"type": "string"},
        "text": {"type": "string"}, "eof": {"type": "boolean"}, "note": {"type": "string"},
        "profile": {"type": "object", "properties": {
            "id": {"type": "string"}, "name": {"type": "string"},
            "executable": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}},
            "cwd": {"type": "string"}, "shell": {"type": "string", "enum": ["none", "powershell", "pwsh", "cmd", "bash", "sh"]},
            "command": {"type": "string"}, "kind": {"type": "string", "enum": ["service", "task", "desktop"]},
            "visible": {"type": "boolean"}, "stdin": {"type": "boolean"},
            "encoding": {"type": "string"}, "ready_text": {"type": "string"},
        }, "required": ["name"], "additionalProperties": False},
    }, "required": ["action"], "additionalProperties": False},
    execute=_application, capabilities=("implementation", "validation"),
)
