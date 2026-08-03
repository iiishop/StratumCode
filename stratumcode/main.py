import os
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import urlopen

import webview

from . import workspaces
from .server import create as create_server

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
WORKSPACE_DIR = FRONTEND_DIR.parent



class Api:
    """Expose native OS dialogs to the webview frontend."""

    def __init__(self):
        self._window = None

    def set_window(self, window):
        self._window = window

    def select_folder(self) -> str:
        """原生目录选择对话框（pywebview 内部在主线程调度，跨平台安全）。

        macOS 直接用 AppKit NSOpenPanel 会崩在
        "NSWindow should only be instantiated on the main thread"——
        JS bridge 调用跑在后台线程，AppKit 要求主线程操作 UI。
        pywebview 的 create_file_dialog 自己处理线程调度，一劳永逸。
        """
        window = self._window
        if window is None:
            return ""
        result = window.create_file_dialog(webview.FileDialog.FOLDER)
        return str(result[0]) if result else ""


def _frontend_deps_installed() -> bool:
    """Check that every dependency declared in package.json exists in node_modules."""
    pkg_path = FRONTEND_DIR / "package.json"
    if not pkg_path.exists():
        return False
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    if not deps:
        return False
    for name in deps:
        if not (FRONTEND_DIR / "node_modules" / name).exists():
            return False
    return True


def _ensure_frontend_deps():
    """Ensure frontend dependencies are installed; run npm install when node_modules is missing or incomplete."""
    if not _frontend_deps_installed():
        # shell=True 仅用于固定字符串 "npm install"（无任何拼接输入），无注入风险
        subprocess.run("npm install", cwd=str(FRONTEND_DIR), shell=True, check=True)


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for(url, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urlopen(url)
            return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError(
        f"Frontend dev server did not start within {timeout}s ({url}). "
        f"Check frontend/vite.err.log for details."
    )


def main():
    _ensure_frontend_deps()
    # 生产模式：每次启动都重新 build，避免 dist 陈旧
    subprocess.run("npm run build", cwd=str(FRONTEND_DIR), shell=True, check=True)

    workspaces.reconcile(str(WORKSPACE_DIR))
    workspace = workspaces.active(str(WORKSPACE_DIR))["path"]
    server = create_server(DIST_DIR, workspace_dir=workspace)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    api = Api()
    window = webview.create_window("StratumCode", f"http://localhost:{port}", js_api=api)
    api.set_window(window)
    webview.start()


def main_dev():
    _ensure_frontend_deps()
    api_port = _free_port()
    workspaces.reconcile(str(WORKSPACE_DIR))
    workspace = workspaces.active(str(WORKSPACE_DIR))["path"]
    server = create_server(DIST_DIR, api_port, workspace_dir=workspace)  # API only, 静态文件走 Vite
    threading.Thread(target=server.serve_forever, daemon=True).start()

    env = {**os.environ, "VITE_API_PORT": str(api_port)}
    vite_port = _free_port()
    subprocess.Popen(
        f"npm run dev -- --port {vite_port}", cwd=str(FRONTEND_DIR), shell=True,
        stdout=open(FRONTEND_DIR / "vite.out.log", "w"),
        stderr=open(FRONTEND_DIR / "vite.err.log", "w"),
        env=env,
    )
    url = f"http://localhost:{vite_port}"
    _wait_for(url, timeout=15)

    api = Api()
    window = webview.create_window("StratumCode", url, js_api=api)
    api.set_window(window)
    webview.start()
