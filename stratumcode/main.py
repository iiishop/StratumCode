import os
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

    def select_folder(self) -> str:
        if sys.platform == "darwin":
            return self._select_folder_macos()
        import tkinter as tk
        import tkinter.filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = tkinter.filedialog.askdirectory(title="Select workspace folder")
        root.destroy()
        return path if path else ""

    @staticmethod
    def _select_folder_macos() -> str:
        # uv 管理的 macOS Python 不带 tkinter（python-build-standalone 仅 Windows 含 Tk），
        # 改用 pyobjc 的 NSOpenPanel 选目录。
        try:
            import AppKit
        except ImportError:
            return ""
        panel = AppKit.NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(False)
        panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setTitle_("Select workspace folder")
        if panel.runModal() != AppKit.NSModalResponseOK:
            return ""
        url = panel.URL()
        return str(url.path()) if url else ""


def _ensure_frontend_deps():
    """Ensure frontend dependencies are installed; run npm install when node_modules is missing."""
    if not (FRONTEND_DIR / "node_modules").exists():
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

    webview.create_window("StratumCode", f"http://localhost:{port}", js_api=Api())
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

    webview.create_window("StratumCode", url, js_api=Api())
    webview.start()
