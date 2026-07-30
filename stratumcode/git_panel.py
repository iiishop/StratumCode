from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import agent_runtime, model_settings

_LOG_LIMIT = 96
_TIMEOUT = 5
_ACTION_TIMEOUT = 120
_PATCH_LIMIT = 12000


def snapshot(workspace_dir: str) -> dict:
    root = _repo_root(workspace_dir)
    if not root:
        return {"is_repo": False, "error": "Current workspace is not inside a Git repository."}

    return {
        "is_repo": True,
        "root": str(root),
        "head": _head(root),
        "status": _status(root),
        "branches": _branches(root),
        "remotes": _remotes(root),
        "stashes": _stashes(root),
        "commits": _commits(root),
    }


def run_action(workspace_dir: str, action: str, payload: dict | None = None) -> dict:
    root = _repo_root(workspace_dir)
    if not root:
        return {"ok": False, "error": "Current workspace is not inside a Git repository."}
    payload = payload or {}
    if action == "fetch":
        return _action_result(root, action, [_run(root, "fetch", "--all", "--prune")])
    if action == "pull":
        return _run_chain(root, action, [("fetch", "--all", "--prune"), ("pull", "--ff-only")])
    if action == "push":
        fetched = _run(root, "fetch", "--all", "--prune")
        if fetched["returncode"] != 0:
            return _action_result(root, action, [fetched])
        if _status(root)["behind"]:
            return {
                "ok": False,
                "action": action,
                "command": "git fetch --all --prune && git push",
                "returncode": 1,
                "stdout": fetched["stdout"],
                "stderr": fetched["stderr"],
                "error": "Remote has newer commits. Pull before pushing.",
                "snapshot": snapshot(str(root)),
            }
        return _action_result(root, action, [fetched, _run(root, "push")])
    if action == "commit":
        paths = _payload_paths(root, payload)
        return _commit(root, str(payload.get("title") or ""), str(payload.get("description") or ""), paths)
    if action == "stage":
        paths = _payload_paths(root, payload)
        return _stage(root, paths)
    if action == "unstage":
        paths = _payload_paths(root, payload)
        return _unstage(root, paths)
    if action == "stash":
        paths = _payload_paths(root, payload)
        return _stash(root, paths)
    if action in {"stash_apply", "unstash"}:
        ref = _payload_stash_ref(root, payload)
        if not ref and action == "unstash":
            ref = next((item["ref"] for item in _stashes(root)), "")
        if not ref:
            return {"ok": False, "error": "Stash ref is required.", "snapshot": snapshot(str(root))}
        return _action_result(root, action, [_run(root, "stash", "apply", ref)])
    if action == "stash_drop":
        ref = _payload_stash_ref(root, payload)
        if not ref:
            return {"ok": False, "error": "Stash ref is required.", "snapshot": snapshot(str(root))}
        return _action_result(root, action, [_run(root, "stash", "drop", ref)])
    if action == "discard":
        paths = _payload_paths(root, payload)
        return _discard(root, paths)
    if action == "generate_commit":
        paths = _payload_paths(root, payload)
        return _generate_commit(root, paths)
    if action == "checkout":
        branch = str(payload.get("branch") or "")
        if not branch:
            return {"ok": False, "error": "Branch name is required."}
        return _action_result(root, action, [_run(root, "checkout", branch)])
    return {"ok": False, "error": "Unsupported Git action."}


def _action_result(root: Path, action: str, results: list[dict]) -> dict:
    failed = next((item for item in results if item["returncode"] != 0), None)
    return {
        "ok": failed is None,
        "action": action,
        "command": " && ".join(item["command"] for item in results),
        "returncode": failed["returncode"] if failed else 0,
        "stdout": "\n".join(item["stdout"] for item in results if item["stdout"]),
        "stderr": "\n".join(item["stderr"] for item in results if item["stderr"]),
        "error": "" if failed is None else (failed["stderr"].strip() or failed["stdout"].strip() or "Git action failed."),
        "snapshot": snapshot(str(root)),
    }


