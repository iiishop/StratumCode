"""Workspace-scoped launch profiles and owned application processes, independent of UI technology."""
from __future__ import annotations

import atexit
import codecs
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

from . import db, terminal_manager

_lock = threading.RLock()
_runs: dict[str, dict] = {}
LOG_LIMIT = 100_000


def _root(workspace):
    return os.path.normcase(str(Path(workspace).resolve()))


def _init(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS application_profiles (id TEXT PRIMARY KEY, workspace TEXT NOT NULL, payload TEXT NOT NULL)")


def profiles(workspace):
    with db.db_session() as conn:
        _init(conn)
        return [json.loads(row[0]) for row in conn.execute(
            "SELECT payload FROM application_profiles WHERE workspace=? ORDER BY id", (_root(workspace),))]


def save(workspace, value):
    if not isinstance(value, dict):
        raise ValueError("profile must be an object")
    p = {key: value.get(key, default) for key, default in {
        "id": "", "name": "", "executable": "", "args": [], "cwd": ".",
        "shell": "none", "command": "", "kind": "service", "visible": False,
        "stdin": False, "ready_text": "", "encoding": "utf-8",
    }.items()}
    if not isinstance(p["name"], str) or not p["name"].strip():
        raise ValueError("A launch name is required")
    if p["shell"] not in {"none", "powershell", "pwsh", "cmd", "bash", "sh"}:
        raise ValueError("Choose an explicit shell or direct executable")
    if p["kind"] not in {"service", "task", "desktop"}:
        raise ValueError("kind must be service, task or desktop")
    if not isinstance(p["args"], list) or any(not isinstance(a, str) for a in p["args"]):
        raise ValueError("args must be an array of strings; no shell quoting")
    for key in ("executable", "command", "cwd", "ready_text", "encoding"):
        if not isinstance(p[key], str) or "\0" in p[key]:
            raise ValueError(f"Invalid {key}")
    if not isinstance(p["visible"], bool) or not isinstance(p["stdin"], bool):
        raise ValueError("visible and stdin must be booleans")
    try:
        codecs.lookup(p["encoding"])
    except LookupError as exc:
        raise ValueError("Unknown output encoding") from exc
    if not isinstance(p["id"], str):
        raise ValueError("Invalid profile id")
    terminal_manager._resolve_cwd(Path(workspace).resolve(), p["cwd"])
    if not (p["executable"] if p["shell"] == "none" else p["command"]).strip():
        raise ValueError("An executable or explicit shell command is required")
    with _lock, db.db_session() as conn:
        _init(conn)
        if p["id"]:
            if not any(x["id"] == p["id"] for x in profiles(workspace)):
                raise ValueError("Unknown profile in this workspace")
        else:
            p["id"] = "app-" + uuid4().hex[:12]
        conn.execute("INSERT OR REPLACE INTO application_profiles VALUES (?, ?, ?)",
                     (p["id"], _root(workspace), json.dumps(p, ensure_ascii=False)))
    return p


def _owned(workspace, run_id):
    run = _runs.get(run_id)
    if not run or run["workspace"] != _root(workspace):
        raise ValueError("Unknown run in this workspace")
    return run


def _snapshot(run, logs=True):
    result = {key: value for key, value in run.items() if key not in {"process", "output", "workspace"}}
    result["output"] = run["output"] if logs else ""
    return result


def snapshot(workspace):
    with _lock:
        return {"workspace": str(Path(workspace).resolve()), "profiles": profiles(workspace),
                "runs": [_snapshot(r, False) for r in reversed(list(_runs.values())) if r["workspace"] == _root(workspace)]}


def read(workspace, run_id, max_output_chars=12_000):
    with _lock:
        result = _snapshot(_owned(workspace, run_id))
        limit = max(0, min(LOG_LIMIT, int(max_output_chars)))
        result["output"] = result["output"][-limit:] if limit else ""
        return result


def start(workspace, profile_id):
    with _lock:
        p = next((p for p in profiles(workspace) if p["id"] == profile_id), None)
        if p is None:
            raise ValueError("Unknown launch profile")
        if any(r["workspace"] == _root(workspace) and r["profile"]["id"] == profile_id
               and r["status"] in {"running", "stopping"} for r in _runs.values()):
            raise ValueError("This profile is already running; stop it before restarting")
        cwd = terminal_manager._resolve_cwd(Path(workspace).resolve(), p["cwd"])
        if p["shell"] == "none":
            exe = p["executable"]
            if "/" in exe or "\\" in exe or (cwd / exe).is_file():
                exe = str((cwd / exe).resolve())
            resolved = shutil.which(exe) or exe
            if os.name == "nt" and Path(resolved).suffix.lower() in {".cmd", ".bat"}:
                raise ValueError("Batch scripts require explicit CMD mode; direct mode does not infer a shell")
            argv, use_shell = [exe, *p["args"]], False
        else:
            argv, _, use_shell = terminal_manager._command_spec(p["command"], p["shell"])
        run = {"id": "run-" + uuid4().hex[:12], "profile": p, "workspace": _root(workspace),
               "status": "running", "readiness": "unverified", "readiness_note": "",
               "started_at": time.time(), "ended_at": None, "pid": None, "exit_code": None,
               "output": "", "dropped_chars": 0, "error": "", "process": None}
        _runs[run["id"]] = run
        try:
            flags = 0
            startupinfo = None
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP
                if not p["visible"]:
                    flags |= subprocess.CREATE_NO_WINDOW
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE
            proc = subprocess.Popen(argv, cwd=cwd, shell=use_shell, creationflags=flags,
                                    startupinfo=startupinfo,
                                    start_new_session=os.name != "nt", stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, stdin=subprocess.PIPE if p["stdin"] else subprocess.DEVNULL)
            run.update(process=proc, pid=proc.pid)
        except OSError as exc:
            run.update(status="failed", error=str(exc), ended_at=time.time())
            return _snapshot(run)
        threading.Thread(target=_collect, args=(run,), daemon=True).start()
        threading.Thread(target=_wait, args=(run,), daemon=True).start()
        return _snapshot(run)


def _collect(run):
    decoder = codecs.getincrementaldecoder(run["profile"]["encoding"])(errors="replace")
    stream = run["process"].stdout
    try:
        while True:
            raw = stream.read1(4096)
            text = decoder.decode(raw, final=not raw)
            with _lock:
                run["output"] += text
                marker = run["profile"]["ready_text"]
                if marker and marker in run["output"] and run["status"] == "running":
                    run.update(readiness="log_matched", readiness_note="Configured startup text observed; not functional validation")
                extra = max(0, len(run["output"]) - LOG_LIMIT)
                run["output"] = run["output"][-LOG_LIMIT:]
                run["dropped_chars"] += extra
            if not raw:
                break
    finally:
        stream.close()


def _wait(run):
    code = run["process"].wait()
    with _lock:
        stopped = run["status"] in {"stopping", "stopped"}
        status = "stopped" if stopped else ("succeeded" if code == 0 and run["profile"]["kind"] == "task" else "exited" if code == 0 else "failed")
        run.update(status=status, exit_code=code, ended_at=time.time(), readiness="not_running")


def stop(workspace, run_id):
    with _lock:
        run = _owned(workspace, run_id)
        if run["status"] not in {"running", "stopping"}:
            return _snapshot(run)
        run["status"] = "stopping"
        proc = run["process"]
    # Only processes created by this manager can reach this operation.
    terminal_manager._kill_process_tree(proc)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        raise ValueError("Process did not stop; restart was not attempted")
    with _lock:
        if run["status"] == "stopping":
            run.update(status="stopped", exit_code=proc.returncode, ended_at=time.time(), readiness="not_running")
        return _snapshot(run)


def action(workspace, params):
    operation = params.get("action")
    if operation == "list":
        return snapshot(workspace)
    if operation == "save":
        return save(workspace, params.get("profile"))
    if operation == "start":
        return start(workspace, params.get("profile_id"))
    if operation == "read":
        return read(workspace, params.get("run_id"), params.get("max_output_chars", 12_000))
    if operation == "delete":
        profile_id = params.get("profile_id")
        with _lock, db.db_session() as conn:
            _init(conn)
            if any(r["workspace"] == _root(workspace) and r["profile"]["id"] == profile_id
                   and r["status"] in {"running", "stopping"} for r in _runs.values()):
                raise ValueError("Stop this profile's process before deleting it")
            conn.execute("DELETE FROM application_profiles WHERE workspace=? AND id=?", (_root(workspace), profile_id))
        return {"deleted": profile_id}
    if operation in {"stop", "restart"}:
        old = stop(workspace, params.get("run_id"))
        return start(workspace, old["profile"]["id"]) if operation == "restart" else old
    with _lock:
        run = _owned(workspace, params.get("run_id"))
        if run["status"] != "running":
            raise ValueError("The application is not running")
        if operation == "confirm_ready":
            note = str(params.get("note") or "").strip()
            if not note:
                raise ValueError("Describe the observed readiness evidence")
            run.update(readiness="confirmed", readiness_note=note)
            return _snapshot(run)
        if operation == "input":
            if not run["profile"]["stdin"]:
                raise ValueError("Standard input was not enabled for this launch")
            text = str(params.get("text") or "")
            if len(text) > 4096:
                raise ValueError("Input is limited to 4096 characters")
            if run.get("input_pending"):
                raise ValueError("Previous input is still pending; the application may not be reading stdin")
            if run["process"].stdin.closed:
                raise ValueError("Standard input is already closed")
            run["input_pending"] = True
            threading.Thread(target=_send_input, args=(run, text, bool(params.get("eof"))), daemon=True).start()
            return _snapshot(run)
    raise ValueError("Unknown application action")


def _send_input(run, text, eof):
    # A program that does not read stdin must not block status or stop requests.
    try:
        stream = run["process"].stdin
        if eof:
            stream.close()
        else:
            stream.write(text.encode(run["profile"]["encoding"]))
            stream.flush()
    except (OSError, ValueError) as exc:
        with _lock:
            run["error"] = f"Input unavailable: {exc}"
    finally:
        with _lock:
            run["input_pending"] = False


def shutdown():
    for run in list(_runs.values()):
        if run["status"] in {"running", "stopping"}:
            try:
                stop(run["workspace"], run["id"])
            except (OSError, ValueError):
                pass


atexit.register(shutdown)
