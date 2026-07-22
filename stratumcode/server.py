import asyncio
import json
import logging
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import app_settings, chat, clearify_runtime, lsp, mcp, model_settings, providers, sessions, subagents, workspaces
from .tools import registry


_logger = logging.getLogger(__name__)
_MAX_BODY_SIZE = 1_000_000
_FILE_PREVIEW_LINES = 120
_FILE_PREVIEW_BYTES = 64_000
_IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "__pycache__", ".pytest_cache"}


# -- 路由系统 ---------------------------------------------------------------

def _dispatch(handler, method, body=None):
    """统一路由分发。返回 True 表示已处理，False 表示未匹配。"""
    path = handler.path

    # 1. 精确匹配
    fn = _ROUTES.get((method, path))
    if fn is not None:
        fn(handler, body)
        return True

    # 2. 前缀分组匹配
    for (m, prefix), sub_routes in _PREFIXES.items():
        if m == method and path.startswith(prefix):
            fn = sub_routes.get(path[len(prefix):])
            if fn is not None:
                fn(handler, body)
                return True

    return False


# -- 精确路由 ---------------------------------------------------------------

def _post_app_settings_save(handler, body):
    if "output_language" in body:
        app_settings.save_output_language(body.get("output_language", ""))
    if "font_scale" in body:
        app_settings.save_font_scale(body.get("font_scale"))
    for key in app_settings.ROUND_LIMITS:
        if key in body:
            app_settings.save_round_limit(key, body.get(key))
    handler._json(app_settings.to_json())


_ROUTES: dict[tuple[str, str], object] = {

    # GET
    ("GET", "/api/providers"):       lambda h, b: h._json(providers.list_saved()),
    ("GET", "/api/model-settings"):  lambda h, b: h._json(model_settings.list_all()),
    ("GET", "/api/app-settings"):    lambda h, b: h._json(app_settings.to_json()),
    ("GET", "/api/workspaces"):      lambda h, b: h._json({
        "items": workspaces.list_all(h.workspace_dir),
        "active": workspaces.active(h.workspace_dir),
    }),
    ("GET", "/api/mcp"):             lambda h, b: (mcp.load_enabled(), h._json({"items": mcp.list_all()})),
    ("GET", "/api/files/list"):      lambda h, b: h._handle_file_list(),
    ("GET", "/api/tools"):           lambda h, b: (mcp.load_enabled(), h._json([t.to_json() for t in registry.list_all()])),

    # POST — 独立路由（不在前缀组里的）
    ("POST", "/api/model-settings/save"):   lambda h, b: (model_settings.save(b["stage"], int(b["provider_id"]), b["model_id"]), h._json({"ok": True})),
    ("POST", "/api/model-settings/delete"): lambda h, b: (model_settings.delete(b["stage"]), h._json({"ok": True})),
    ("POST", "/api/app-settings/save"):     _post_app_settings_save,
    ("POST", "/api/workspaces/save"):       lambda h, b: h._json({"id": workspaces.save(b.get("name", ""), b["path"])}),
    ("POST", "/api/workspaces/activate"):   lambda h, b: (workspaces.activate(int(b["id"])), h._json({"ok": True})),
    ("POST", "/api/workspaces/delete"):     lambda h, b: (workspaces.delete(int(b["id"]), h.workspace_dir), h._json({"ok": True})),
    ("POST", "/api/tools/run"):             lambda h, b: h._handle_run(b),
    ("POST", "/api/chat"):                  lambda h, b: h._handle_chat(b),
    ("POST", "/api/chat/answer"):           lambda h, b: h._handle_chat_answer(b),
    ("POST", "/api/subagents/mcp-install"): lambda h, b: h._handle_mcp_install(b),
    ("POST", "/api/files/preview"):         lambda h, b: h._handle_file_preview(b),
}


# -- 前缀分组路由 -----------------------------------------------------------

def _post_provider_models(handler, body):
    provider = providers.get_saved(int(body["provider_id"]))
    if provider is None:
        handler._json({"error": "provider not found"}, 404)
        return
    account_id = ""
    if provider.get("auth_type") == "codex_oauth":
        provider["api_key"], account_id = providers.codex_access_token(provider)
    handler._json({"models": providers.list_models(provider["base_url"], provider["api_key"], account_id)})


