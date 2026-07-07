from __future__ import annotations

import asyncio
import json
import platform
import re
import time
from collections.abc import Iterator
from dataclasses import asdict
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from . import model_settings, prompt, providers
from .agent import EvidencePolicy, EvidenceRun, RunState
from .agent.policy import DISCOVERY_TOOLS, EvidencePhase
from .agent.tools import agent_tools
from .tools import registry

MAX_AGENT_ROUNDS = 14
MAX_EMPTY_TOOL_ROUNDS = 2


def _start(event_id: str, event_type: str, data: dict) -> dict:
    return {"op": "start", "id": event_id, "event": event_type, "data": data}


def _execute_tool(name: str, params: dict, workspace_dir: str):
    tool = registry.get(name)
    if not tool:
        raise ValueError(f"unknown tool: {name}")
    return tool, asyncio.run(tool.execute(params, {"directory": workspace_dir}))


def _call_model(
    provider: dict,
    model: str,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools or agent_tools(DISCOVERY_TOOLS),
        "temperature": 0.1,
    }
    request = Request(
        f"{provider['base_url'].rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=90) as response:
            data = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise ValueError(f"provider request failed ({exc.code}): {detail}") from exc
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0].get("message"), dict):
        raise ValueError("provider returned no assistant message")
    return choices[0]["message"]


def _content_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    return ""


