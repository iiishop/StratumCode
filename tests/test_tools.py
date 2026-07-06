import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stratumcode.mcp import _register_mcp_stub
from stratumcode.server import create
from stratumcode.tools import registry
from stratumcode.tools.builtin import _resolve, _validate_web_url, _webfetch


class ToolSecurityTests(unittest.TestCase):
    def test_resolve_rejects_sibling_with_shared_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            sibling = Path(tmp) / "repo-secret"
            root.mkdir()
            sibling.mkdir()

            with self.assertRaises(PermissionError):
                _resolve("../repo-secret/token.txt", {"directory": str(root)})

    def test_webfetch_rejects_file_urls(self):
        url = Path(__file__).resolve().as_uri()
        result = asyncio.run(_webfetch({"url": url}, {}))

        self.assertTrue(result.title.startswith("[error]"))
        self.assertIn("http and https", result.output)

    def test_webfetch_rejects_loopback(self):
        with self.assertRaises(PermissionError):
            _validate_web_url("http://127.0.0.1/private")

    def test_unimplemented_tools_are_not_exposed(self):
        names = {tool.name for tool in registry.list_all()}

        self.assertNotIn("task", names)
        self.assertNotIn("lsp", names)

    def test_registry_rejects_duplicate_names(self):
        with self.assertRaises(ValueError):
            registry.register(registry.get("read"))


class ToolIntegrationTests(unittest.TestCase):
    def test_server_keeps_static_and_workspace_roots_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            static_root = Path(tmp) / "dist"
            workspace_root = Path(tmp) / "workspace"
            static_root.mkdir()
            workspace_root.mkdir()

            with patch("stratumcode.server.ThreadingHTTPServer") as server_factory:
                create(static_root, 0, workspace_dir=workspace_root)
                handler = server_factory.call_args.args[1]

                with patch("stratumcode.server._Handler") as handler_class:
                    handler("request", "client", "server")

                kwargs = handler_class.call_args.kwargs
                self.assertEqual(kwargs["directory"], str(static_root.resolve()))
                self.assertEqual(kwargs["workspace_dir"], str(workspace_root.resolve()))

    def test_mcp_stub_uses_async_execute_contract(self):
        _register_mcp_stub("audit-test")
        tool = registry.get("mcp_audit-test_ping")

        result = asyncio.run(tool.execute({}, {}))

        self.assertEqual(result.title, "mcp-ping")


if __name__ == "__main__":
    unittest.main()
