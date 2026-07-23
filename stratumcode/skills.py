from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from shutil import which
from urllib.request import urlopen

import yaml

SKILL_ROOT = Path.home() / ".local" / "share" / "stratumcode" / "skills"
SKILL_ROOTS = [
    ("stratumcode", SKILL_ROOT),
    ("codex", Path.home() / ".codex" / "skills"),
    ("agents", Path.home() / ".agents" / "skills"),
    ("opencode", Path.home() / ".config" / "opencode" / "skills"),
    ("opencode", Path.home() / ".opencode" / "skills"),
    ("hermes", Path.home() / ".config" / "hermes" / "skills"),
    ("hermes", Path.home() / ".hermes" / "skills"),
    ("claude", Path.home() / ".claude" / "skills"),
    ("cursor", Path.home() / ".cursor" / "skills"),
]
_MAX_PREVIEW_BYTES = 96_000
_SKILL_SPEC_RE = re.compile(r"(?P<package>[\w.-]+/[\w.-]+@[\w.-]+)")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def list_local() -> dict:
    items = []
    for source, root in SKILL_ROOTS:
        items.extend(_skills_in(root, source))
    items = _dedupe_skills(items)
    return {
        "items": sorted(items, key=lambda item: item["name"].casefold()),
        "roots": [str(root) for _, root in SKILL_ROOTS],
        "root_sources": [{"source": source, "path": str(root)} for source, root in SKILL_ROOTS],
        "runtime": runtime_status(),
    }


def runtime_status() -> dict:
    node = _tool_command("node")
    npm = _tool_command("npm")
    npx = _npx_command()
    return {
        "available": bool(npx),
        "node": {"available": bool(node), "command": node},
        "npm": {"available": bool(npm), "command": npm},
        "npx": {"available": bool(npx), "command": npx},
    }


def install_runtime() -> dict:
    _winget_install("OpenJS.NodeJS.LTS", "Node.js")
    status = runtime_status()
    if not status["available"]:
        raise ValueError("Node.js installed but npx is not available in this process")
    return status


def search(query: str) -> dict:
    query = query.strip()
    if not query:
        return {"items": []}
    npx = _npx_command()
    if not npx:
        raise ValueError("npx is required to search skills")
    try:
        result = subprocess.run(
            [npx, "skills", "find", query],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(str(exc)) from exc
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or "skill search failed")
    local_packages = {item["package"] for item in list_local()["items"] if item.get("package")}
    items = _parse_find_output(result.stdout)
    for item in items:
        item["installed"] = item.get("package") in local_packages
    return {"items": items}


def add(source: str) -> dict:
    source = source.strip()
    if not source:
        raise ValueError("source is required")
    if source.startswith(("http://", "https://")):
        try:
            _add_with_cli(source)
        except ValueError:
            _download_skill_url(source)
        return list_local()
    if _SKILL_SPEC_RE.search(source):
        _add_with_cli(source)
        return list_local()
    _import_local(Path(source).expanduser())
    return list_local()


def create(name: str, description: str = "", content: str = "") -> dict:
    slug = _slug(name)
    if not slug:
        raise ValueError("name is required")
    target = SKILL_ROOT / slug
    if target.exists():
        raise ValueError(f"skill already exists: {slug}")
    target.mkdir(parents=True)
    body = content.strip() or f"""---
name: {slug}
description: {description.strip()}
---

# {slug}
"""
    (target / "SKILL.md").write_text(body + ("\n" if not body.endswith("\n") else ""), encoding="utf-8")
    return {"skill": _skill_from_file(target / "SKILL.md", SKILL_ROOT, "stratumcode")}


def delete(path: str) -> dict:
    path = path.strip()
    if not path:
        raise ValueError("path is required")
    skill_file = _skill_file(Path(path))
    skill_dir = skill_file.parent.resolve()
    root = SKILL_ROOT.resolve()
    try:
        skill_dir.relative_to(root)
    except ValueError as exc:
        raise ValueError("only StratumCode skills can be deleted") from exc
    if skill_dir == root:
        raise ValueError("refusing to delete skill root")
    shutil.rmtree(skill_dir)
    return list_local()


def preview(path: str = "", source: str = "") -> dict:
    if path:
        skill_file = _skill_file(Path(path))
        content = _read_preview(skill_file)
        return {"path": str(skill_file), "content": content, "truncated": skill_file.stat().st_size > _MAX_PREVIEW_BYTES}
    if source.startswith(("http://", "https://")):
        if not _looks_like_markdown(source):
            return {"path": source, "content": source, "truncated": False}
        try:
            with urlopen(source, timeout=20) as response:
                data = response.read(_MAX_PREVIEW_BYTES + 1)
        except OSError as exc:
            raise ValueError(str(exc)) from exc
        return {
            "path": source,
            "content": data[:_MAX_PREVIEW_BYTES].decode("utf-8", errors="replace"),
            "truncated": len(data) > _MAX_PREVIEW_BYTES,
        }
    command = f"npx skills add {source}" if source else ""
    return {"path": source, "content": command, "truncated": False}


