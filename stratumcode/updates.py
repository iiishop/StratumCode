import json
import os
import re
import subprocess
import sys
import threading
import shutil
import time
from pathlib import Path
from urllib.request import Request, urlopen


REPO = "iiishop/StratumCode"
ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
GITHUB_API = f"https://api.github.com/repos/{REPO}"

# GitHub REST API 未认证限流 60 次/小时/IP；前端每分钟轮询一次更新状态，
# 不缓存必然撞限流。release 信息缓存 10 分钟（<=6 次/小时），git 远端信息缓存 2 分钟。
_RELEASE_CACHE_TTL_SECONDS = 600
_GIT_REMOTE_CACHE_TTL_SECONDS = 120
_RELEASE_CACHE: dict = {}
_GIT_REMOTE_CACHE: dict = {}
_GIT_BEHIND_CACHE: dict = {}


def status() -> dict:
    """Return local and remote update metadata."""
    current_version = _local_version()
    current_commit = _git(["rev-parse", "HEAD"], check=False).strip()
    short_commit = _git(["rev-parse", "--short", "HEAD"], check=False).strip()
    latest_release, release_error = _latest_release()
    remote_commit, remote_error = _remote_main_commit()
    commits_behind, behind_error = _commits_behind()
    latest_version = latest_release.get("tag_name", "").lstrip("v")
    diagnostics = []
    if release_error:
        diagnostics.append({"source": "github_release", "message": release_error, "hint": "GitHub API unreachable or rate-limited. Check network or try again later."})
    if remote_error:
        diagnostics.append({"source": "git_remote", "message": remote_error, "hint": "Cannot reach origin remote. Verify network and `git remote -v`."})
    if behind_error:
        diagnostics.append({"source": "git_behind", "message": behind_error, "hint": "Cannot fetch from origin. Check network or remote URL."})
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
        "diagnostics": diagnostics,
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
    """Restart the current StratumCode process after the HTTP response returns.

    用 uv run 重启（项目统一 uv 管理）：自动 sync 依赖，跨平台，不依赖 console script wrapper。
    按当前进程 argv 判断 dev 还是生产模式，重启后保持一致。
    """
    entry = "stratumcode-dev" if "dev" in os.path.basename(sys.argv[0]) else "stratumcode"
    command = ["uv", "run", entry]

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


def _latest_release():
    now = time.time()
    if _RELEASE_CACHE and now - _RELEASE_CACHE.get("at", 0) < _RELEASE_CACHE_TTL_SECONDS:
        return _RELEASE_CACHE.get("release", {}), _RELEASE_CACHE.get("error")
    try:
        with urlopen(_request(f"{GITHUB_API}/releases/latest"), timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _RELEASE_CACHE.update({"at": now, "release": {}, "error": str(exc)})
        return {}, str(exc)
    release = {
        "tag_name": str(data.get("tag_name") or ""),
        "name": str(data.get("name") or ""),
        "html_url": str(data.get("html_url") or ""),
        "body": str(data.get("body") or ""),
        "published_at": str(data.get("published_at") or ""),
    }
    _RELEASE_CACHE.update({"at": now, "release": release, "error": None})
    return release, None


def _remote_main_commit():
    now = time.time()
    if _GIT_REMOTE_CACHE and now - _GIT_REMOTE_CACHE.get("at", 0) < _GIT_REMOTE_CACHE_TTL_SECONDS:
        return _GIT_REMOTE_CACHE.get("remote_commit", ""), _GIT_REMOTE_CACHE.get("remote_error")
    try:
        out = _git(["ls-remote", "origin", "refs/heads/main"], check=False, timeout=20)
        if not out.strip():
            _GIT_REMOTE_CACHE.update({"at": now, "remote_commit": "", "remote_error": "git ls-remote returned empty — origin remote missing or unreachable"})
            return "", "git ls-remote returned empty — origin remote missing or unreachable"
        commit = out.split()[0]
        _GIT_REMOTE_CACHE.update({"at": now, "remote_commit": commit, "remote_error": None})
        return commit, None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        _GIT_REMOTE_CACHE.update({"at": now, "remote_commit": "", "remote_error": str(exc)})
        return "", str(exc)


def _commits_behind():
    now = time.time()
    if _GIT_BEHIND_CACHE and now - _GIT_BEHIND_CACHE.get("at", 0) < _GIT_REMOTE_CACHE_TTL_SECONDS:
        return _GIT_BEHIND_CACHE.get("commits_behind", 0), _GIT_BEHIND_CACHE.get("behind_error")
    try:
        _git(["fetch", "origin", "main"], timeout=60)
        out = _git(["rev-list", "--count", "HEAD..origin/main"], timeout=20)
        count = int(out.strip() or "0")
        _GIT_BEHIND_CACHE.update({"at": now, "commits_behind": count, "behind_error": None})
        return count, None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        _GIT_BEHIND_CACHE.update({"at": now, "commits_behind": 0, "behind_error": str(exc)})
        return 0, str(exc)


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
