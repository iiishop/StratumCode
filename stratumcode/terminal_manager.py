from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

DEFAULT_TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 200_000
KILL_GRACE_SECONDS = 1.5
RUNNING = "running"
EXITED = "exited"
FAILED = "failed"
TERMINATED = "terminated"
TIMEOUT = "timeout"
SHELLS = {"auto", "cmd", "powershell", "pwsh", "bash", "git_bash", "sh"}


@dataclass
class TerminalSession:
    id: str
    command: str
    shell: str
    resolved_shell: str
    cwd: Path
    workspace: Path
    background: bool
    process: subprocess.Popen | None = None
    status: str = RUNNING
    exit_code: int | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    output: str = ""
    dropped_chars: int = 0
    error: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)

    def append(self, chunk: str, limit: int) -> None:
        if not chunk:
            return
        with self.lock:
            self.output += chunk
            extra = len(self.output) - limit
            if extra > 0:
                self.output = self.output[extra:]
                self.dropped_chars += extra

    def finish(self, status: str, exit_code: int | None = None, error: str = "") -> None:
        with self.lock:
            if self.status != RUNNING and status != TERMINATED:
                return
            self.status = status
            self.exit_code = exit_code
            self.error = error or self.error
            self.ended_at = self.ended_at or time.time()

    def snapshot(self, *, output_chars: int = 12_000) -> dict:
        with self.lock:
            pid = self.process.pid if self.process else None
            now = self.ended_at or time.time()
            output = self.output[-max(0, output_chars):] if output_chars else ""
            cwd = _relative_or_absolute(self.cwd, self.workspace)
            return {
                "session_id": self.id,
                "command": self.command,
                "shell": self.shell,
                "resolved_shell": self.resolved_shell,
                "cwd": cwd,
                "pid": pid,
                "background": self.background,
                "status": self.status,
                "exit_code": self.exit_code,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "duration_ms": int((now - self.started_at) * 1000),
                "output": output,
                "output_chars": len(self.output),
                "dropped_chars": self.dropped_chars,
                "error": self.error,
            }


_sessions: dict[str, TerminalSession] = {}
_sessions_lock = threading.RLock()