def _run_chain(root: Path, action: str, commands: list[tuple[str, ...]]) -> dict:
    results = []
    for command in commands:
        result = _run(root, *command)
        results.append(result)
        if result["returncode"] != 0:
            break
    return _action_result(root, action, results)


def _run(root: Path, *args: str) -> dict:
    result = _git(root, *args, check=False, timeout=_ACTION_TIMEOUT)
    return {**result, "command": f"git {' '.join(args)}"}


def _commit(root: Path, title: str, description: str, paths: list[str]) -> dict:
    title = title.strip()
    description = description.strip()
    if not title:
        return {"ok": False, "error": "Commit title is required.", "snapshot": snapshot(str(root))}
    status = _status(root)
    if not paths and not status["dirty"]:
        return {"ok": False, "error": "No changes to commit.", "snapshot": snapshot(str(root))}
    args = ["commit", "-m", title]
    if description:
        args.extend(["-m", description])
    if paths:
        return _run_chain(root, "commit", [("add", "-A", "--", *paths), tuple(args)])
    if status["counts"]["staged"]:
        return _action_result(root, "commit", [_run(root, *args)])
    return _run_chain(root, "commit", [("add", "-A"), tuple(args)])


def _stage(root: Path, paths: list[str]) -> dict:
    if not paths:
        if not _status(root)["dirty"]:
            return {"ok": False, "error": "No changes to stage.", "snapshot": snapshot(str(root))}
        return _action_result(root, "stage", [_run(root, "add", "-A")])
    return _action_result(root, "stage", [_run(root, "add", "--", *paths)])


def _unstage(root: Path, paths: list[str]) -> dict:
    if not paths:
        return {"ok": False, "error": "No changes selected.", "snapshot": snapshot(str(root))}
    return _action_result(root, "unstage", [_run(root, "restore", "--staged", "--", *paths)])


def _stash(root: Path, paths: list[str]) -> dict:
    if not paths and not _status(root)["dirty"]:
        return {"ok": False, "error": "No changes to stash.", "snapshot": snapshot(str(root))}
    args = ("stash", "push", "-u", "-m", "StratumCode stash", "--", *paths) if paths else ("stash", "push", "-u", "-m", "StratumCode stash")
    return _action_result(root, "stash", [_run(root, *args)])


def _discard(root: Path, paths: list[str]) -> dict:
    if not paths:
        return _action_result(root, "discard", [_run(root, "reset", "--hard"), _run(root, "clean", "-fd")])
    tracked = [path for path in paths if not _status_file_by_path(root).get(path, {}).get("untracked")]
    untracked = [path for path in paths if path not in tracked]
    commands = []
    if tracked:
        commands.append(("restore", "--staged", "--worktree", "--", *tracked))
    if untracked:
        commands.append(("clean", "-f", "--", *untracked))
    return _run_chain(root, "discard", commands)


def _generate_commit(root: Path, paths: list[str]) -> dict:
    source = _commit_source(root, paths)
    if not source["text"]:
        return {"ok": False, "error": "No changes available for commit message generation.", "snapshot": snapshot(str(root))}
    fallback = _fallback_commit_message(source)
    try:
        generated = _llm_commit_message(source) or fallback
    except Exception as exc:
        generated = {**fallback, "source": f"fallback: {exc}"}
    return {"ok": True, **generated, "snapshot": snapshot(str(root))}


def _repo_root(workspace_dir: str) -> Path | None:
    result = _git(workspace_dir, "rev-parse", "--show-toplevel", check=False)
    if result["returncode"] != 0:
        return None
    value = result["stdout"].strip()
    return Path(value) if value else None