def _skills_in(root: Path, source: str) -> list[dict]:
    if not root.is_dir():
        return []
    result = []
    for entry in root.iterdir():
        skill_file = _skill_file(entry, missing_ok=True)
        if skill_file:
            result.append(_skill_from_file(skill_file, root, source))
    return result


def _skill_from_file(skill_file: Path, root: Path, source: str = "local") -> dict:
    meta = _read_frontmatter(skill_file)
    name = str(meta.get("name") or skill_file.parent.name)
    return {
        "id": str(skill_file.parent),
        "name": name,
        "description": str(meta.get("description") or ""),
        "path": str(skill_file.parent),
        "skill_file": str(skill_file),
        "root": str(root),
        "source": source,
        "source_label": source,
        "source_path": str(root),
        "package": str(meta.get("package") or ""),
        "metadata": meta,
        "installed": True,
        "updated_at": skill_file.stat().st_mtime,
    }


def _dedupe_skills(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        key = _skill_key(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _skill_key(item: dict) -> str:
    value = item.get("package") or item.get("name") or item.get("path") or item.get("id") or ""
    return str(value).casefold()


def _read_frontmatter(path: Path) -> dict:
    try:
        return _read_frontmatter_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


def _read_frontmatter_text(content: str) -> dict:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta_lines = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        meta_lines.append(line)
    try:
        parsed = yaml.safe_load("\n".join(meta_lines)) or {}
    except yaml.YAMLError:
        return {}
    return _json_safe(parsed) if isinstance(parsed, dict) else {}


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple | set):
        return [_json_safe(item) for item in value]
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


def _parse_find_output(output: str | None) -> list[dict]:
    items = []
    current = None
    for raw_line in (output or "").splitlines():
        line = _ANSI_RE.sub("", raw_line).strip()
        match = _SKILL_SPEC_RE.search(line)
        if match:
            if "<" in line or ">" in line:
                continue
            package = match.group("package")
            name = package.rsplit("@", 1)[-1]
            current = {
                "id": package,
                "name": name,
                "description": "",
                "package": package,
                "source": "remote",
                "source_label": "skills.sh",
                "url": "",
                "installed": False,
            }
            items.append(current)
            continue
        url_match = re.search(r"https?://\S+", line)
        if current and url_match:
            current["url"] = url_match.group(0)
    return items


def _add_with_cli(source: str) -> None:
    npx = _npx_command()
    if not npx:
        raise ValueError("npx is required to add remote skills")
    result = subprocess.run(
        [npx, "skills", "add", source, "-g", "-y"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or "skill add failed")


def _download_skill_url(source: str) -> None:
    with urlopen(source, timeout=30) as response:
        content = response.read(_MAX_PREVIEW_BYTES + 1).decode("utf-8", errors="replace")
    if not content.lstrip().startswith("---"):
        raise ValueError("URL did not resolve to a SKILL.md file")
    meta = _read_frontmatter_text(content)
    name = _slug(meta.get("name") or Path(source).stem)
    if not name:
        raise ValueError("skill name is required")
    target = SKILL_ROOT / name
    if target.exists():
        raise ValueError(f"skill already exists: {name}")
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(content, encoding="utf-8")


def _import_local(source: Path) -> None:
    source = source.resolve()
    skill_file = _skill_file(source)
    meta = _read_frontmatter(skill_file)
    name = _slug(meta.get("name") or source.stem or source.name)
    if not name:
        raise ValueError("skill name is required")
    target = SKILL_ROOT / name
    if target.exists():
        raise ValueError(f"skill already exists: {name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        target.mkdir()
        shutil.copy2(source, target / "SKILL.md")


def _skill_file(path: Path, missing_ok: bool = False) -> Path | None:
    if path.is_dir():
        candidate = path / "SKILL.md"
    else:
        candidate = path
    if candidate.is_file() and candidate.name == "SKILL.md":
        return candidate
    if missing_ok:
        return None
    raise ValueError(f"not a skill: {path}")


def _read_preview(path: Path) -> str:
    with path.open("rb") as file:
        data = file.read(_MAX_PREVIEW_BYTES + 1)
    return data[:_MAX_PREVIEW_BYTES].decode("utf-8", errors="replace")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", value.strip().casefold()).strip("-_.")


def _tool_command(command: str) -> str:
    found = which(command)
    if found:
        return found
    if os.name == "nt":
        suffix = ".cmd" if command in {"npm", "npx"} else ".exe"
        common = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs" / f"{command}{suffix}"]
        return next((str(path) for path in common if path.exists()), "")
    return ""


def _winget_install(package_id: str, label: str) -> None:
    winget = which("winget")
    if os.name != "nt" or not winget:
        raise ValueError(f"{label} is required. Automatic install currently needs winget on Windows.")
    result = subprocess.run(
        [
            winget, "install", "--id", package_id, "--exact",
            "--accept-package-agreements", "--accept-source-agreements",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or f"{label} install failed")


def _looks_like_markdown(source: str) -> bool:
    lower = source.lower()
    return lower.endswith(".md") or "raw.githubusercontent.com" in lower


def _npx_command() -> str:
    return _tool_command("npx")
