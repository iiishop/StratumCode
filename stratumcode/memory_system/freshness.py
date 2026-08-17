from __future__ import annotations

import hashlib
from pathlib import Path


def file_fingerprint(workspace_dir: str, path: str, *, include_hash: bool = False) -> dict:
    target = _workspace_path(workspace_dir, path)
    if not target.exists() or not target.is_file():
        return {"path": path, "exists": False}
    stat = target.stat()
    data = {
        "path": _relative_path(workspace_dir, target),
        "exists": True,
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }
    if include_hash:
        data["sha256"] = _sha256(target)
    return data


def freshness_status(workspace_dir: str, fingerprint: dict) -> str:
    path = str(fingerprint.get("path") or "")
    if not path:
        return "unknown"
    current = file_fingerprint(workspace_dir, path, include_hash=bool(fingerprint.get("sha256")))
    if not current.get("exists"):
        return "stale"
    for key in ("mtime_ns", "size", "sha256"):
        if key in fingerprint and fingerprint.get(key) != current.get(key):
            return "stale"
    return "fresh"


def _workspace_path(workspace_dir: str, path: str) -> Path:
    root = Path(workspace_dir or ".").expanduser().resolve()
    target = Path(path)
    if target.is_absolute():
        return target
    return root / target


def _relative_path(workspace_dir: str, path: Path) -> str:
    root = Path(workspace_dir or ".").expanduser().resolve()
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