def evidence_stream(
    hypothesis: str,
    context: list[str],
    workspace_dir: str,
    max_rounds: int | None = None,
) -> Iterator[dict]:
    setting = model_settings.resolve(model_settings.EVIDENCE_STAGE)
    if setting is None:
        raise ValueError(
            "No model configured for evidence gathering. Configure a default or evidence model in Providers."
        )

    provider = setting["provider"]
    model = setting["model_id"]
    run = EvidenceRun(hypothesis)
    run.transition(RunState.GATHERING)
    policy = EvidencePolicy(max_rounds=max_rounds or MAX_AGENT_ROUNDS)
    run_id = uuid4().hex[:10]
    stage_id = f"{run_id}-stage"
    hypothesis_id = f"{run_id}-hypothesis"
    yield _start(stage_id, "stage", {
        "name": "evidence",
        "label": "Gather evidence",
        "state": run.state.value,
        "phase": policy.phase.value,
        "model": model,
        "provider": provider["name"],
        "inherited": setting["inherited"],
    })
    yield _start(hypothesis_id, "hypothesis", {
        "text": hypothesis,
        "confidence": run.confidence,
        "status": run.state.value,
    })

    messages = [
        {
            "role": "system",
            "content": prompt.build_evidence(
                hypothesis=hypothesis,
                directory=workspace_dir,
                platform=platform.system(),
                model=model,
                context=context,
                max_rounds=policy.max_rounds,
            ),
        },
        {"role": "user", "content": hypothesis},
    ]
    observed_calls: dict[str, dict] = {}
    empty_tool_rounds = 0

    for round_index in range(policy.max_rounds):
        allowed_tools, _, checkpoint_instruction = policy.next_request(run)
        phase_before = policy.phase
        if checkpoint_instruction:
            messages.append({
                "role": "system",
                "content": "\n\n".join(
                    part for part in (
                        checkpoint_instruction,
                        _checkpoint_observations(observed_calls),
                    )
                    if part
                ),
            })
        assistant = _call_model(
            provider,
            model,
            messages,
            tools=agent_tools(DISCOVERY_TOOLS, allowed_tools),
        )
        content = _content_text(assistant.get("content"))
        tool_calls = assistant.get("tool_calls") or []
        messages.append({
            "role": "assistant",
            "content": assistant.get("content"),
            **({"tool_calls": tool_calls} if tool_calls else {}),
        })

        if content:
            yield _start(f"{run_id}-thinking-{round_index}", "thinking", {
                "text": content,
                "done": True,
                "open": bool(tool_calls),
        })

        if not tool_calls:
            empty_tool_rounds += 1
            if empty_tool_rounds >= MAX_EMPTY_TOOL_ROUNDS:
                policy.request_checkpoint()
                messages.append({
                    "role": "system",
                    "content": (
                        "You have stopped without tool calls twice. Record evidence "
                        "from existing observations or conclude if enough evidence is recorded."
                    ),
                })
                if not run.evidence:
                    break
                continue
            if policy.phase != EvidencePhase.EVALUATE and (run.evidence or observed_calls):
                messages.append({
                    "role": "system",
                    "content": "Continue the current evidence phase. Use one of the tools "
                    "available in this phase rather than stopping.",
                })
                continue
            if not run.evidence and observed_calls:
                policy.request_checkpoint()
                messages.append({
                    "role": "system",
                    "content": "You have tool observations but no recorded evidence. "
                    "The next turn must call record_evidence.",
                })
                continue
            break

        empty_tool_rounds = 0
        concluded = False
        for raw_call in tool_calls:
            call_id = raw_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = raw_call.get("function") or {}
            name = function.get("name", "")
            try:
                arguments = json.loads(function.get("arguments") or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
                if name not in set(allowed_tools):
                    raise ValueError(f"unknown agent tool: {name or 'tool'}")
                output, emitted, concluded = _handle_agent_tool(
                    name=name,
                    call_id=call_id,
                    arguments=arguments,
                    run=run,
                    observed_calls=observed_calls,
                    workspace_dir=workspace_dir,
                    hypothesis_id=hypothesis_id,
                    stage_id=stage_id,
                    run_id=run_id,
                    policy=policy,
                )
                yield from emitted
                if policy.phase != phase_before:
                    yield {"op": "update", "id": stage_id, "patch": {
                        "phase": policy.phase.value,
                    }}
                    phase_before = policy.phase
            except Exception as exc:
                output = _tool_error_json(exc, name)
                if name == "record_evidence":
                    policy.note_checkpoint_failure()
                yield _start(call_id, "tool", {
                    "name": name or "invalid",
                    "description": (
                        registry.get(name).description
                        if registry.get(name)
                        else "Agent control tool"
                    ),
                    "status": "error",
                    "open": False,
                    "input": function.get("arguments") or "{}",
                    "output": output,
                })
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": output,
            })
            if concluded:
                break
        if concluded:
            break

    if run.state == RunState.GATHERING:
        if run.evidence:
            if policy.phase != EvidencePhase.EVALUATE:
                policy.phase = EvidencePhase.EVALUATE
                yield {"op": "update", "id": stage_id, "patch": {
                    "phase": policy.phase.value,
                }}
            summary = _fallback_summary(run)
        else:
            summary = _no_evidence_summary(observed_calls)
        run.conclude(summary)
        yield {"op": "update", "id": stage_id, "patch": {"state": run.state.value}}
        yield {"op": "update", "id": hypothesis_id, "patch": {
            "confidence": run.confidence,
            "status": run.state.value,
        }}
        yield _start(f"{run_id}-verdict", "verdict", {
            "verdict": run.state.value,
            "confidence": run.confidence,
            "summary": run.summary,
        })

    if run.summary.strip():
        yield _start(f"{run_id}-output", "output", {
            "content": run.summary,
            "streaming": False,
        })
    yield {"op": "done", "run": run.snapshot()}


def _fallback_summary(run: EvidenceRun) -> str:
    findings = []
    for item in run.evidence.values():
        stance = "supports" if item.stance == "support" else "opposes"
        findings.append(f"{item.id} {stance}: {item.claim} ({item.source_uri})")
    return "Verdict computed from recorded evidence: " + "; ".join(findings)


def _no_evidence_summary(observed_calls: dict[str, dict]) -> str:
    summary = (
        "Evidence gathering ended without a valid evidence record. "
        "Tool observations were not converted into grounded evidence."
    )
    observations = _observed_call_summary(observed_calls)
    return summary + ("\n\nRecent observations:\n" + observations if observations else "")