def start(
    command: str,
    *,
    workspace: str | Path,
    cwd: str = "",
    shell: str = "auto",
    background: bool = False,
    timeout_seconds: float | None = None,
    max_output_chars: int = MAX_OUTPUT_CHARS,
) -> dict:
    command = str(command or "").strip()
    if not command:
        raise ValueError("terminal command is required")
    workspace_path = Path(workspace).resolve()
    cwd_path = _resolve_cwd(workspace_path, cwd)
    argv, resolved_shell, use_shell = _command_spec(command, shell)
    session = TerminalSession(
        id=f"term-{uuid4().hex[:10]}",
        command=command,
        shell=_normalize_shell(shell),
        resolved_shell=resolved_shell,
        cwd=cwd_path,
        workspace=workspace_path,
        background=background,
    )
    with _sessions_lock:
        _sessions[session.id] = session
    try:
        session.process = subprocess.Popen(
            argv,
            cwd=str(cwd_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=use_shell,
            creationflags=_creationflags(),
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        session.finish(FAILED, error=str(exc))
        return session.snapshot()
    threading.Thread(target=_read_output, args=(session, max_output_chars), daemon=True).start()
    if background:
        threading.Thread(target=_wait_background, args=(session,), daemon=True).start()
        return session.snapshot()
    _wait_blocking(session, timeout_seconds)
    return session.snapshot(output_chars=max_output_chars)


def list_sessions(*, include_finished: bool = True, background_only: bool = False) -> list[dict]:
    with _sessions_lock:
        items = list(_sessions.values())
    if background_only:
        items = [item for item in items if item.background]
    if not include_finished:
        items = [item for item in items if item.status == RUNNING]
    return sorted(
        (item.snapshot() for item in items),
        key=lambda item: (item["status"] != RUNNING, -float(item["started_at"] or 0)),
    )


def poll(session_id: str) -> dict:
    return _session(session_id).snapshot()


def wait(session_id: str, timeout_seconds: float | None = None) -> dict:
    session = _session(session_id)
    proc = session.process
    if proc is not None and session.status == RUNNING:
        try:
            exit_code = proc.wait(timeout=float(timeout_seconds) if timeout_seconds else None)
            if session.status == RUNNING:
                session.finish(EXITED if exit_code == 0 else FAILED, exit_code)
        except subprocess.TimeoutExpired:
            pass
    return session.snapshot()


def read(session_id: str, *, max_output_chars: int = 12_000) -> dict:
    session = _session(session_id)
    return session.snapshot(output_chars=max_output_chars)


def terminate(session_id: str, *, reason: str = "model") -> dict:
    session = _session(session_id)
    proc = session.process
    if proc is None or session.status != RUNNING:
        return session.snapshot()
    _kill_process_tree(proc)
    session.finish(TERMINATED, proc.poll(), reason)
    return session.snapshot()


def _wait_background(session: TerminalSession) -> None:
    proc = session.process
    if proc is None:
        return
    exit_code = proc.wait()
    if session.status == RUNNING:
        session.finish(EXITED if exit_code == 0 else FAILED, exit_code)


def _wait_blocking(session: TerminalSession, timeout_seconds: float | None) -> None:
    proc = session.process
    if proc is None:
        return
    timeout = DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else float(timeout_seconds)
    try:
        exit_code = proc.wait(timeout=timeout)
        session.finish(EXITED if exit_code == 0 else FAILED, exit_code)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        session.finish(TIMEOUT, proc.poll(), f"timeout after {timeout:g}s")


def _read_output(session: TerminalSession, max_output_chars: int) -> None:
    proc = session.process
    if proc is None or proc.stdout is None:
        return
    while True:
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        session.append(chunk, max_output_chars)


def _session(session_id: str) -> TerminalSession:
    with _sessions_lock:
        session = _sessions.get(str(session_id or ""))
    if session is None:
        raise ValueError(f"unknown terminal session: {session_id}")
    return session


def _resolve_cwd(workspace: Path, cwd: str) -> Path:
    path = (workspace / (cwd or ".")).resolve()
    if not path.is_relative_to(workspace):
        raise PermissionError(f"terminal cwd escapes workspace: {cwd}")
    if not path.is_dir():
        raise NotADirectoryError(f"terminal cwd is not a directory: {cwd or '.'}")
    return path


def _normalize_shell(shell: str) -> str:
    value = str(shell or "auto").strip().casefold()
    if value not in SHELLS:
        raise ValueError(f"unsupported shell: {shell}")
    return value


def _command_spec(command: str, shell: str) -> tuple[list[str] | str, str, bool]:
    shell_name = _normalize_shell(shell)
    if os.name == "nt":
        return _windows_argv(command, shell_name)
    return _posix_argv(command, shell_name)


def _windows_argv(command: str, shell: str) -> tuple[list[str] | str, str, bool]:
    if shell == "auto":
        shell = "powershell" if shutil.which("powershell") else "cmd"
    if shell == "cmd":
        return command, "cmd", True
    if shell == "powershell":
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command], "powershell", False
    if shell == "pwsh":
        exe = shutil.which("pwsh")
        if not exe:
            raise FileNotFoundError("pwsh not found")
        return [exe, "-NoProfile", "-Command", command], "pwsh", False
    if shell in {"bash", "git_bash", "sh"}:
        bash = _find_windows_bash()
        if not bash:
            raise FileNotFoundError("bash not found")
        return [bash, "-lc", command], "bash", False
    raise ValueError(f"unsupported shell: {shell}")


def _posix_argv(command: str, shell: str) -> tuple[list[str] | str, str, bool]:
    if shell == "auto":
        shell = "bash" if shutil.which("bash") else "sh"
    if shell in {"bash", "git_bash"}:
        exe = shutil.which("bash")
        if not exe:
            raise FileNotFoundError("bash not found")
        return [exe, "-lc", command], "bash", False
    if shell == "sh":
        return [shutil.which("sh") or "/bin/sh", "-c", command], "sh", False
    if shell in {"powershell", "pwsh"}:
        exe = shutil.which("pwsh") or shutil.which("powershell")
        if not exe:
            raise FileNotFoundError(f"{shell} not found")
        return [exe, "-NoProfile", "-Command", command], shell, False
    raise ValueError(f"unsupported shell on this platform: {shell}")


def _find_windows_bash() -> str:
    for candidate in (
        Path(os.environ.get("ProgramFiles", "")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Git" / "bin" / "bash.exe",
        shutil.which("bash"),
    ):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return ""


def _creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _kill_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            return
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=KILL_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix() or "."
    except ValueError:
        return str(path)
