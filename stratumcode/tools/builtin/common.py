from __future__ import annotations

from pathlib import Path


IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
}


def _resolve(path: str, ctx: dict) -> Path:
    d = Path(ctx.get("directory", ".")).resolve()
    path = path or "."
    p = (d / path).resolve()
    if not p.is_relative_to(d):
        raise PermissionError(f"path escapes worktree: {path}")
    return p


def _ignored(path: Path, root: Path) -> bool:
    try:
        return any(part in IGNORED_DIRS for part in path.relative_to(root).parts)
    except ValueError:
        return False


def _expand_braces(pattern: str) -> list[str]:
    start = pattern.find("{")
    end = pattern.find("}", start + 1)
    if start == -1 or end == -1:
        return [pattern]
    prefix, suffix = pattern[:start], pattern[end + 1:]
    return [
        expanded
        for option in pattern[start + 1:end].split(",")
        for expanded in _expand_braces(prefix + option + suffix)
    ]
