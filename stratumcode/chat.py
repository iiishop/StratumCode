from __future__ import annotations

import asyncio
import json
import platform
import re
import time
from collections.abc import Iterator
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from . import model_settings, prompt, providers
from .agent import EvidencePolicy, EvidenceRun, RunState
from .agent.policy import DISCOVERY_TOOLS, EvidencePhase
from .agent.tools import agent_tools
from .tools import registry

MAX_AGENT_ROUNDS = 14
MAX_EMPTY_TOOL_ROUNDS = 2
MAX_MODEL_OUTPUT_TOKENS = 2048
MODEL_RETRY_STATUS_CODES = {429, 502, 503, 504}
MODEL_RETRY_DELAYS = (0.5, 1.0)


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
        "max_tokens": MAX_MODEL_OUTPUT_TOKENS,
    }
    body = json.dumps(payload).encode()
    url = f"{provider['base_url'].rstrip('/')}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
    }
    attempts = len(MODEL_RETRY_DELAYS) + 1
    for attempt in range(attempts):
        request = Request(url, data=body, headers=headers)
        try:
            with urlopen(request, timeout=90) as response:
                data = json.load(response)
            break
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            if exc.code not in MODEL_RETRY_STATUS_CODES or attempt == attempts - 1:
                raise ValueError(f"provider request failed ({exc.code}): {detail}") from exc
        except URLError as exc:
            detail = str(exc.reason if hasattr(exc, "reason") else exc)
            if attempt == attempts - 1:
                raise ValueError(f"provider request failed: {detail}") from exc
        time.sleep(MODEL_RETRY_DELAYS[attempt])
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0].get("message"), dict):
        raise ValueError("provider returned no assistant message")
    message = choices[0]["message"]
    message["_usage"] = data.get("usage") or {}
    return message


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
    pricing_rules = providers.get_model_pricing(provider["id"], model)
    run = EvidenceRun(hypothesis)
    run.transition(RunState.GATHERING)
    discovery_tools = _discovery_tools(hypothesis, context)
    policy = EvidencePolicy(
        discovery_tools=discovery_tools,
        max_rounds=max_rounds or MAX_AGENT_ROUNDS,
    )
    run_id = uuid4().hex[:10]
    stage_id = f"{run_id}-stage"
    hypothesis_id = f"{run_id}-hypothesis"
    yield _start(stage_id, "stage", {
        "name": "evidence",
        "label": "Gather evidence",
        "state": run.state.value,
        "phase": policy.phase.value,
        "model": model,
        "context_length": providers.model_context_length(provider["base_url"], provider["api_key"], model),
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
            "content": prompt.build_evidence_static(),
        },
        {
            "role": "system",
            "content": prompt.build_evidence_context(
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
    usage_total = _empty_usage(pricing_rules)

    for round_index in range(policy.max_rounds):
        allowed_tools, _, _ = policy.next_request(run)
        phase_before = policy.phase
        thinking_id = f"{run_id}-thinking-{round_index}"
        yield _start(thinking_id, "thinking", {
            "text": "",
            "done": False,
            "open": True,
        })
        assistant = _call_model(
            provider,
            model,
            messages,
            tools=agent_tools(discovery_tools),
        )
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield _start(f"{run_id}-usage-{round_index}", "usage", {
                "delta": usage,
                "total": usage_total,
            })
        content = _content_text(assistant.get("content"))
        tool_calls = assistant.get("tool_calls") or []
        messages.append({
            "role": "assistant",
            "content": assistant.get("content"),
            **({"tool_calls": tool_calls} if tool_calls else {}),
        })

        if content:
            yield {"op": "update", "id": thinking_id, "patch": {
                "text": content,
                "done": True,
                "open": bool(tool_calls),
            }}
        else:
            yield {"op": "update", "id": thinking_id, "patch": {
                "done": True,
            }}

        if not tool_calls:
            empty_tool_rounds += 1
            if empty_tool_rounds >= MAX_EMPTY_TOOL_ROUNDS:
                policy.request_checkpoint()
                if not run.evidence:
                    break
                continue
            if policy.phase != EvidencePhase.EVALUATE and (run.evidence or observed_calls):
                continue
            if not run.evidence and observed_calls:
                policy.request_checkpoint()
                continue
            break

        empty_tool_rounds = 0
        concluded = False
        compaction_messages: list[dict] = []
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
                gen = _handle_agent_tool(
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
                try:
                    while True:
                        yield next(gen)
                except StopIteration as e:
                    output, concluded = e.value
                if name == "record_evidence":
                    if compact_message := _compact_tool_message(arguments):
                        compaction_messages.append(compact_message)
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
        messages.extend(compaction_messages)
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
            "findings": _build_findings(run),
            "support_count": sum(1 for e in run.evidence.values() if e.stance == "support"),
            "oppose_count": sum(1 for e in run.evidence.values() if e.stance == "oppose"),
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


def _build_findings(run: EvidenceRun) -> list[dict]:
    return [
        {
            "id": item.id,
            "stance": item.stance,
            "claim": item.claim,
            "weight": round(item.strength * 100),
            "source_type": item.source_type,
            "source_uri": item.source_uri,
            "excerpt": item.excerpt[:200],
        }
        for item in run.evidence.values()
    ]


def _discovery_tools(hypothesis: str = "", context: list[str] | None = None) -> tuple[str, ...]:
    mcp_tools = tuple(tool.name for tool in registry.list_all() if tool.name.startswith("mcp_"))
    return DISCOVERY_TOOLS + tuple(name for name in mcp_tools if name not in DISCOVERY_TOOLS)


def _compact_tool_message(evidence_args: dict) -> dict | None:
    source_call_id = evidence_args.get("source_tool_call_id")
    if not source_call_id:
        return None
    compact = {
        "tool_call_id": source_call_id,
        "compacted": True,
        "summary": (
            "The referenced tool output has been recorded as evidence. "
            "For future reasoning, use the recorded excerpt instead of re-reading the full output."
        ),
        "recorded_evidence": {
            "id": evidence_args.get("evidence_id"),
            "source_uri": evidence_args.get("source_uri"),
            "excerpt": evidence_args.get("excerpt"),
        },
    }
    return {
        "role": "user",
        "content": json.dumps(compact, ensure_ascii=False),
    }


def _empty_usage(pricing_rules: list[dict]) -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "currency": _pricing_currency(pricing_rules),
    }


def _usage_delta(pricing_rules: list[dict], usage: dict) -> dict:
    if not usage:
        return {}
    input_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    cached_tokens = int(
        usage.get("prompt_cache_hit_tokens")
        or details.get("cached_tokens")
        or 0
    )
    pricing = _active_pricing(pricing_rules)
    cost = _usage_cost(input_tokens, output_tokens, cached_tokens, pricing) if pricing else 0.0
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "total_tokens": int(usage.get("total_tokens") or input_tokens + output_tokens),
        "cost": round(cost, 6),
        "currency": (pricing or {}).get("currency") or _pricing_currency(pricing_rules),
    }


def _add_usage(total: dict, delta: dict) -> None:
    for key in ("input_tokens", "output_tokens", "cached_tokens", "total_tokens"):
        total[key] += delta.get(key, 0)
    total["cost"] = round(total["cost"] + delta.get("cost", 0), 6)
    total["currency"] = delta.get("currency") or total["currency"]


def _pricing_currency(pricing_rules: list[dict]) -> str:
    pricing = _active_pricing(pricing_rules)
    return (pricing or {}).get("currency", "USD")


def _active_pricing(rules: list[dict]) -> dict | None:
    if isinstance(rules, dict):
        rules = [rules]
    if not isinstance(rules, list):
        return None
    now = datetime.now(timezone.utc)
    minute = now.hour * 60 + now.minute
    for rule in rules:
        start = _minute(rule.get("start", "00:00"))
        end = _minute(rule.get("end", "24:00"))
        if start <= minute < end or (end < start and (minute >= start or minute < end)):
            return rule
    return rules[0] if rules else None


def _minute(value: str) -> int:
    hour, minute = [int(part) for part in value.split(":", 1)]
    return hour * 60 + minute


def _usage_cost(input_tokens: int, output_tokens: int, cached_tokens: int, pricing: dict) -> float:
    uncached_input = max(0, input_tokens - cached_tokens)
    return (
        uncached_input * float(pricing.get("input_per_m") or 0)
        + output_tokens * float(pricing.get("output_per_m") or 0)
        + cached_tokens * float(pricing.get("cache_per_m") or 0)
    ) / 1_000_000


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
            "retryable": _tool_error_retryable(exc),
        }
    }, ensure_ascii=False)


