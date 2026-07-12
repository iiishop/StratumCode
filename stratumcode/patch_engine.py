from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from difflib import unified_diff

from . import patch_authorization, snapshot_store


MAX_FILES = 5
MAX_OPS_PER_FILE = 30
MAX_FILE_BYTES = 1_000_000
MAX_CHANGED_BYTES = 200_000
_workspace_locks: dict[str, threading.RLock] = {}
_workspace_locks_guard = threading.RLock()


class PatchError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FileSnapshot:
    id: str
    path: str
    content_hash: str
    size: int
    mtime_ns: int
    encoding: str
    newline: str
    bom: bool


@dataclass(frozen=True)
class CompiledEdit:
    start: int
    end: int
    replacement: bytes


def snapshot_file(path: Path, root: Path) -> FileSnapshot:
    data = _read_text_bytes(path)
    stat = path.stat()
    content_hash = _hash(data)
    rel_path = str(path.relative_to(root))
    rel_key = rel_path.replace("\\", "/")
    snapshot_id = "FS-" + hashlib.sha256(
        f"{root.resolve()}|{rel_key}|{content_hash}".encode("utf-8")
    ).hexdigest()[:16]
    snapshot = FileSnapshot(
        id=snapshot_id,
        path=rel_path,
        content_hash=content_hash,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        encoding=_encoding(data),
        newline=_newline(data),
        bom=data.startswith(b"\xef\xbb\xbf"),
    )
    snapshot_store.save(snapshot, root)
    return snapshot


def apply_patch_request(request: dict, root: Path) -> dict:
    with _workspace_lock(root):
        files = request.get("files")
        if not isinstance(files, list) or not files:
            raise PatchError("INVALID_REQUEST", "files must be a non-empty array")
        if len(files) > MAX_FILES:
            raise PatchError("PATCH_TOO_LARGE", f"at most {MAX_FILES} files per patch")
        _validate_authorization(request, root, changed_bytes=0)

        prepared = []
        touched_bytes = 0
        for file_patch in files:
            result = _prepare_file(file_patch, root)
            touched_bytes += int(result["touched_bytes"])
            if touched_bytes > MAX_CHANGED_BYTES:
                raise PatchError("PATCH_TOO_LARGE", "changed bytes exceed limit")
            prepared.append(result)
        _validate_authorization(request, root, changed_bytes=touched_bytes)

        tx = _prepare_transaction(request, root, prepared)
        committed: list[dict] = []
        try:
            _set_transaction_state(tx, "committing")
            _precommit_preflight(tx)
            for entry in tx["files"]:
                path = Path(entry["absolute_path"])
                after = Path(entry["after_path"]).read_bytes()
                if entry["mode"] == "create":
                    _atomic_write(path, after)
                else:
                    _atomic_write(path, after, metadata_source=path)
                committed.append(entry)
                _mark_committed(tx, entry["path"])
            _set_transaction_state(tx, "committed")
            patch_authorization.mark_step_applied(
                str(request.get("authorization_id") or ""),
                str(request.get("step_id") or ""),
            )
        except Exception as exc:
            _abort_transaction(tx, committed, exc)

    return {
        "status": "applied",
        "patch_id": str(request.get("patch_id") or ""),
        "authorization_id": str(request.get("authorization_id") or ""),
        "step_id": str(request.get("step_id") or ""),
        "files": [
            {
                "path": str(item["path"].relative_to(root)),
                "before_hash": _hash(item["before"]),
                "after_hash": _hash(item["after"]),
                "operations_applied": item["operations"],
                "removed_bytes": item["removed_bytes"],
                "added_bytes": item["added_bytes"],
                "touched_bytes": item["touched_bytes"],
                "lsp": _lsp_diagnostics(item["path"], root),
            }
            for item in prepared
        ],
        "diff": "\n".join(item["diff"] for item in prepared if item["diff"]),
        "next_state": "validation_required",
        "rollback_id": tx["id"],
    }


