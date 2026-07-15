from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess

from ..spec import ToolDef, ToolResult
from .common import IGNORED_DIRS, _expand_braces, _ignored, _resolve


async def _grep(params: dict, ctx: dict) -> ToolResult:
    patterns = [str(item).strip() for item in params.get("patterns") or [] if str(item).strip()]
    pattern = str(params.get("pattern") or "").strip()
    if patterns:
        return await _grep_patterns(patterns[:12], params, ctx)
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


async def _grep_patterns(patterns: list[str], params: dict, ctx: dict) -> ToolResult:
    include = params.get("include") or ""
    root = Path(ctx.get("directory", ".")).resolve()
    target = _resolve(params.get("path") or ".", ctx)
    if _ignored(target, root):
        return ToolResult.ok("grep", "{}", count=0, patterns=len(patterns))
    target_arg = "." if target == root else str(target.relative_to(root))
    results = {}
    total = 0
    for pattern in patterns:
        cmd = ["rg", "--no-heading", "--line-number", "--color", "never"]
        for ignored in sorted(IGNORED_DIRS):
            cmd.extend(["--glob", f"!{ignored}/**"])
        if include:
            for expanded in _expand_braces(include):
                cmd.extend(["--glob", expanded])
        cmd.extend([pattern, target_arg])
        try:
            lines = await asyncio.to_thread(_run_rg, cmd, root)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ToolResult.err("grep", "rg not found; install ripgrep for grep support")
        total += len(lines)
        results[pattern] = {
            "count": len(lines),
            "matches": lines[:40],
            "truncated": len(lines) > 40,
        }
    return ToolResult.ok(
        "grep patterns",
        json.dumps(results, ensure_ascii=False, indent=2),
        patterns=len(patterns),
        count=total,
    )


grep_tool = ToolDef(
    name="grep",
    description="Search file contents with one regex pattern or several related regex patterns. Requires ripgrep (rg) installed.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "patterns": {"type": "array", "items": {"type": "string"}, "description": "Related regex patterns to search together, up to 12. Use instead of repeated grep calls."},
            "include": {"type": "string", "description": "Glob pattern to filter files (e.g. *.py)"},
            "path": {"type": "string", "description": "Optional file or directory path to search within"},
        },
    },
    execute=_grep,
)

TOOL = grep_tool


def _run_rg(cmd: list[str], cwd: Path) -> list[str]:
    out = subprocess.run(cmd, capture_output=True, timeout=15, cwd=str(cwd))
    stdout = out.stdout.decode("utf-8", errors="replace")
    return stdout.strip().splitlines()