def _tool_error_retryable(exc: Exception) -> bool:
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError)):
        return False
    if isinstance(exc, json.JSONDecodeError):
        return False
    if isinstance(exc, (KeyError, TypeError)):
        return False
    if isinstance(exc, ValueError):
        message = str(exc).casefold()
        permanent = (
            "path escapes worktree",
            "not a file",
            "unknown agent tool",
            "tool arguments must be an object",
            "source_tool_call_id must reference",
        )
        return not any(text in message for text in permanent)
    return True


def _useful_excerpt_candidate(line: str) -> bool:
    text = line.strip()
    if len(text) < 8:
        return False
    lowered = text.casefold()
    if lowered in {"(no matches)", "(no results)", "<!doctype html>"}:
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
    return set(re.findall(
        r"[a-zA-Z_][a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]{2,}",
        value.casefold(),
    ))


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
):
    """Yields stream packets directly. Returns (model_output, concluded) via StopIteration."""
    discovery_tools = policy.available_discovery_tools() if policy else DISCOVERY_TOOLS
    if name in discovery_tools:
        if policy:
            arguments = policy.prepare_discovery(name, arguments)
        tool = registry.get(name)
        yield _start(call_id, "tool", {
            "name": name,
            "description": tool.description,
            "status": "running",
            "open": False,
            "input": json.dumps(arguments, ensure_ascii=False, indent=2),
            "output": "",
        })
        _, result = _execute_tool(name, arguments, workspace_dir)
        observed_calls[call_id] = {
            "name": name,
            "arguments": arguments,
            "result": result,
        }
        if policy:
            useful = not result.title.startswith("[error]")
            policy.note_discovery(name, arguments, useful=useful)
        yield {"op": "update", "id": call_id, "patch": {
            "status": "error" if result.title.startswith("[error]") else "done",
            "title": result.title,
            "output": result.output,
            "metadata": result.metadata,
        }}
        model_output = {
            "tool_call_id": call_id,
            "title": result.title,
            "output": result.output,
            "metadata": result.metadata,
        }
        return json.dumps(model_output, ensure_ascii=False), False

    if name == "record_evidence":
        source_call_id = arguments.get("source_tool_call_id")
        observed = observed_calls.get(source_call_id)
        if observed is None:
            raise ValueError("source_tool_call_id must reference a completed discovery tool")
        record_args = dict(arguments)
        record_args.pop("source_tool_call_id", None)
        excerpt = arguments.get("excerpt", "")
        if _normalized(excerpt) in {"(no matches)", "(no results)"}:
            raise ValueError("empty search results cannot be recorded as evidence excerpt")
        observed_output = observed["result"].output
        if not excerpt or _normalized(excerpt) not in _normalized(observed_output):
            replacement = _best_excerpt_candidate(
                observed_output,
                arguments.get("claim", ""),
            )
            if replacement:
                arguments["excerpt"] = replacement
                record_args["excerpt"] = replacement
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
        item = run.add_evidence(**record_args, tool_call_id=source_call_id)
        if policy:
            policy.note_evidence(item.stance)
        data = {
            **asdict(item),
            "confidence": run.confidence,
        }
        yield _start(f"{run_id}-evidence-{item.id}", "evidence", data)
        yield {"op": "update", "id": hypothesis_id, "patch": {
            "confidence": run.confidence,
        }}
        return json.dumps(data, ensure_ascii=False), False

    if name == "link_evidence":
        if "relation" not in arguments and "relationship" in arguments:
            arguments["relation"] = arguments.pop("relationship")
        edge = run.link_evidence(**arguments)
        if policy:
            policy.note_audit()
        data = {**asdict(edge), "confidence": run.confidence}
        yield _start(
            f"{run_id}-relation-{len(run.relations)}",
            "evidence_relation",
            data,
        )
        yield {"op": "update", "id": hypothesis_id, "patch": {
            "confidence": run.confidence,
        }}
        return json.dumps(data, ensure_ascii=False), False

    if name == "conclude":
        if not run.evidence:
            raise ValueError("at least one grounded evidence item is required before concluding")
        verdict = run.conclude(arguments["summary"])
        yield {"op": "update", "id": stage_id, "patch": {
            "state": verdict.value,
        }}
        yield {"op": "update", "id": hypothesis_id, "patch": {
            "confidence": run.confidence,
            "status": verdict.value,
        }}
        data = {
            "verdict": verdict.value,
            "confidence": run.confidence,
            "summary": run.summary,
            "findings": _build_findings(run),
            "support_count": sum(1 for e in run.evidence.values() if e.stance == "support"),
            "oppose_count": sum(1 for e in run.evidence.values() if e.stance == "oppose"),
        }
        yield _start(f"{run_id}-verdict", "verdict", data)
        return json.dumps(data, ensure_ascii=False), True

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
