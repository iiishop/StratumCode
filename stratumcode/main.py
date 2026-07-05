import subprocess
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import webview

from .tools import free_port, wait_for

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"


def _launch(url):
    webview.create_window("StratumCode", url)
    webview.start()


def main():
    if not (DIST_DIR / "index.html").exists():
        subprocess.run(
            "npm run build", cwd=str(FRONTEND_DIR), shell=True, check=True,
        )

    server = HTTPServer(
        ("localhost", 0),
        lambda *a, **kw: SimpleHTTPRequestHandler(*a, directory=str(DIST_DIR), **kw),
    )
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    _launch(f"http://localhost:{port}")


def main_dev():
    port = free_port()
    subprocess.Popen(
        f"npm run dev -- --port {port}", cwd=str(FRONTEND_DIR), shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://localhost:{port}"
    wait_for(url, timeout=15)
    _launch(url)
