import json
from http.server import HTTPServer, SimpleHTTPRequestHandler

from . import providers


class _Handler(SimpleHTTPRequestHandler):
    """静态文件 + /api/* 路由合一的请求处理器。"""

    def do_GET(self):
        if self.path == "/api/providers":
            self._json(providers.list_saved())
        else:
            super().do_GET()

    def do_POST(self):
        path = self.path
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))

        if path == "/api/providers/save":
            pid = providers.save(body["name"], body["base_url"], body["api_key"])
            self._json({"id": pid})
        elif path == "/api/providers/delete":
            providers.delete(body["id"])
            self._json({"ok": True})
        elif path == "/api/providers/test-connection":
            ok, msg = providers.test_connection(body["base_url"], body["api_key"])
            self._json({"ok": ok, "msg": msg})
        elif path == "/api/providers/list-models":
            models = providers.list_models(body["base_url"], body["api_key"])
            self._json({"models": models})
        elif path == "/api/providers/test-model":
            ok, msg = providers.test_model(body["base_url"], body["api_key"], body["model_id"])
            self._json({"ok": ok, "msg": msg})
        else:
            self._json({"error": "not found"}, 404)

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


def create(static_dir: str, port: int = 0) -> HTTPServer:
    handler = lambda *a, **kw: _Handler(*a, directory=str(static_dir), **kw)
    return HTTPServer(("localhost", port), handler)