def _head(root: Path) -> dict:
    branch = _git(root, "branch", "--show-current", check=False)["stdout"].strip()
    short = _git(root, "rev-parse", "--short", "HEAD", check=False)["stdout"].strip()
    full = _git(root, "rev-parse", "HEAD", check=False)["stdout"].strip()
    return {"branch": branch or "detached", "short": short, "hash": full}


def _status(root: Path) -> dict:
    lines = _git(root, "status", "--porcelain=v1", "-b", "--untracked-files=all")["stdout"].splitlines()
    branch_line = lines[0] if lines and lines[0].startswith("## ") else ""
    files = [_status_file(line) for line in lines[1:] if line]
    files = [item for item in files if item["path"]]
    counts = {"staged": 0, "modified": 0, "untracked": 0, "conflicted": 0}
    for item in files:
        if item["conflicted"]:
            counts["conflicted"] += 1
        if item["staged"]:
            counts["staged"] += 1
        if item["worktree"] and not item["untracked"]:
            counts["modified"] += 1
        if item["untracked"]:
            counts["untracked"] += 1
    ahead, behind, upstream = _parse_branch_status(branch_line)
    return {
        "branch_line": branch_line.removeprefix("## "),
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "files": files,
        "counts": counts,
        "dirty": bool(files),
    }


def _status_file(line: str) -> dict:
    status = line[:2]
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return {
        "path": path,
        "index": status[0],
        "worktree": status[1],
        "staged": status[0] not in {" ", "?"},
        "untracked": status == "??",
        "conflicted": status[0] == "U" or status[1] == "U" or status in {"AA", "DD"},
    }


def _payload_paths(root: Path, payload: dict) -> list[str]:
    raw = payload.get("paths")
    if not isinstance(raw, list):
        return []
    allowed = set(_status_file_by_path(root))
    paths = []
    for item in raw:
        path = str(item or "").replace("\\", "/").strip()
        if path in allowed and path not in paths:
            paths.append(path)
    return paths


def _payload_stash_ref(root: Path, payload: dict) -> str:
    ref = str(payload.get("ref") or "").strip()
    allowed = {item["ref"] for item in _stashes(root)}
    return ref if ref in allowed else ""


def _status_file_by_path(root: Path) -> dict[str, dict]:
    return {item["path"]: item for item in _status(root)["files"]}


def _parse_branch_status(line: str) -> tuple[int, int, str]:
    if "..." not in line:
        return 0, 0, ""
    right = line.split("...", 1)[1]
    upstream = right.split(" [", 1)[0].strip()
    ahead = behind = 0
    if "[" in right and "]" in right:
        detail = right.split("[", 1)[1].split("]", 1)[0]
        for part in (item.strip() for item in detail.split(",")):
            if part.startswith("ahead "):
                ahead = int(part.removeprefix("ahead ") or 0)
            elif part.startswith("behind "):
                behind = int(part.removeprefix("behind ") or 0)
    return ahead, behind, upstream


def _branches(root: Path) -> list[dict]:
    output = _git(
        root,
        "branch",
        "--format=%(refname)%00%(refname:short)%00%(objectname:short)%00%(upstream:short)%00%(upstream:trackshort)%00%(HEAD)",
        "--all",
    )["stdout"]
    branches = []
    for line in output.splitlines():
        refname, name, short, upstream, track, head = (line.split("\0") + ["", "", "", "", "", ""])[:6]
        if not refname or refname.endswith("/HEAD"):
            continue
        remote = refname.startswith("refs/remotes/")
        branches.append({
            "name": name,
            "hash": short,
            "upstream": upstream,
            "track": track,
            "current": head == "*",
            "remote": remote,
        })
    return branches


def _remotes(root: Path) -> list[dict]:
    remotes: dict[str, dict] = {}
    for line in _git(root, "remote", "-v", check=False)["stdout"].splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        name, url, kind = parts[0], parts[1], parts[2].strip("()")
        item = remotes.setdefault(name, {"name": name, "fetch": "", "push": ""})
        item[kind] = url
    return list(remotes.values())