def rollback_patch(
    rollback_id: str,
    *,
    root: Path | str | None = None,
    authorization_id: str | None = None,
    force: bool = False,
) -> dict:
    tx = _load_transaction(rollback_id)
    if root is not None and os.path.normcase(str(Path(tx.get("workspace_root") or "").resolve())) != os.path.normcase(str(Path(root).resolve())):
        raise PatchError("ROLLBACK_NOT_AVAILABLE", "rollback belongs to a different workspace")
    if authorization_id and str(tx.get("authorization_id") or "") != str(authorization_id):
        raise PatchError("ROLLBACK_NOT_AVAILABLE", "rollback belongs to a different authorization")
    state = tx.get("state")
    if state == "rolled_back":
        return {"status": "rolled_back", "rollback_id": rollback_id, "files": []}
    if state not in {"committed", "rollback_failed"} and not force:
        raise PatchError("ROLLBACK_NOT_AVAILABLE", f"transaction is not committed: {state}")
    _set_transaction_state(tx, "rolling_back")
    try:
        files = _rollback_entries(tx, reversed(tx.get("files", [])), force=force)
        _set_transaction_state(tx, "rolled_back")
        patch_authorization.mark_step_rolled_back(
            str(tx.get("authorization_id") or ""),
            str(tx.get("step_id") or ""),
        )
        return {"status": "rolled_back", "rollback_id": rollback_id, "files": files}
    except PatchError as exc:
        _set_transaction_state(tx, "rollback_failed", error=f"{exc.code}: {exc.message}")
        raise
    except Exception as exc:
        _set_transaction_state(tx, "rollback_failed", error=str(exc))
        raise PatchError("ROLLBACK_FAILED", str(exc)) from exc


def list_patch_transactions(root: Path | str | None = None, *, limit: int = 50) -> list[dict]:
    base = _transactions_root()
    if not base.is_dir():
        return []
    workspace = str(Path(root).resolve()) if root else None
    items: list[dict] = []
    for journal in base.glob("RB-*/journal.json"):
        try:
            tx = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if workspace and str(Path(tx.get("workspace_root", "")).resolve()) != workspace:
            continue
        items.append({
            "rollback_id": tx.get("id"),
            "state": tx.get("state"),
            "patch_id": tx.get("patch_id"),
            "authorization_id": tx.get("authorization_id"),
            "step_id": tx.get("step_id"),
            "workspace_root": tx.get("workspace_root"),
            "created_at": tx.get("created_at"),
            "updated_at": tx.get("updated_at", tx.get("created_at")),
            "files": [
                {
                    "path": item.get("path"),
                    "mode": item.get("mode"),
                    "before_hash": item.get("before_hash"),
                    "after_hash": item.get("after_hash"),
                    "committed": bool(item.get("committed")),
                }
                for item in tx.get("files", [])
            ],
        })
    items.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
    return items[: max(0, min(limit, 200))]


def _validate_authorization(request: dict, root: Path, *, changed_bytes: int) -> None:
    try:
        patch_authorization.validate_request(request, root, changed_bytes=changed_bytes)
    except patch_authorization.AuthorizationError as exc:
        raise PatchError(exc.code, exc.message) from exc


@contextmanager
def _workspace_lock(root: Path):
    key = os.path.normcase(str(root.resolve()))
    with _workspace_locks_guard:
        lock = _workspace_locks.setdefault(key, threading.RLock())
    with lock:
        yield


def _abort_transaction(tx: dict, committed: list[dict], original_error: Exception) -> None:
    try:
        _rollback_items(tx, committed, force=True)
        _set_transaction_state(tx, "rolled_back", error=str(original_error))
    except Exception as rollback_exc:
        _set_transaction_state(tx, "rollback_failed", error=f"{original_error}; rollback: {rollback_exc}")
        raise PatchError("ROLLBACK_FAILED", str(rollback_exc)) from rollback_exc
    if isinstance(original_error, PatchError):
        raise original_error
    raise PatchError("WRITE_FAILED", str(original_error)) from original_error