def _post_provider_test_model(handler, body):
    if body.get("provider_id"):
        provider = providers.get_saved(int(body["provider_id"]))
        if provider is None:
            handler._json({"error": "provider not found"}, 404)
            return
        if provider.get("auth_type") == "codex_oauth":
            provider["api_key"], _ = providers.codex_access_token(provider)
        ok, msg = providers.test_model(provider["base_url"], provider["api_key"], body["model_id"])
    else:
        ok, msg = providers.test_model(body["base_url"], body["api_key"], body["model_id"])
    handler._json({"ok": ok, "msg": msg})


_POST_PROVIDERS = {
    "save":              lambda h, b: h._json({"id": providers.save(b["name"], b["base_url"], b["api_key"], b.get("pricing_rules"))}),
    "codex-oauth/start": lambda h, b: h._json(providers.start_codex_oauth()),
    "codex-oauth/finish":lambda h, b: h._json(providers.finish_codex_oauth(b["device_auth_id"], b["user_code"])),
    "delete":            lambda h, b: (providers.delete(b["id"]), h._json({"ok": True})),
    "test-connection":   lambda h, b: (t := providers.test_connection(b["base_url"], b["api_key"]), h._json({"ok": t[0], "msg": t[1]}))[1],
    "list-models":       lambda h, b: h._json({"models": providers.list_models(b["base_url"], b["api_key"])}),
    "model-pricing/get": lambda h, b: h._json({"pricing_rules": providers.get_model_pricing(int(b["provider_id"]), b["model_id"])}),
    "model-pricing/save":lambda h, b: (providers.save_model_pricing(int(b["provider_id"]), b["model_id"], b.get("pricing_rules", [])), h._json({"ok": True})),
    "models":            _post_provider_models,
    "test-model":        _post_provider_test_model,
}


_POST_SESSIONS = {
    "list":       lambda h, b: h._json({"items": sessions.list_by_workspace(int(b["workspace_id"]))}),
    "create":     lambda h, b: h._json({"session": sessions.create(int(b["workspace_id"]))}),
    "get":        lambda h, b: h._json({"session": sessions.get(int(b["id"]))}),
    "rename":     lambda h, b: (sessions.rename(int(b["id"]), b["name"]), h._json({"ok": True})),
    "save-state": lambda h, b: (sessions.save_state(int(b["id"]), b.get("state", {})), h._json({"ok": True})),
    "delete":     lambda h, b: (sessions.delete(int(b["id"])), h._json({"ok": True})),
}


_POST_MCP = {
    "save":      lambda h, b: (sid := mcp.save_server(b), h._json({"server": mcp.start_server(sid)})),
    "start":     lambda h, b: h._json({"server": mcp.start_server(int(b["id"]))}),
    "configure": lambda h, b: h._json({"server": mcp.configure(int(b["id"]), b.get("env", {}))}),
    "delete":    lambda h, b: (mcp.delete_server(int(b["id"])), h._json({"ok": True})),
}


_PREFIXES: dict[tuple[str, str], dict[str, object]] = {
    ("POST", "/api/providers/"): _POST_PROVIDERS,
    ("POST", "/api/sessions/"):  _POST_SESSIONS,
    ("POST", "/api/mcp/"):       _POST_MCP,
}


class _StratumThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True