def _observed_call_summary(observed_calls: dict[str, dict]) -> str:
    lines = []
    for call_id, observed in list(observed_calls.items())[-4:]:
        result = observed["result"]
        excerpt = next(
            (line.strip() for line in result.output.splitlines() if _useful_excerpt_candidate(line)),
            result.output.strip(),
        )[:200]
        lines.append(f"- {call_id} {observed['name']}: {result.title} :: {excerpt}")
    return "\n".join(line for line in lines if line.strip())


def _tool_error_json(exc: Exception, tool_name: str) -> str:
    return json.dumps({
        "error": {
            "code": exc.__class__.__name__,
            "tool": tool_name or "invalid",
            "message": str(exc),
            "retryable": True,
        }
    }, ensure_ascii=False)


def _checkpoint_observations(observed_calls: dict[str, dict]) -> str:
    if not observed_calls:
        return ""
    blocks = []
    for call_id, observed in list(observed_calls.items())[-6:]:
        lines = [
            line.strip()
            for line in observed["result"].output.splitlines()
            if _useful_excerpt_candidate(line)
        ][:4]
        if not lines:
            continue
        blocks.append(
            f"- source_tool_call_id={call_id} ({observed['name']}):\n"
            + "\n".join(f"  excerpt candidate: {line}" for line in lines)
        )
    if not blocks:
        return ""
    return (
        "Recent exact excerpt candidates. Copy one candidate verbatim into "
        "record_evidence.excerpt; do not paraphrase it:\n"
        + "\n".join(blocks)
    )


def _useful_excerpt_candidate(line: str) -> bool:
    text = line.strip()
    if len(text) < 8:
        return False
    lowered = text.casefold()
    if lowered in {"(no matches)", "<!doctype html>"}:
        return False
    return any(ch.isalnum() for ch in text)


def _best_excerpt_candidate(output: str, claim: str) -> str:
    claim_tokens = _excerpt_tokens(claim)
    best = ("", 0)
    for line in output.splitlines():
        candidate = line.strip()
        if not _useful_excerpt_candidate(candidate):
            continue
        score = len(claim_tokens & _excerpt_tokens(candidate))
        if score > best[1]:
            best = (candidate, score)
    return best[0] if best[1] > 0 else ""


def _excerpt_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(
            r"[a-zA-Z_][a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]{2,}",
            value.casefold(),
        )
        if len(token) >= 3 or "\u4e00" <= token[0] <= "\u9fff"
    }


