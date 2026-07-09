from __future__ import annotations

import fnmatch
from pathlib import Path

from ..spec import ToolDef, ToolResult
from .common import _expand_braces, _ignored


async def _glob(params: dict, ctx: dict) -> ToolResult:
    d = Path(ctx.get("directory", ".")).resolve()
    pattern = params.get("pattern") or "**/*"
    patterns = _expand_braces(pattern)
    matches = sorted({
        p.relative_to(d).as_posix()
        for p in d.rglob("*")
        if p.is_file() and not _ignored(p, d) and _matches_any(p.relative_to(d).as_posix(), patterns)
    })
    max_matches = 100
    selected = matches[:max_matches]
    truncated = len(matches) > max_matches
    return ToolResult.ok(
        f"glob {pattern}",
        (
            "\n".join(selected) + ("\n... (truncated)" if truncated else "")
            if matches
            else "(no matches)"
        ),
        count=len(matches),
        truncated=truncated,
    )


glob_tool = ToolDef(
    name="glob",
    description="Find files matching a glob pattern (e.g. **/*.py, src/**/*.ts).",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern relative to workspace root"},
        },
        "required": ["pattern"],
    },
    execute=_glob,
)

TOOL = glob_tool


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(
        fnmatch.fnmatchcase(path, pattern)
        or (pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]))
        for pattern in patterns
    )
