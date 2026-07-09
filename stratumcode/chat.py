from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from urllib.request import Request, urlopen
from uuid import uuid4

from . import app_settings, hypothesis_verifier, investigator, model_settings, prompt, providers
from .agent_runtime import (
    MAX_MODEL_OUTPUT_TOKENS,
    call_model as _call_model,
    content_text as _content_text,
    start_event,
    usage_delta as _usage_delta,
)
from .tools import registry

MAX_AGENT_ROUNDS = hypothesis_verifier.MAX_AGENT_ROUNDS
MAX_EMPTY_TOOL_ROUNDS = hypothesis_verifier.MAX_EMPTY_TOOL_ROUNDS
MAX_ANALYZER_ATTEMPTS = 3
TASK_INTENT_TYPES = {"feature", "bugfix", "refactor", "question", "investigation", "other"}
TASK_CERTAINTIES = {"certain", "uncertain", "guess"}
TASK_CLUE_KINDS = {"file", "line", "symbol", "route", "other"}


def analyze_task(message: str, context: list[str], workspace_dir: str) -> dict:
    setting = (
        model_settings.resolve(model_settings.DEFAULT_STAGE)
        or model_settings.resolve(model_settings.EVIDENCE_STAGE)
    )
    if setting is None:
        raise ValueError(
            "No model configured for task analysis. Configure a default or evidence model in Providers."
        )

    provider = setting["provider"]
    model = setting["model_id"]
    last_error = ""
    for _ in range(MAX_ANALYZER_ATTEMPTS):
        assistant = _call_model(
            provider,
            model,
            [
                {"role": "system", "content": prompt.build_task_analyzer(app_settings.get_output_language())},
                {
                    "role": "user",
                    "content": prompt.build_task_analyzer_user(
                        message=message,
                        directory=workspace_dir,
                        context=context,
                        error=last_error,
                    ),
                },
            ],
            tools=[],
        )
        try:
            if assistant.get("tool_calls"):
                raise ValueError("tool calls are not allowed")
            analysis = _validate_task_analysis(_json_object(_content_text(assistant.get("content"))))
            analysis["model"] = model
            analysis["provider"] = provider["name"]
            analysis["suggested_first_tools"] = _suggested_first_tools(analysis)
            analysis["evidence_hypothesis"] = _analysis_hypothesis(message, analysis)
            return analysis
        except ValueError as exc:
            last_error = str(exc)
    raise ValueError(f"task analyzer returned invalid JSON after {MAX_ANALYZER_ATTEMPTS} attempts: {last_error}")


def _json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("response is not a JSON object")
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    return data


def _validate_task_analysis(data: dict) -> dict:
    intent = data.get("intent")
    if not isinstance(intent, dict):
        raise ValueError("intent must be an object")
    intent_type = str(intent.get("type") or "other").strip().casefold()
    summary = str(intent.get("summary") or "").strip()
    if intent_type not in TASK_INTENT_TYPES:
        intent_type = "other"
    if not summary:
        raise ValueError("intent.summary is required")

    return {
        "intent": {"type": intent_type, "summary": summary},
        "constraints": _string_list(data.get("constraints"), "constraints"),
        "hypotheses": _hypotheses(data.get("hypotheses")),
        "clues": _clues(data.get("clues")),
        "unknowns": _string_list(data.get("unknowns"), "unknowns"),
    }