def _handle_agent_tool(
    *,
    name: str,
    call_id: str,
    arguments: dict,
    run: EvidenceRun,
    observed_calls: dict[str, dict],
    workspace_dir: str,
    hypothesis_id: str,
    stage_id: str,
    run_id: str,
    policy: EvidencePolicy | None = None,
) -> tuple[str, list[dict], bool]:
    events: list[dict] = []
    if name in DISCOVERY_TOOLS:
        if policy:
            arguments = policy.prepare_discovery(name, arguments)
        tool = registry.get(name)
        events.append(_start(call_id, "tool", {
            "name": name,
            "description": tool.description,
            "status": "running",
            "open": False,
            "input": json.dumps(arguments, ensure_ascii=False, indent=2),
            "output": "",
        }))
        _, result = _execute_tool(name, arguments, workspace_dir)
        observed_calls[call_id] = {
            "name": name,
            "arguments": arguments,
            "result": result,
        }
        if policy:
            useful = not result.title.startswith("[error]")
            policy.note_discovery(name, arguments, useful=useful)
        events.append({"op": "update", "id": call_id, "patch": {
            "status": "error" if result.title.startswith("[error]") else "done",
            "title": result.title,
            "output": result.output,
            "metadata": result.metadata,
        }})
        model_output = {
            "tool_call_id": call_id,
            "title": result.title,
            "output": result.output,
            "metadata": result.metadata,
        }
        return json.dumps(model_output, ensure_ascii=False), events, False

    if name == "record_evidence":
        source_call_id = arguments.pop("source_tool_call_id")
        observed = observed_calls.get(source_call_id)
        if observed is None:
            raise ValueError("source_tool_call_id must reference a completed discovery tool")
        excerpt = arguments.get("excerpt", "")
        observed_output = observed["result"].output
        if not excerpt or _normalized(excerpt) not in _normalized(observed_output):
            replacement = _best_excerpt_candidate(
                observed_output,
                arguments.get("claim", ""),
            )
            if replacement:
                arguments["excerpt"] = replacement
            else:
                sample = next(
                    (
                        line.strip()
                        for line in observed_output.splitlines()
                        if _useful_excerpt_candidate(line)
                    ),
                    observed_output.strip(),
                )[:300]
                raise ValueError(
                    "evidence excerpt must be copied from the cited tool output "
                    f"(whitespace differences are allowed). Try an exact excerpt like: {sample!r}"
                )
        item = run.add_evidence(**arguments, tool_call_id=source_call_id)
        if policy:
            policy.note_evidence(item.stance)
        data = {
            **asdict(item),
            "confidence": run.confidence,
        }
        events.append(_start(f"{run_id}-evidence-{item.id}", "evidence", data))
        events.append({"op": "update", "id": hypothesis_id, "patch": {
            "confidence": run.confidence,
        }})
        return json.dumps(data, ensure_ascii=False), events, False

    if name == "link_evidence":
        if "relation" not in arguments and "relationship" in arguments:
            arguments["relation"] = arguments.pop("relationship")
        edge = run.link_evidence(**arguments)
        if policy:
            policy.note_audit()
        data = {**asdict(edge), "confidence": run.confidence}
        events.append(_start(
            f"{run_id}-relation-{len(run.relations)}",
            "evidence_relation",
            data,
        ))
        events.append({"op": "update", "id": hypothesis_id, "patch": {
            "confidence": run.confidence,
        }})
        return json.dumps(data, ensure_ascii=False), events, False

    if name == "conclude":
        if not run.evidence:
            raise ValueError("at least one grounded evidence item is required before concluding")
        verdict = run.conclude(arguments["summary"])
        events.append({"op": "update", "id": stage_id, "patch": {
            "state": verdict.value,
        }})
        events.append({"op": "update", "id": hypothesis_id, "patch": {
            "confidence": run.confidence,
            "status": verdict.value,
        }})
        data = {
            "verdict": verdict.value,
            "confidence": run.confidence,
            "summary": run.summary,
        }
        events.append(_start(f"{run_id}-verdict", "verdict", data))
        return json.dumps(data, ensure_ascii=False), events, True

    raise ValueError(f"unknown agent tool: {name}")


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


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
    yield _start(thinking, "thinking", {"text": "", "done": False, "open": True})
    thought = f"Inspecting {', '.join(context) or 'the workspace'} for: {message}"
    for index in range(0, len(thought), 12):
        pause()
        yield {"op": "delta", "id": thinking, "field": "text", "value": thought[index:index + 12]}
    yield {"op": "update", "id": thinking, "patch": {"done": True}}

    for call_id, name, params in (
        ("test-read", "read", {"path": "stratumcode/server.py", "start_line": 1, "end_line": 90}),
        ("test-grep", "grep", {"pattern": "/api/tools|registry\\.", "include": "*.py"}),
    ):
        tool = registry.get(name)
        pause(2)
        yield _start(call_id, "tool", {
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

    yield _start("test-agent", "subagent", {
        "name": "@explore",
        "task": "Map the server routes and chat integration points",
        "status": "done",
        "result": "Found the request handler and provider integration.",
        "open": True,
    })
    yield _start("test-diff", "diff", {
        "path": "stratumcode/server.py",
        "hunks": [{"type": "add", "lines": ["+        elif path == \"/api/chat\":"]}],
        "accepted": None,
    })
    yield _start("test-output", "output", {
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
                    yield _start(output_id, "output", {
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
    return evidence_stream(message, context, workspace_dir, max_rounds=max_rounds)
