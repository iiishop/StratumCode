import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import urlopen

import webview

from .server import create as create_server

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"


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

    server = create_server(DIST_DIR)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    webview.create_window("StratumCode", f"http://localhost:{port}")
    webview.start()


def main_dev():
    api_port = _free_port()
    server = create_server(DIST_DIR, api_port)  # API only, 静态文件走 Vite
    threading.Thread(target=server.serve_forever, daemon=True).start()

    env = {**os.environ, "VITE_API_PORT": str(api_port)}
    vite_port = _free_port()
    subprocess.Popen(
        f"npm run dev -- --port {vite_port}", cwd=str(FRONTEND_DIR), shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    url = f"http://localhost:{vite_port}"
    _wait_for(url, timeout=15)

    webview.create_window("StratumCode", url)
    webview.start()