def _string_list(value, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return [str(item).strip() for item in value if str(item).strip()]


def _hypotheses(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("hypotheses must be an array")
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("hypotheses items must be objects")
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        certainty = str(raw.get("certainty") or "uncertain").strip().casefold()
        if certainty not in TASK_CERTAINTIES:
            certainty = "uncertain"
        items.append({"text": text, "certainty": certainty})
    return items


def _clues(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("clues must be an array")
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("clues items must be objects")
        value_text = str(raw.get("value") or raw.get("path") or raw.get("symbol") or "").strip()
        if not value_text:
            continue
        kind = str(raw.get("kind") or "other").strip().casefold()
        if kind not in TASK_CLUE_KINDS:
            kind = "other"
        line = raw.get("line")
        try:
            line = int(line) if line not in (None, "") else None
        except (TypeError, ValueError):
            line = None
        items.append({
            "kind": kind,
            "value": value_text,
            "path": str(raw.get("path") or "").strip(),
            "line": line if line and line > 0 else None,
            "symbol": str(raw.get("symbol") or "").strip(),
            "note": str(raw.get("note") or "").strip(),
        })
    return items


def _analysis_hypothesis(message: str, analysis: dict) -> str:
    if analysis["hypotheses"]:
        return analysis["hypotheses"][0]["text"]
    return f"The workspace contains enough implementation context to satisfy: {analysis['intent']['summary'] or message}"


def _suggested_first_tools(analysis: dict) -> list[dict]:
    calls = []
    seen = set()
    for clue in analysis["clues"]:
        path = clue.get("path") or _path_from_text(clue.get("value", ""))
        if path:
            line = clue.get("line") or _line_from_text(clue.get("value", "")) or 1
            args = {
                "path": path,
                "start_line": max(1, line),
                "end_line": max(80, line + 80),
            }
            call = {"tool": "read", "arguments": args}
        else:
            pattern = clue.get("symbol") or clue.get("value", "")
            if not pattern:
                continue
            call = {"tool": "grep", "arguments": {"pattern": pattern}}
        key = json.dumps(call, sort_keys=True)
        if key not in seen:
            seen.add(key)
            calls.append(call)
        if len(calls) >= 4:
            break
    if not calls:
        calls.append({"tool": "glob", "arguments": {"pattern": "*"}})
    return calls


def _path_from_text(value: str) -> str:
    match = re.search(r"[\w./\\-]+\.[A-Za-z0-9_]+", value or "")
    return match.group(0).replace("\\", "/") if match else ""


def _line_from_text(value: str) -> int | None:
    match = re.search(r"(?:line|L|:)\s*(\d+)", value or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def _analysis_context(analysis: dict) -> list[str]:
    lines = [f"Task intent ({analysis['intent']['type']}): {analysis['intent']['summary']}"]
    lines.extend(f"Constraint: {item}" for item in analysis["constraints"])
    lines.extend(
        f"Assumption to verify ({item['certainty']}): {item['text']}"
        for item in analysis["hypotheses"]
    )
    for clue in analysis["clues"]:
        parts = [clue["kind"], clue["value"]]
        if clue.get("path"):
            parts.append(f"path={clue['path']}")
        if clue.get("line"):
            parts.append(f"line={clue['line']}")
        if clue.get("symbol"):
            parts.append(f"symbol={clue['symbol']}")
        lines.append("Clue to verify: " + " ".join(str(part) for part in parts if part))
    for call in analysis["suggested_first_tools"]:
        lines.append("Suggested first tool call: " + json.dumps(call, ensure_ascii=False))
    lines.extend(f"Initial unknown: {item}" for item in analysis["unknowns"])
    return lines


def analyzed_stream(
    message: str,
    context: list[str],
    workspace_dir: str,
    max_rounds: int | None = None,
    answer: dict | None = None,
) -> Iterator[dict]:
    answer_context = _answer_context(answer)
    if answer_context:
        context = context + answer_context
    analysis = analyze_task(message, context, workspace_dir)
    yield start_event(f"task-analysis-{uuid4().hex[:8]}", "task_analysis", analysis)
    yield from investigator.investigation_stream(
        message=message,
        analysis=analysis,
        context=context + _analysis_context(analysis),
        workspace_dir=workspace_dir,
        max_rounds=max_rounds,
    )


def evidence_stream(
    hypothesis: str,
    context: list[str],
    workspace_dir: str,
    max_rounds: int | None = None,
) -> Iterator[dict]:
    original_call_model = hypothesis_verifier._call_model
    hypothesis_verifier._call_model = _call_model
    try:
        yield from hypothesis_verifier.evidence_stream(
            hypothesis,
            context,
            workspace_dir,
            max_rounds=max_rounds,
        )
    finally:
        hypothesis_verifier._call_model = original_call_model


_discovery_tools = hypothesis_verifier._discovery_tools
_execute_tool = hypothesis_verifier._execute_tool


def _handle_agent_tool(**kwargs):
    events = []
    gen = hypothesis_verifier._handle_agent_tool(**kwargs)
    try:
        while True:
            events.append(next(gen))
    except StopIteration as exc:
        output, concluded, _ = exc.value
    return output, events, concluded


def test_stream(
    message: str,
    context: list[str],
    workspace_dir: str = ".",
    delay: float = 0.04,
) -> Iterator[dict]:
    """Backend-owned deterministic stream used by protocol tests."""
    def pause(multiplier=1):
        if delay:
            time.sleep(delay * multiplier)

    thinking = "test-thinking"
    yield start_event(thinking, "thinking", {"text": "", "done": False, "open": True})
    thought = f"Inspecting {', '.join(context) or 'the workspace'} for: {message}"
    for index in range(0, len(thought), 12):
        pause()
        yield {"op": "delta", "id": thinking, "field": "text", "value": thought[index:index + 12]}
    yield {"op": "update", "id": thinking, "patch": {"done": True}}

    yield start_event("test-analysis", "task_analysis", {
        "intent": {"type": "investigation", "summary": "Inspect the server tools and chat integration points."},
        "constraints": [],
        "hypotheses": [{"text": message, "certainty": "uncertain"}],
        "clues": [{"kind": "file", "value": "stratumcode/server.py", "path": "stratumcode/server.py", "line": 1, "symbol": "", "note": ""}],
        "unknowns": ["Which backend routes connect tool execution to chat streaming."],
        "suggested_first_tools": [{"tool": "read", "arguments": {"path": "stratumcode/server.py", "start_line": 1, "end_line": 90}}],
        "evidence_hypothesis": message,
        "model": "test",
        "provider": "test",
    })

    for call_id, name, params in (
        ("test-read", "read", {"path": "stratumcode/server.py", "start_line": 1, "end_line": 90}),
        ("test-grep", "grep", {"pattern": "/api/tools|registry\\.", "include": "*.py"}),
    ):
        tool = registry.get(name)
        pause(2)
        yield start_event(call_id, "tool", {
            "name": name,
            "description": tool.description,
            "status": "running",
            "open": False,
            "input": json.dumps(params, indent=2),
            "output": "",
        })
        _, result = _execute_tool(name, params, workspace_dir)
        yield {"op": "update", "id": call_id, "patch": {
            "status": "error" if result.title.startswith("[error]") else "done",
            "output": result.output,
            "title": result.title,
            "metadata": result.metadata,
        }}

    yield start_event("test-agent", "subagent", {
        "name": "@explore",
        "task": "Map the server routes and chat integration points",
        "status": "done",
        "result": "Found the request handler and provider integration.",
        "open": True,
    })
    yield start_event("test-diff", "diff", {
        "path": "stratumcode/server.py",
        "hunks": [{"type": "add", "lines": ["+        elif path == \"/api/chat\":"]}],
        "accepted": None,
    })
    yield start_event("test-output", "output", {
        "content": "The backend chat stream is connected.",
        "streaming": False,
    })
    yield {"op": "done"}


def provider_stream(provider_id: int, model: str, message: str, context: list[str]) -> Iterator[dict]:
    """Legacy direct provider stream retained for API compatibility."""
    provider = providers.get_saved(provider_id)
    if not provider:
        raise ValueError(f"unknown provider: {provider_id}")
    output_id = "provider-output"
    started = False
    request = Request(
        f"{provider['base_url'].rstrip('/')}/v1/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": message}],
            "stream": True,
        }).encode(),
        headers={
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
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
                if not started:
                    yield start_event(output_id, "output", {
                        "content": "",
                        "streaming": True,
                    })
                    started = True
                yield {"op": "delta", "id": output_id, "field": "content", "value": content}
    if started:
        yield {"op": "update", "id": output_id, "patch": {"streaming": False}}
    yield {"op": "done"}


def stream(request: dict, workspace_dir: str = ".") -> Iterator[dict]:
    message = request.get("message", "").strip()
    if not message:
        raise ValueError("message is required")
    context = request.get("context", [])
    if not isinstance(context, list) or not all(isinstance(path, str) for path in context):
        raise ValueError("context must be an array of file paths")
    if request.get("mode") == "test":
        return test_stream(message, context, workspace_dir)
    max_rounds = request.get("max_rounds")
    if max_rounds is not None:
        max_rounds = min(50, max(1, int(max_rounds)))
    answer = request.get("answer") if isinstance(request.get("answer"), dict) else None
    if answer and str(answer.get("origin_message") or "").strip():
        message = str(answer["origin_message"]).strip()
    return analyzed_stream(message, context, workspace_dir, max_rounds=max_rounds, answer=answer)


def _answer_context(answer: dict | None) -> list[str]:
    if not answer:
        return []
    question_id = str(answer.get("question_id") or answer.get("unknown_id") or "").strip()
    question = str(answer.get("question") or "").strip()
    selected = str(answer.get("selected_option_id") or "").strip()
    response = str(answer.get("response") or answer.get("text") or "").strip()
    if not response:
        return []
    lines = ["User answered a pending agent question."]
    if question_id:
        lines.append(f"Answered question id: {question_id}")
    if question:
        lines.append(f"Question: {question}")
    if selected:
        lines.append(f"Selected option: {selected}")
    lines.append(f"User answer: {response}")
    return lines