def _prepare_file(file_patch: dict, root: Path) -> dict:
    rel = str(file_patch.get("path") or "").strip()
    if not rel:
        raise PatchError("INVALID_REQUEST", "file path is required")
    path = _safe_path(root, rel)
    mode = str(file_patch.get("mode") or "modify")
    if mode == "create":
        return _prepare_create(file_patch, path, root)
    if not path.is_file():
        raise PatchError("FILE_NOT_FOUND", rel)
    before = _read_text_bytes(path)
    snapshot_id = str(file_patch.get("snapshot_id") or "")
    if not snapshot_id:
        raise PatchError("SNAPSHOT_REQUIRED", "modifying an existing file requires snapshot_id from read")
    _validate_snapshot(snapshot_id, rel, path, root)
    operations = file_patch.get("operations")
    if not isinstance(operations, list) or not operations:
        raise PatchError("INVALID_REQUEST", "operations must be a non-empty array")
    if len(operations) > MAX_OPS_PER_FILE:
        raise PatchError("PATCH_TOO_LARGE", f"at most {MAX_OPS_PER_FILE} operations per file")
    bom, body = _split_bom(before)
    encoding = "utf-8"
    text = body.decode(encoding)
    edits = _compile_operations(text, operations, encoding)
    after_body = _apply_edits(body, edits)
    after = bom + after_body
    removed_bytes = sum(edit.end - edit.start for edit in edits)
    added_bytes = sum(len(edit.replacement) for edit in edits)
    return {
        "path": path,
        "rel_path": str(path.relative_to(root)),
        "mode": mode,
        "before": before,
        "after": after,
        "encoding": _encoding(before),
        "operations": len(operations),
        "removed_bytes": removed_bytes,
        "added_bytes": added_bytes,
        "touched_bytes": removed_bytes + added_bytes,
        "diff": _diff(str(path.relative_to(root)), before, after, encoding),
    }


def _validate_snapshot(snapshot_id: str, rel: str, path: Path, root: Path) -> None:
    row = snapshot_store.get(snapshot_id)
    if not row:
        raise PatchError("SNAPSHOT_NOT_FOUND", f"snapshot not found: {snapshot_id}")
    if not snapshot_store.root_matches(row, root):
        raise PatchError("SNAPSHOT_WORKSPACE_MISMATCH", "snapshot belongs to a different workspace")
    if not snapshot_store.path_matches(row, rel):
        raise PatchError("SNAPSHOT_PATH_MISMATCH", "snapshot belongs to a different file")
    current = snapshot_file(path, root)
    if current.content_hash != row["content_hash"]:
        raise PatchError("STALE_SNAPSHOT", "file changed after it was inspected")


def _prepare_create(file_patch: dict, path: Path, root: Path) -> dict:
    if path.exists():
        raise PatchError("FILE_ALREADY_EXISTS", str(path.relative_to(root)))
    content = str(file_patch.get("content") or "")
    after = content.encode("utf-8")
    return {
        "path": path,
        "rel_path": str(path.relative_to(root)),
        "mode": "create",
        "before": b"",
        "after": after,
        "encoding": "utf-8",
        "operations": 1,
        "removed_bytes": 0,
        "added_bytes": len(after),
        "touched_bytes": len(after),
        "diff": _diff(str(path.relative_to(root)), b"", after, "utf-8"),
    }


def _compile_operations(text: str, operations: list[dict], encoding: str) -> list[CompiledEdit]:
    edits: list[CompiledEdit] = []
    newline = "\r\n" if "\r\n" in text else "\n"
    for operation in operations:
        op = operation.get("op")
        expected = int(operation.get("expected_count") or 1)
        if expected != 1:
            raise PatchError("EXPECTED_COUNT_MISMATCH", "expected_count must be 1 in this version")
        if op == "replace_exact":
            old = _normalize_operation_newlines(str(operation.get("old_text") or ""), newline)
            new = _normalize_operation_newlines(str(operation.get("new_text") or ""), newline)
            start, end = (0, 0) if old == "" and text == "" else _unique_span(text, old)
            edits.append(_edit(text, start, end, new, encoding))
        elif op == "delete_exact":
            old = _normalize_operation_newlines(str(operation.get("old_text") or ""), newline)
            start, end = _unique_span(text, old)
            edits.append(_edit(text, start, end, "", encoding))
        elif op in {"insert_before", "insert_after"}:
            anchor = _normalize_operation_newlines(str(operation.get("anchor") or ""), newline)
            start, end = _unique_span(text, anchor)
            offset = start if op == "insert_before" else end
            insert = _normalize_operation_newlines(str(operation.get("text") or ""), newline)
            edits.append(_edit(text, offset, offset, insert, encoding))
        else:
            raise PatchError("INVALID_OPERATION", f"unsupported operation: {op}")
    return _check_overlaps(edits)


def _normalize_operation_newlines(value: str, newline: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)


