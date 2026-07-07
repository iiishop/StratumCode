import asyncio
import json
import logging
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import chat, model_settings, providers, workspaces
from .tools import registry


_logger = logging.getLogger(__name__)
_MAX_BODY_SIZE = 1_000_000
_FILE_PREVIEW_LINES = 120
_FILE_PREVIEW_BYTES = 64_000


class _Handler(SimpleHTTPRequestHandler):
    """静态文件 + /api/* 路由合一的请求处理器。"""

    def __init__(self, *args, workspace_dir: str, **kwargs):
        self.workspace_dir = workspace_dir
        super().__init__(*args, **kwargs)

    def do_GET(self):
        try:
            self._dispatch_get()
        except Exception:
            _logger.exception("GET request failed: %s", self.path)
            self._json({"error": "request failed"}, 500)

    def _dispatch_get(self):
        if self.path == "/api/providers":
            self._json(providers.list_saved())
        elif self.path == "/api/model-settings":
            self._json(model_settings.list_all())
        elif self.path == "/api/workspaces":
            self._json({
                "items": workspaces.list_all(self.workspace_dir),
                "active": workspaces.active(self.workspace_dir),
            })
        elif self.path == "/api/tools":
            self._json([t.to_json() for t in registry.list_all()])
        else:
            super().do_GET()

    def do_POST(self):
        try:
            self._dispatch_post()
        except (KeyError, TypeError, ValueError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception:
            _logger.exception("POST request failed: %s", self.path)
            self._json({"error": "request failed"}, 500)

    def _dispatch_post(self):
        path = self.path
        try:
            body = self._read_body()
        except (json.JSONDecodeError, ValueError) as exc:
            self._json({"error": str(exc)}, 400)
            return

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
        elif path == "/api/providers/models":
            provider = providers.get_saved(int(body["provider_id"]))
            if provider is None:
                self._json({"error": "provider not found"}, 404)
                return
            self._json({"models": providers.list_models(provider["base_url"], provider["api_key"])})
        elif path == "/api/providers/test-model":
            ok, msg = providers.test_model(body["base_url"], body["api_key"], body["model_id"])
            self._json({"ok": ok, "msg": msg})
        elif path == "/api/model-settings/save":
            model_settings.save(
                body["stage"], int(body["provider_id"]), body["model_id"]
            )
            self._json({"ok": True})
        elif path == "/api/model-settings/delete":
            model_settings.delete(body["stage"])
            self._json({"ok": True})
        elif path == "/api/workspaces/save":
            workspace_id = workspaces.save(body.get("name", ""), body["path"])
            self._json({"id": workspace_id})
        elif path == "/api/workspaces/activate":
            workspaces.activate(int(body["id"]))
            self._json({"ok": True})
        elif path == "/api/workspaces/delete":
            workspaces.delete(int(body["id"]), self.workspace_dir)
            self._json({"ok": True})

        elif path == "/api/tools/run":
            self._handle_run(body)
        elif path == "/api/chat":
            self._handle_chat(body)
        elif path == "/api/files/preview":
            self._handle_file_preview(body)

        else:
            self._json({"error": "not found"}, 404)

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > _MAX_BODY_SIZE:
            raise ValueError(f"request body must be at most {_MAX_BODY_SIZE} bytes")
        if not length:
            return {}
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return body

    def _handle_run(self, body: dict):
        tool_name = body.get("tool", "")
        params = body.get("params", {})
        tool = registry.get(tool_name)
        if not tool:
            self._json({"error": f"unknown tool: {tool_name}"}, 404)
            return

        ctx = {"directory": self._workspace_path()}
        try:
            result = asyncio.run(tool.execute(params, ctx))
            self._json({"title": result.title, "output": result.output, "metadata": result.metadata})
        except Exception:
            _logger.exception("tool execution failed: %s", tool_name)
            self._json({"title": f"error: {tool_name}", "output": "tool execution failed", "metadata": {}}, 500)

    def _handle_chat(self, body: dict):
        try:
            events = chat.stream(body, self._workspace_path())
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for event in events:
                self.wfile.write(json.dumps(event, ensure_ascii=False).encode() + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            _logger.exception("chat stream failed")
            error = {"op": "error", "message": str(exc)}
            try:
                self.wfile.write(json.dumps(error, ensure_ascii=False).encode() + b"\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _handle_file_preview(self, body: dict):
        path = body.get("path")
        if not isinstance(path, str) or not path.strip():
            self._json({"error": "path is required"}, 400)
            return
        tool = registry.get("read")
        try:
            result = asyncio.run(tool.execute(
                {"path": path, "start_line": 1, "end_line": _FILE_PREVIEW_LINES},
                {"directory": self._workspace_path()},
            ))
        except PermissionError as exc:
            self._json({"error": str(exc)}, 403)
            return
        if result.title.startswith("[error]"):
            self._json({"error": result.output}, 404)
            return

        content = result.output
        encoded = content.encode("utf-8")
        truncated_bytes = len(encoded) > _FILE_PREVIEW_BYTES
        if truncated_bytes:
            content = encoded[:_FILE_PREVIEW_BYTES].decode("utf-8", errors="ignore")
        total_lines = result.metadata.get("total_lines", len(content.splitlines()))
        self._json({
            "path": path,
            "content": content,
            "total_lines": total_lines,
            "shown_lines": min(total_lines, _FILE_PREVIEW_LINES),
            "truncated": truncated_bytes or total_lines > _FILE_PREVIEW_LINES,
        })

    def _workspace_path(self) -> str:
        return workspaces.active(self.workspace_dir)["path"]

    def _json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def create(static_dir: str, port: int = 0, workspace_dir: str | None = None) -> ThreadingHTTPServer:
    static_root = Path(static_dir).resolve()
    workspace_root = Path(workspace_dir or static_root).resolve()
    workspaces.activate_path(str(workspace_root))

    def handler(*args, **kwargs):
        return _Handler(
            *args,
            directory=str(static_root),
            workspace_dir=str(workspace_root),
            **kwargs,
        )

    return ThreadingHTTPServer(("localhost", port), handler)
