from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess

from ..spec import ToolDef, ToolResult
from .common import IGNORED_DIRS, _expand_braces, _ignored, _resolve


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
    for ignored in sorted(IGNORED_DIRS):
        cmd.extend(["--glob", f"!{ignored}/**"])
    if include:
        for expanded in _expand_braces(include):
            cmd.extend(["--glob", expanded])
    cmd.extend([pattern, target_arg])

    try:
        lines = await asyncio.to_thread(_run_rg, cmd, d)
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
        count=len(lines),
        truncated=trunc,
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

TOOL = grep_tool


def _run_rg(cmd: list[str], cwd: Path) -> list[str]:
    out = subprocess.run(cmd, capture_output=True, timeout=15, cwd=str(cwd))
    stdout = out.stdout.decode("utf-8", errors="replace")
    return stdout.strip().splitlines()