def _unique_span(text: str, needle: str) -> tuple[int, int]:
    if not needle:
        raise PatchError("ANCHOR_NOT_FOUND", "empty anchor")
    first = text.find(needle)
    if first < 0:
        raise PatchError("ANCHOR_NOT_FOUND", needle[:80])
    if text.find(needle, first + 1) >= 0:
        raise PatchError("ANCHOR_NOT_UNIQUE", needle[:80])
    return first, first + len(needle)


def _edit(text: str, start: int, end: int, replacement: str, encoding: str) -> CompiledEdit:
    return CompiledEdit(
        len(text[:start].encode(encoding)),
        len(text[:end].encode(encoding)),
        replacement.encode(encoding),
    )


def _check_overlaps(edits: list[CompiledEdit]) -> list[CompiledEdit]:
    ordered = sorted(edits, key=lambda item: (item.start, item.end))
    cursor = -1
    for edit in ordered:
        if edit.start < cursor:
            raise PatchError("OVERLAPPING_OPERATIONS", "operations overlap")
        cursor = edit.end
    return ordered


def _apply_edits(original: bytes, edits: list[CompiledEdit]) -> bytes:
    parts = []
    cursor = 0
    for edit in edits:
        parts.append(original[cursor:edit.start])
        parts.append(edit.replacement)
        cursor = edit.end
    parts.append(original[cursor:])
    return b"".join(parts)


