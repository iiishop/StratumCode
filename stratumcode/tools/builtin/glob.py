from __future__ import annotations

import fnmatch
import os
from functools import lru_cache
from pathlib import Path

from ..spec import ToolDef, ToolResult
from .common import IGNORED_DIRS, _expand_braces


async def _glob(params: dict, ctx: dict) -> ToolResult:
    d = Path(ctx.get("directory", ".")).resolve()
    pattern = params.get("pattern") or "**/*"
    patterns = [p.replace("\\", "/").removeprefix("./") for p in _expand_braces(pattern)]
    max_depth = None if any("**" in p.split("/") for p in patterns) else max(len(p.split("/")) for p in patterns)
    ignored = IGNORED_DIRS | {".StratumCode"}
    matches = []
    for base, directories, files in os.walk(d, followlinks=False):
        relative = Path(base).relative_to(d)
        depth = len(relative.parts)
        directories[:] = [name for name in directories if name not in ignored
                           and (max_depth is None or depth + 1 < max_depth)
                           and _matches_any((relative / name).as_posix(), patterns, directory=True)]
        for name in files:
            path = (relative / name).as_posix()
            if _matches_any(path, patterns):
                matches.append(path)
    matches.sort()
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
        workspace_root=str(d),
        pattern=pattern,
        ignored_directories=sorted(ignored),
    )


glob_tool = ToolDef(
    name="glob",
    description="Find workspace-relative files. * matches within ONE path segment; ** matches zero or more directories. For example *.py is root-only, **/*.py is recursive. Results exclude generated/dependency directories and may be truncated. Use list_directory for immediate directories, not glob('*').",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern relative to workspace root"},
        },
        "required": ["pattern"],
    },
    execute=_glob,
    capabilities=("investigation", "investigation.project_evidence"),
)

TOOL = glob_tool


def _matches_any(path: str, patterns: list[str], *, directory: bool = False) -> bool:
    parts = tuple(path.replace("\\", "/").split("/"))

    def matches(pattern: str) -> bool:
        segments = tuple(pattern.replace("\\", "/").split("/"))

        @lru_cache(maxsize=None)
        def visit(i: int, j: int) -> bool:
            if directory and i == len(parts):
                return j < len(segments)
            if j == len(segments):
                return i == len(parts)
            if segments[j] == "**":
                return visit(i, j + 1) or (i < len(parts) and visit(i + 1, j))
            return i < len(parts) and fnmatch.fnmatchcase(parts[i], segments[j]) and visit(i + 1, j + 1)

        return visit(0, 0)

    return any(matches(pattern) for pattern in patterns)
