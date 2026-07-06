import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from stratumcode import chat
from stratumcode.server import create


class ChatProtocolTests(unittest.TestCase):
    def test_backend_test_stream_covers_all_frontend_event_types_in_order(self):
        events = list(chat.test_stream("inspect the server", ["stratumcode/server.py"], delay=0))
        started = [event["event"] for event in events if event["op"] == "start"]

        self.assertEqual(
            started,
            ["thinking", "tool", "tool", "subagent", "diff", "output"],
        )
        read_update = next(event for event in events if event.get("id") == "test-read" and event["op"] == "update")
        grep_update = next(event for event in events if event.get("id") == "test-grep" and event["op"] == "update")
        self.assertIn("class _Handler", read_update["patch"]["output"])
        self.assertIn("server.py", grep_update["patch"]["output"])
        self.assertEqual(events[-1], {"op": "done"})

    def test_chat_endpoint_streams_ndjson(self):
        packets = iter([
            {"op": "start", "id": "output", "event": "output", "data": {"content": "", "streaming": True}},
            {"op": "delta", "id": "output", "field": "content", "value": "hello"},
            {"op": "done"},
        ])

        with tempfile.TemporaryDirectory() as tmp:
            static_root = Path(tmp)
            server = create(static_root, 0, workspace_dir=static_root)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://localhost:{server.server_port}/api/chat",
                    data=json.dumps({"mode": "test", "message": "hello"}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("stratumcode.chat.test_stream", return_value=packets):
                    with urlopen(request) as response:
                        streamed = [json.loads(line) for line in response]
                        content_type = response.headers["Content-Type"]
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(streamed[-1], {"op": "done"})
        self.assertEqual(streamed[1]["value"], "hello")
        self.assertIn("application/x-ndjson", content_type)

    def test_chat_requires_a_message(self):
        with self.assertRaisesRegex(ValueError, "message is required"):
            chat.stream({"mode": "test", "message": "  "})


class FilePreviewTests(unittest.TestCase):
    def test_file_preview_reads_real_workspace_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
            server = create(root, 0, workspace_dir=root)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://localhost:{server.server_port}/api/files/preview",
                    data=json.dumps({"path": "sample.py"}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request) as response:
                    preview = json.load(response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertIn("def hello", preview["content"])
        self.assertEqual(preview["total_lines"], 2)
        self.assertFalse(preview["truncated"])


if __name__ == "__main__":
    unittest.main()