def _safe_path(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise PatchError("PATH_OUTSIDE_WORKSPACE", rel)
    if ".git" in path.relative_to(root).parts:
        raise PatchError("PATH_NOT_AUTHORIZED", "refusing to modify .git")
    return path


def _read_text_bytes(path: Path) -> bytes:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise PatchError("PATCH_TOO_LARGE", "file too large")
    data = path.read_bytes()
    if b"\x00" in data:
        raise PatchError("BINARY_FILE_NOT_ALLOWED", "binary file")
    enc = _encoding(data)
    try:
        data.decode(enc)
    except UnicodeDecodeError as exc:
        raise PatchError("ENCODING_UNSUPPORTED", str(exc)) from exc
    return data


def _encoding(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


def _split_bom(data: bytes) -> tuple[bytes, bytes]:
    if data.startswith(b"\xef\xbb\xbf"):
        return b"\xef\xbb\xbf", data[3:]
    return b"", data


def _newline(data: bytes) -> str:
    return "crlf" if b"\r\n" in data else "lf"


def _hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes, *, metadata_source: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if metadata_source and metadata_source.exists():
            shutil.copystat(metadata_source, tmp, follow_symlinks=False)
        os.replace(tmp, path)
    except Exception as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise PatchError("WRITE_FAILED", str(exc)) from exc


def _diff(path: str, before: bytes, after: bytes, encoding: str) -> str:
    old = before.decode(encoding).splitlines(keepends=True)
    new = after.decode(encoding).splitlines(keepends=True)
    return "".join(unified_diff(old, new, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))


def _lsp_diagnostics(path: Path, root: Path) -> dict:
    try:
        from . import lsp

        rel = str(path.relative_to(root))
        status = lsp.touch_file(rel, str(root))
        diagnostics = lsp.diagnostics_for(path)
        return {
            "checked": bool(status.get("checked")),
            "server": status.get("server", ""),
            "error": status.get("error", ""),
            "diagnostics": diagnostics[:20],
            "diagnostic_count": len(diagnostics),
        }
    except Exception as exc:
        return {
            "checked": False,
            "server": "",
            "error": str(exc),
            "diagnostics": [],
            "diagnostic_count": 0,
        }


def _prepare_transaction(request: dict, root: Path, prepared: list[dict]) -> dict:
    tx_id = f"RB-{int(time.time() * 1000)}-{hashlib.sha256(os.urandom(16)).hexdigest()[:8]}"
    directory = _transaction_dir(tx_id)
    backups = directory / "backups"
    afters = directory / "after"
    backups.mkdir(parents=True, exist_ok=False)
    afters.mkdir(parents=True, exist_ok=False)
    now = time.time()
    tx = {
        "id": tx_id,
        "state": "prepared",
        "patch_id": request.get("patch_id", ""),
        "authorization_id": request.get("authorization_id", ""),
        "step_id": request.get("step_id", ""),
        "workspace_root": str(root),
        "request": request,
        "created_at": now,
        "updated_at": now,
        "files": [],
    }
    for index, item in enumerate(prepared):
        backup = None
        if item["mode"] != "create":
            backup = backups / f"{index}.bin"
            _write_bytes_durable(backup, item["before"])
        after = afters / f"{index}.bin"
        _write_bytes_durable(after, item["after"])
        tx["files"].append({
            "path": item["rel_path"],
            "absolute_path": str(item["path"]),
            "mode": item["mode"],
            "backup_path": str(backup) if backup else "",
            "after_path": str(after),
            "before_hash": _hash(item["before"]),
            "after_hash": _hash(item["after"]),
            "committed": False,
        })
    _write_transaction(tx)
    return tx


def _precommit_preflight(tx: dict) -> None:
    for entry in tx["files"]:
        path = Path(entry["absolute_path"])
        if entry.get("mode") == "create":
            if path.exists():
                raise PatchError("FILE_ALREADY_EXISTS", entry["path"])
            continue
        if not path.is_file():
            raise PatchError("STALE_SNAPSHOT", f"file missing before commit: {entry['path']}")
        if _hash(path.read_bytes()) != entry["before_hash"]:
            raise PatchError("STALE_SNAPSHOT", f"file changed before commit: {entry['path']}")


def _transaction_dir(tx_id: str) -> Path:
    return _transactions_root() / tx_id


def _transactions_root() -> Path:
    return Path(os.environ.get("STRATUMCODE_DATA_DIR") or tempfile.gettempdir()) / "stratumcode" / "transactions"


def _transaction_file(tx_id: str) -> Path:
    return _transaction_dir(tx_id) / "journal.json"


def _write_transaction(tx: dict) -> None:
    path = _transaction_file(tx["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    _write_bytes_durable(tmp, json.dumps(tx, ensure_ascii=False, indent=2).encode("utf-8"))
    os.replace(tmp, path)


def _load_transaction(tx_id: str) -> dict:
    if not tx_id or any(part in tx_id for part in ("/", "\\", "..")):
        raise PatchError("ROLLBACK_NOT_FOUND", "invalid rollback id")
    path = _transaction_file(tx_id)
    if not path.is_file():
        raise PatchError("ROLLBACK_NOT_FOUND", tx_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _set_transaction_state(tx: dict, state: str, *, error: str = "") -> None:
    tx["state"] = state
    tx["updated_at"] = time.time()
    if error:
        tx["error"] = error
    _write_transaction(tx)


def _mark_committed(tx: dict, rel_path: str) -> None:
    for item in tx["files"]:
        if item["path"] == rel_path:
            item["committed"] = True
            break
    tx["updated_at"] = time.time()
    _write_transaction(tx)


def _rollback_items(tx: dict, prepared: list[dict], *, force: bool) -> None:
    paths = {item.get("rel_path") or item.get("path") for item in prepared}
    entries = [item for item in tx["files"] if item["path"] in paths]
    _rollback_entries(tx, reversed(entries), force=force)


def _rollback_entries(tx: dict, entries, *, force: bool) -> list[dict]:
    entries = list(entries)
    _preflight_rollback_entries(entries, force=force)
    root = Path(tx.get("workspace_root") or ".")
    results = []
    for entry in entries:
        path = Path(entry["absolute_path"])
        if entry.get("mode") == "create":
            if path.exists():
                path.unlink()
            results.append({"path": entry["path"], "status": "deleted_created_file"})
            continue
        backup = Path(entry["backup_path"])
        _atomic_write(path, backup.read_bytes(), metadata_source=path)
        results.append({
            "path": entry["path"],
            "status": "restored",
            "hash": entry["before_hash"],
            "lsp": _lsp_diagnostics(path, root),
        })
    return results


def _preflight_rollback_entries(entries: list[dict], *, force: bool) -> None:
    for entry in entries:
        path = Path(entry["absolute_path"])
        if entry.get("mode") == "create":
            if path.exists() and not force and _hash(path.read_bytes()) != entry["after_hash"]:
                raise PatchError("ROLLBACK_CONFLICT", entry["path"])
            continue
        if not path.exists():
            raise PatchError("ROLLBACK_CONFLICT", f"missing file: {entry['path']}")
        if not force and _hash(path.read_bytes()) != entry["after_hash"]:
            raise PatchError("ROLLBACK_CONFLICT", entry["path"])
        if not Path(entry["backup_path"]).is_file():
            raise PatchError("ROLLBACK_FAILED", f"missing backup: {entry['path']}")


def _write_bytes_durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
