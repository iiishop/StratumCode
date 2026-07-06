import json
import time
from collections.abc import Iterator
from urllib.request import Request, urlopen

from . import providers


def _start(event_id: str, event_type: str, data: dict) -> dict:
    return {"op": "start", "id": event_id, "event": event_type, "data": data}


def test_stream(message: str, context: list[str], delay: float = 0.04) -> Iterator[dict]:
    """Backend-owned deterministic stream used to exercise the complete chat UI."""
    def pause(multiplier=1):
        if delay:
            time.sleep(delay * multiplier)

    thinking = "test-thinking"
    yield _start(thinking, "thinking", {"text": "", "done": False, "open": True})
    thought = f"Inspecting {', '.join(context) or 'the workspace'} for: {message}"
    for chunk in (thought[index:index + 12] for index in range(0, len(thought), 12)):
        pause()
        yield {"op": "delta", "id": thinking, "field": "text", "value": chunk}
    yield {"op": "update", "id": thinking, "patch": {"done": True}}

    read_id = "test-read"
    pause(2)
    yield _start(read_id, "tool", {
        "name": "read", "description": "Read a selected workspace file.", "symbol": "R",
        "tone": "blue", "status": "running", "open": True,
        "input": json.dumps({"path": "stratumcode/server.py", "start_line": 1, "end_line": 90}, indent=2),
        "output": "",
    })
    pause(4)
    yield {"op": "update", "id": read_id, "patch": {
        "status": "done",
        "output": "Read 90 lines from stratumcode/server.py\nFound the HTTP handler and API routes.",
    }}

    grep_id = "test-grep"
    pause(2)
    yield _start(grep_id, "tool", {
        "name": "grep", "description": "Search workspace text with ripgrep.", "symbol": "/",
        "tone": "red", "status": "running", "open": True,
        "input": json.dumps({"pattern": "/api/tools|registry\\.", "include": "*.py"}, indent=2),
        "output": "",
    })
    pause(4)
    yield {"op": "update", "id": grep_id, "patch": {
        "status": "done",
        "output": "stratumcode/server.py:24  GET /api/tools\nstratumcode/server.py:65  POST /api/tools/run",
    }}

    agent_id = "test-agent"
    pause(2)
    yield _start(agent_id, "subagent", {
        "name": "@explore", "task": "Map the server routes and chat integration points",
        "status": "running", "result": "", "open": True,
    })
    pause(5)
    yield {"op": "update", "id": agent_id, "patch": {
        "status": "done",
        "result": "Found the request handler in stratumcode/server.py and provider calls in stratumcode/providers.py.",
    }}

    pause(2)
    yield _start("test-diff", "diff", {
        "path": "stratumcode/server.py",
        "hunks": [
            {"type": "add", "lines": [
                "+        elif path == \"/api/chat\":",
                "+            self._handle_chat(body)",
            ]},
            {"type": "keep", "lines": ["         else:", "             self._json({\"error\": \"not found\"}, 404)"]},
        ],
        "accepted": None,
    })

    output_id = "test-output"
    pause(2)
    yield _start(output_id, "output", {"content": "", "streaming": True})
    reply = "The backend chat stream is connected. Events now arrive in call order through /api/chat."
    for chunk in (reply[index:index + 8] for index in range(0, len(reply), 8)):
        pause()
        yield {"op": "delta", "id": output_id, "field": "content", "value": chunk}
    yield {"op": "update", "id": output_id, "patch": {"streaming": False}}
    yield {"op": "done"}


def provider_stream(provider_id: int, model: str, message: str, context: list[str]) -> Iterator[dict]:
    """Stream an OpenAI-compatible provider through the same frontend event protocol."""
    provider = providers.get_saved(provider_id)
    if not provider:
        raise ValueError(f"unknown provider: {provider_id}")
    if not model:
        raise ValueError("model is required")

    output_id = "provider-output"
    yield _start(output_id, "output", {"content": "", "streaming": True})
    context_note = f"\n\nWorkspace context: {', '.join(context)}" if context else ""
    request = Request(
        f"{provider['base_url'].rstrip('/')}/v1/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": message + context_note}],
            "stream": True,
        }).encode(),
        headers={
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    with urlopen(request) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            data = json.loads(payload)
            content = data.get("choices", [{}])[0].get("delta", {}).get("content")
            if content:
                yield {"op": "delta", "id": output_id, "field": "content", "value": content}
    yield {"op": "update", "id": output_id, "patch": {"streaming": False}}
    yield {"op": "done"}


def stream(request: dict) -> Iterator[dict]:
    message = request.get("message", "").strip()
    if not message:
        raise ValueError("message is required")
    context = request.get("context", [])
    if not isinstance(context, list) or not all(isinstance(path, str) for path in context):
        raise ValueError("context must be an array of file paths")
    if request.get("mode") == "test":
        return test_stream(message, context)
    return provider_stream(request.get("provider_id"), request.get("model", ""), message, context)
