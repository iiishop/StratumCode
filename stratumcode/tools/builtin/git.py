from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..spec import ToolDef, ToolResult
from .common import _resolve

_TIMEOUT = 10
_LIMIT = 20000


async def _git_tool(params: dict, ctx: dict) -> ToolResult:
    root = _resolve(".", ctx)
    if not _is_repo(root):
        return ToolResult.err("git", "Current workspace is not inside a Git repository.")
    op = str(params.get("operation") or "status")
    try:
        if op == "status":
            return _status(root)
        if op == "diff":
            return _diff(root, params)
        if op == "file_history":
            return _file_history(root, params)
        if op == "file_change_diff":
            return _file_change_diff(root, params)
        if op == "show_commit":
            return _show_commit(root, params)
        if op == "blame_range":
            return _blame_range(root, params)
    except (PermissionError, ValueError) as exc:
        return ToolResult.err(f"git {op}", str(exc), operation=op)
    return ToolResult.err("git", f"unsupported operation: {op}", operation=op)


def _status(root: Path) -> ToolResult:
    output = _git(root, "status", "--short", "--branch", "--untracked-files=all")
    return ToolResult.ok("git status", output or "(clean)", operation="status")


def _diff(root: Path, params: dict) -> ToolResult:
    scope = str(params.get("scope") or "working")
    context = str(int(params.get("context") or 3))
    paths = _paths(root, params.get("paths"))
    if scope == "staged":
        args = ["diff", "--cached", f"--unified={context}"]
    elif scope == "head":
        args = ["diff", "HEAD", f"--unified={context}"]
    elif scope == "branch":
        base = str(params.get("base") or "origin/main").strip()
        args = ["diff", f"{base}...HEAD", f"--unified={context}"]
    else:
        args = ["diff", f"--unified={context}"]
    output = _git(root, *args, *(_pathspec(paths)))
    return ToolResult.ok(f"git diff {scope}", _trim(output) or "(no diff)", operation="diff", scope=scope, paths=paths)


def _file_history(root: Path, params: dict) -> ToolResult:
    path = _path(root, params.get("path"))
    limit = max(1, min(100, int(params.get("limit") or 20)))
    output = _git(root, "log", "--follow", f"--max-count={limit}", "--date=iso", "--pretty=format:%H%x1f%h%x1f%ad%x1f%an%x1f%s", "--", path)
    items = [_history_item(line) for line in output.splitlines() if line.strip()]
    return ToolResult.ok(f"git file history {path}", json.dumps({"path": path, "items": items}, ensure_ascii=False, indent=2), operation="file_history", count=len(items))


def _file_change_diff(root: Path, params: dict) -> ToolResult:
    path = _path(root, params.get("path"))
    steps = max(1, int(params.get("steps") or 1))
    commits = _file_commits(root, path)
    if not commits:
        return ToolResult.err("git file_change_diff", f"no history for file: {path}", operation="file_change_diff", path=path)
    index = min(steps - 1, len(commits) - 1)
    commit = commits[index]
    parent = f"{commit}^"
    old = f"{parent}:{path}"
    new = f"{commit}:{path}"
    if index == len(commits) - 1:
        output = _git(root, "show", "--format=", "--find-renames", commit, "--", path, check=False)
        title = f"git file introduction diff {path}"
    else:
        output = _git(root, "diff", "--find-renames", parent, commit, "--", path, check=False)
        title = f"git file change diff {path}"
    meta = _commit_meta(root, commit)
    return ToolResult.ok(title, _trim(output) or f"(no textual diff between {old} and {new})", operation="file_change_diff", path=path, commit=commit, steps=steps, commit_meta=meta)


def _show_commit(root: Path, params: dict) -> ToolResult:
    commit = str(params.get("commit") or "").strip()
    if not commit:
        raise ValueError("commit is required")
    output = _git(root, "show", "--stat", "--patch", "--find-renames", "--format=fuller", commit)
    return ToolResult.ok(f"git show {commit}", _trim(output), operation="show_commit", commit=commit)


def _blame_range(root: Path, params: dict) -> ToolResult:
    path = _path(root, params.get("path"))
    start = max(1, int(params.get("start_line") or 1))
    end = max(start, int(params.get("end_line") or start))
    output = _git(root, "blame", "--line-porcelain", f"-L{start},{end}", "HEAD", "--", path)
    rows = []
    current = {}
    for line in output.splitlines():
        if line.startswith("\t"):
            rows.append({**current, "text": line[1:]})
            current = {}
        elif len(parts := line.split()) >= 3 and re.fullmatch(r"[0-9a-f]{7,40}", parts[0]):
            current["commit"] = parts[0]
            current["line"] = int(parts[2])
        elif " " in line:
            key, value = line.split(" ", 1)
            if key in {"author", "author-time", "summary", "filename"}:
                current[key.replace("-", "_")] = value
    return ToolResult.ok(f"git blame {path} L{start}-{end}", json.dumps({"path": path, "lines": rows}, ensure_ascii=False, indent=2), operation="blame_range", path=path, start_line=start, end_line=end)


def _is_repo(root: Path) -> bool:
    return _git(root, "rev-parse", "--is-inside-work-tree", check=False).strip() == "true"


def _git(root: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=_TIMEOUT,
        check=False,
    )
    if check and completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout


def _path(root: Path, value) -> str:
    path = str(value or "").replace("\\", "/").strip()
    if not path:
        raise ValueError("path is required")
    _resolve(path, {"directory": str(root)})
    return path


def _paths(root: Path, value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_path(root, item) for item in value]


def _pathspec(paths: list[str]) -> list[str]:
    return ["--", *paths] if paths else []


def _file_commits(root: Path, path: str) -> list[str]:
    output = _git(root, "log", "--follow", "--format=%H", "--", path)
    return [line.strip() for line in output.splitlines() if line.strip()]


def _history_item(line: str) -> dict:
    full, short, date, author, subject = (line.split("\x1f") + ["", "", "", "", ""])[:5]
    return {"hash": full, "short": short, "date": date, "author": author, "subject": subject}


def _commit_meta(root: Path, commit: str) -> dict:
    output = _git(root, "show", "-s", "--date=iso", "--pretty=format:%H%x1f%h%x1f%ad%x1f%an%x1f%s", commit)
    return _history_item(output)


def _trim(output: str) -> str:
    return output if len(output) <= _LIMIT else output[:_LIMIT] + "\n... (truncated)"


git_tool = ToolDef(
    name="git",
    description="Read-only Git context: status, diffs, file history/change diffs, commit details, and blame ranges.",
    parameters={
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["status", "diff", "file_history", "file_change_diff", "show_commit", "blame_range"]},
            "scope": {"type": "string", "enum": ["working", "staged", "head", "branch"]},
            "path": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
            "commit": {"type": "string"},
            "base": {"type": "string"},
            "steps": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "context": {"type": "integer", "minimum": 0, "maximum": 20},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        },
        "required": ["operation"],
    },
    execute=_git_tool,
    capabilities=("investigation", "investigation.project_evidence"),
)

TOOL = git_tool
