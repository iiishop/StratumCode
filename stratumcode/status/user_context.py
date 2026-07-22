from __future__ import annotations

import os


def _workspace_snapshot(workspace_dir: str) -> list[str]:
    root = os.path.abspath(workspace_dir or ".")
    if not os.path.isdir(root):
        return []
    ignored = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}
    files: list[tuple[str, int]] = []
    dirs = 0
    for current, names, filenames in os.walk(root):
        names[:] = [name for name in names if name not in ignored and not name.startswith(".")]
        dirs += len(names)
        for filename in filenames:
            if filename.startswith("."):
                continue
            path = os.path.join(current, filename)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            rel = os.path.relpath(path, root).replace("\\", "/")
            files.append((rel, size))
            if len(files) >= 41:
                break
        if len(files) >= 41:
            break
    visible = files[:40]
    lines = [
        "Workspace snapshot:",
        f"- root: {root}",
        f"- visible files: {len(files) if len(files) < 41 else '40+'}",
        f"- visible directories: {dirs}",
    ]
    if visible:
        lines.append("- files:")
        lines.extend(f"  - {path} ({size} bytes{' / empty' if size == 0 else ''})" for path, size in visible)
    else:
        lines.append("- files: (none)")
    return lines
