import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import urlopen
import tkinter.filedialog

import webview

from . import workspaces
from .server import create as create_server

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
WORKSPACE_DIR = FRONTEND_DIR.parent



class Api:
    """Expose native OS dialogs to the webview frontend."""

    def select_folder(self) -> str:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = tkinter.filedialog.askdirectory(title="Select workspace folder")
        root.destroy()
        return path if path else ""
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


def main():
    if not (DIST_DIR / "index.html").exists():
        subprocess.run(
            "npm run build", cwd=str(FRONTEND_DIR), shell=True, check=True,
        )

    workspaces.reconcile(str(WORKSPACE_DIR))
    workspace = workspaces.active(str(WORKSPACE_DIR))["path"]
    server = create_server(DIST_DIR, workspace_dir=workspace)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    webview.create_window("StratumCode", f"http://localhost:{port}", js_api=Api())
    webview.start()


def main_dev():
    api_port = _free_port()
    workspaces.reconcile(str(WORKSPACE_DIR))
    workspace = workspaces.active(str(WORKSPACE_DIR))["path"]
    server = create_server(DIST_DIR, api_port, workspace_dir=workspace)  # API only, 静态文件走 Vite
    threading.Thread(target=server.serve_forever, daemon=True).start()

    env = {**os.environ, "VITE_API_PORT": str(api_port)}
    vite_port = _free_port()
    subprocess.Popen(
        f"npm run dev -- --port {vite_port}", cwd=str(FRONTEND_DIR), shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    url = f"http://localhost:{vite_port}"
    _wait_for(url, timeout=15)

    webview.create_window("StratumCode", url, js_api=Api())
    webview.start()
