import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from urllib.request import Request, urlopen


REPO = "iiishop/StratumCode"
ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
GITHUB_API = f"https://api.github.com/repos/{REPO}"


def status() -> dict:
    """Return local and remote update metadata."""
    current_version = _local_version()
    current_commit = _git(["rev-parse", "HEAD"], check=False).strip()
    short_commit = _git(["rev-parse", "--short", "HEAD"], check=False).strip()
    latest_release = _latest_release()
    remote_commit = _remote_main_commit()
    commits_behind = _commits_behind()
    latest_version = latest_release.get("tag_name", "").lstrip("v")
    return {
        "repo": REPO,
        "current_version": current_version,
        "current_commit": current_commit,
        "short_commit": short_commit,
        "latest_version": latest_version,
        "latest_release": latest_release,
        "stable_available": bool(latest_version and _is_newer(latest_version, current_version)),
        "remote_commit": remote_commit,
        "remote_short_commit": remote_commit[:7],
        "commits_behind": commits_behind,
        "dev_available": commits_behind > 0,
    }


def apply_events(channel: str):
    """Yield NDJSON-safe update progress events."""
    if channel == "stable":
        yield from _apply_stable()
    elif channel == "dev":
        yield from _apply_dev()
    else:
        yield {"op": "error", "message": f"unknown update channel: {channel}"}


def restart() -> dict:
    """Restart the current StratumCode process after the HTTP response returns."""
    command = [sys.executable, *sys.argv]

    def relaunch() -> None:
        subprocess.Popen(command, cwd=str(ROOT), close_fds=True)
        os._exit(0)

    threading.Timer(0.25, relaunch).start()
    return {"ok": True}


def _apply_stable():
    data = status()
    target = data.get("latest_release", {}).get("tag_name", "")
    label = data.get("latest_version", "")
    if not data.get("stable_available"):
        yield {"op": "done", "progress": 100, "message": "Already on the latest stable release."}
        return
    if _dirty_paths():
        yield {"op": "error", "message": "Local changes block a release checkout."}
        return
    yield {"op": "progress", "progress": 8, "message": f"Fetching release {label}."}
    _git(["fetch", "--tags", "origin"], timeout=120)
    yield {"op": "progress", "progress": 72, "message": f"Checking out {target}."}
    _git(["checkout", "--detach", target], timeout=120)
    yield {"op": "done", "progress": 100, "version": label, "message": "Stable release is ready. Restart to apply it."}


def _apply_dev():
    data = status()
    if not data.get("dev_available"):
        yield {"op": "done", "progress": 100, "message": "Already on the latest dev commit."}
        return
    if _dirty_paths():
        yield {"op": "error", "message": "Local changes block a dev update."}
        return
    yield {"op": "progress", "progress": 12, "message": "Fetching main."}
    _git(["fetch", "origin", "main"], timeout=120)
    _git(["checkout", "main"], timeout=120)
    yield {"op": "progress", "progress": 54, "message": "Fast-forwarding local checkout."}
    _git(["pull", "--ff-only", "origin", "main"], timeout=120)
    new_hash = _git(["rev-parse", "--short", "HEAD"]).strip()
    yield {"op": "done", "progress": 100, "commit": new_hash, "message": "Dev update is ready. Restart to apply it."}


def _local_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    return "0.0.0"


def _latest_release() -> dict:
    try:
        with urlopen(_request(f"{GITHUB_API}/releases/latest"), timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        "tag_name": str(data.get("tag_name") or ""),
        "name": str(data.get("name") or ""),
        "html_url": str(data.get("html_url") or ""),
        "body": str(data.get("body") or ""),
        "published_at": str(data.get("published_at") or ""),
    }


def _remote_main_commit() -> str:
    out = _git(["ls-remote", "origin", "refs/heads/main"], check=False, timeout=20)
    return out.split()[0] if out.strip() else ""


def _commits_behind() -> int:
    try:
        _git(["fetch", "origin", "main"], timeout=60)
        out = _git(["rev-list", "--count", "HEAD..origin/main"], timeout=20)
        return int(out.strip() or "0")
    except (subprocess.CalledProcessError, ValueError):
        return 0


def _dirty_paths() -> list[str]:
    out = _git(["status", "--porcelain"], check=False)
    paths = []
    for line in out.splitlines():
        path = line[3:].strip()
        if path == ".codegraph/daemon.pid":
            continue
        paths.append(path)
    return paths


def _is_newer(candidate: str, current: str) -> bool:
    return _version_tuple(candidate) > _version_tuple(current)


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = [int(part) for part in re.findall(r"\d+", value)[:3]]
    return tuple((parts + [0, 0, 0])[:3])


def _request(url: str) -> Request:
    return Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "StratumCode"})


def _git(args: list[str], check: bool = True, timeout: int = 30) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )
    return result.stdout


if __name__ == "__main__":
    assert _version_tuple("v0.0.10") > _version_tuple("0.0.2")
    assert _version_tuple("0.1") == (0, 1, 0)