class _Handler(SimpleHTTPRequestHandler):
    """静态文件 + /api/* 路由合一的请求处理器。"""

    def __init__(self, *args, workspace_dir: str, **kwargs):
        self.workspace_dir = workspace_dir
        super().__init__(*args, **kwargs)

    # -- GET / POST ---------------------------------------------------------

    def do_GET(self):
        try:
            if not _dispatch(self, "GET"):
                if self.path.startswith("/api/lsp"):
                    self._handle_lsp_get()
                else:
                    super().do_GET()
        except Exception:
            _logger.exception("GET request failed: %s", self.path)
            try:
                self._json({"error": "request failed"}, 500)
            except OSError:
                pass

    def do_POST(self):
        try:
            body = self._read_body()
        except (json.JSONDecodeError, ValueError) as exc:
            self._json({"error": str(exc)}, 400)
            return

        try:
            if not _dispatch(self, "POST", body):
                if self.path.startswith("/api/lsp"):
                    self._handle_lsp_post(body)
                else:
                    self._json({"error": "not found"}, 404)
        except (KeyError, TypeError, ValueError) as exc:
            try:
                self._json({"error": str(exc)}, 400)
            except OSError:
                pass
        except Exception:
            _logger.exception("POST request failed: %s", self.path)
            try:
                self._json({"error": "request failed"}, 500)
            except OSError:
                pass

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

    # -- 业务 handler -------------------------------------------------------

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

    def _handle_chat_answer(self, body: dict):
        question_id = str(body.get("question_id") or "").strip()
        if not question_id:
            self._json({"error": "question_id is required"}, 400)
            return
        if not clearify_runtime.answer(question_id, body):
            self._json({"error": "unknown clearify question"}, 404)
            return
        self._json({"ok": True})

    def _handle_mcp_install(self, body: dict):
        hint = body.get("hint", "")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for event in subagents.mcp_install_stream(hint, self._workspace_path()):
                self.wfile.write(json.dumps(event, ensure_ascii=False).encode() + b"\n")
                self.wfile.flush()
        except Exception as exc:
            _logger.exception("mcp installer stream failed")
            try:
                self.wfile.write(json.dumps({"op": "error", "message": str(exc)}, ensure_ascii=False).encode() + b"\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _handle_lsp_get(self):
        params = parse_qs(urlparse(self.path).query)
        self._json({
            "items": lsp.list_all(
                language=params.get("language", [None])[0],
                query=params.get("query", [None])[0],
            ),
            "languages": lsp.languages(),
            "mason": lsp.mason_status(),
            "bootstrap": lsp.bootstrap_status(),
        })

    def _handle_lsp_post(self, body: dict):
        action = body.get("action", "")
        name = body.get("name", "").strip()
        if action == "bootstrap_mason":
            self._handle_lsp_bootstrap()
            return
        if action == "install_mason":
            try:
                self._json({"mason": lsp.install_mason()})
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
            return
        if not name:
            self._json({"error": "name is required"}, 400)
            return
        try:
            if action == "install":
                self._json({"server": lsp.install(name)})
            elif action == "uninstall":
                self._json({"server": lsp.uninstall(name)})
            elif action == "enable":
                self._json({"server": lsp.enable(name, bool(body.get("value", True)))})
            elif action == "configure":
                if "executable" in body:
                    self._json({"server": lsp.configure(name, body.get("executable", ""), body.get("args") or [], True)})
                else:
                    self._json({"server": lsp.configure(name, body.get("env", {}))})
            elif action == "probe":
                self._json(lsp.probe(name))
            elif action == "sync":
                self._json(lsp.sync_now())
            else:
                self._json({"error": f"unknown action: {action}"}, 400)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)

    def _handle_lsp_bootstrap(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for event in lsp.bootstrap_mason_events():
                self.wfile.write(json.dumps(event, ensure_ascii=False).encode() + b"\n")
                self.wfile.flush()
        except Exception as exc:
            _logger.exception("lsp bootstrap failed")
            try:
                self.wfile.write(json.dumps({"op": "error", "message": str(exc)}, ensure_ascii=False).encode() + b"\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _handle_file_list(self):
        root = Path(self._workspace_path()).resolve()
        items = []
        for p in root.rglob("*"):
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if any(part.startswith(".") or part in _IGNORED_DIRS for part in rel.parts):
                continue
            items.append({
                "path": rel.as_posix(),
                "type": "dir" if p.is_dir() else "file",
            })
        items.sort(key=lambda x: (0 if x["type"] == "dir" else 1, x["path"].casefold()))
        self._json({"files": items})

    def _handle_file_preview(self, body: dict):
        path = body.get("path")
        if not isinstance(path, str) or not path.strip():
            self._json({"error": "path is required"}, 400)
            return
        tool = registry.get("read")
        try:
            result = asyncio.run(tool.execute(
                {"path": path, "start_line": 1, "end_line": _FILE_PREVIEW_LINES},
                {"directory": self._workspace_path(), "skip_lsp": True},
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

    return _StratumThreadingHTTPServer(("localhost", port), handler)