def _stashes(root: Path) -> list[dict]:
    output = _git(root, "stash", "list", "--pretty=format:%gd%x1f%cr%x1f%gs", check=False)["stdout"]
    stashes = []
    for line in output.splitlines():
        ref, relative_date, subject = (line.split("\x1f") + ["", "", ""])[:3]
        if ref:
            stashes.append({"ref": ref, "relative_date": relative_date, "subject": subject})
    return stashes


def _commits(root: Path) -> list[dict]:
    output = _git(
        root,
        "log",
        "--all",
        f"--max-count={_LOG_LIMIT}",
        "--date=short",
        "--pretty=format:%H%x1f%h%x1f%P%x1f%an%x1f%ar%x1f%ad%x1f%s%x1f%D",
    )["stdout"]
    commits = []
    for line in output.splitlines():
        full, short, parents, author, relative_date, date, subject, refs = (line.split("\x1f") + [""] * 8)[:8]
        if not full:
            continue
        commits.append({
            "hash": full,
            "short": short,
            "parents": [item for item in parents.split() if item],
            "author": author,
            "relative_date": relative_date,
            "date": date,
            "subject": subject,
            "refs": _refs(refs),
        })
    return commits


def _refs(value: str) -> list[str]:
    refs = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        if item.startswith("HEAD -> "):
            item = item.removeprefix("HEAD -> ")
        refs.append(item.removeprefix("tag: "))
    return refs


def _commit_source(root: Path, paths: list[str]) -> dict:
    stashes = _stashes(root)
    if stashes and not paths:
        patch = _git(root, "stash", "show", "-p", "--stat", stashes[0]["ref"], check=False)["stdout"]
        return {"kind": "stash", "label": stashes[0]["subject"], "text": patch[:_PATCH_LIMIT]}
    path_args = ["--", *paths] if paths else []
    parts = [_git(root, "diff", "--cached", "--stat", *path_args, check=False)["stdout"]]
    parts.append(_git(root, "diff", "--stat", *path_args, check=False)["stdout"])
    status_args = ["status", "--short", "--untracked-files=all", *path_args]
    parts.append(_git(root, *status_args, check=False)["stdout"])
    return {"kind": "working_tree", "label": "Working tree", "text": "\n".join(part for part in parts if part).strip()[:_PATCH_LIMIT]}


def _llm_commit_message(source: dict) -> dict | None:
    setting = model_settings.resolve(model_settings.GIT_COMMIT_STAGE)
    if setting is None:
        return None
    assistant = agent_runtime.call_model(
        setting["provider"],
        setting["model_id"],
        [
            {"role": "system", "content": (
                "Generate a concise Conventional Commit title and body from Git changes. "
                "Return only JSON with keys title and description."
            )},
            {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
        ],
        tools=[],
        use_skills=False,
    )
    text = agent_runtime.content_text(assistant.get("content"))
    if text.startswith("```"):
        text = text.strip("` \n")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    data = json.loads(text)
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    return {"title": title, "description": description, "source": "llm"} if title else None


def _fallback_commit_message(source: dict) -> dict:
    text = source["text"]
    lowered = text.lower()
    kind = "feat"
    if any(item in lowered for item in ("test", "spec")):
        kind = "test"
    elif any(item in lowered for item in ("doc", "readme")):
        kind = "docs"
    elif any(item in lowered for item in ("fix", "bug", "error")):
        kind = "fix"
    files = [
        line.strip().split()[0]
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(("?", "M ", "A ", "D "))
    ]
    target = Path(files[0]).stem if files else "changes"
    return {
        "title": f"{kind}: update {target}",
        "description": text[:1600],
        "source": "fallback",
    }


def _git(cwd: str | Path, *args: str, check: bool = True, timeout: int = _TIMEOUT) -> dict:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        error = exc.stderr or f"git {' '.join(args)} timed out"
        if check:
            raise ValueError(error)
        return {
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": error,
        }
    if check and completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
