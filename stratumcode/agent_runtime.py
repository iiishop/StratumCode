from __future__ import annotations

import json
import socket
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import app_settings, providers, skill_runtime
from .agent.policy import DISCOVERY_TOOLS
from .agent.tools import agent_tools

DEFAULT_MODEL_REQUEST_ATTEMPTS = 3
MODEL_RETRY_STATUS_CODES = {429, 502, 503, 504}
MODEL_RETRY_DELAYS = (0.5, 1.0)
MODEL_STREAM_DEADLINE_SECONDS = 180
OUTPUT_TRUNCATION_REASONS = {"length", "max_tokens", "max_output_tokens", "incomplete"}
_NON_THINKING_MODELS: set[str] = set()


def start_event(event_id: str, event_type: str, data: dict) -> dict:
    return {"op": "start", "id": event_id, "event": event_type, "data": data}


def stage_progress(
    event_id: str,
    items: list[dict],
    item_id: str,
    label: str,
    *,
    description: str = "",
    detail: str = "",
    state: str = "running",
) -> dict:
    item = next((entry for entry in items if entry.get("id") == item_id), None)
    if item is None:
        item = {"id": item_id, "label": label}
        items.append(item)
    item.update({
        "description": description,
        "detail": detail,
        "state": state,
    })
    return {
        "op": "update",
        "id": event_id,
        "patch": {"progress": [dict(entry) for entry in items]},
    }


def call_model(
    provider: dict,
    model: str,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    tool_choice=None,
    use_skills: bool = True,
) -> dict:
    if not use_skills:
        return _call_model_once(
            provider,
            model,
            messages,
            tools=tools,
            tool_choice=tool_choice,
        )
    catalog = skill_runtime.tool_catalog_prompt()
    loaded_messages = skill_runtime.loaded_messages()
    current_tools = _with_skill_tool(tools)
    current_messages = [
        *messages,
        *([{"role": "system", "content": catalog}] if catalog else []),
        *loaded_messages,
    ]
    return _call_model_once(
        provider,
        model,
        current_messages,
        tools=current_tools,
        tool_choice=tool_choice,
    )


def select_initial_skills(
    provider: dict,
    model: str,
    context: str,
) -> dict:
    prompt_text = skill_runtime.initial_selection_prompt(context)
    return finish_initial_skill_selection(provider, model, prompt_text)


def finish_initial_skill_selection(
    provider: dict,
    model: str,
    prompt_text: str,
) -> dict:
    if not prompt_text:
        return {}
    assistant = _call_model_once(
        provider,
        model,
        [{"role": "system", "content": prompt_text}],
        tools=[skill_runtime.tool_schema()],
    )
    choices = []
    for call in assistant.get("tool_calls") or []:
        loaded, _, choice = execute_skill_tool_call(call)
        if loaded and choice not in choices:
            choices.append(choice)
    skill_runtime.finish_selection(choices)
    return assistant


def execute_skill_tool_call(call: dict) -> tuple[bool, str, dict]:
    function = call.get("function") or {}
    if function.get("name") != "load_skill":
        return False, "", {}
    try:
        arguments = json.loads(function.get("arguments") or "{}")
        if not isinstance(arguments, dict):
            raise TypeError("load_skill arguments must be an object")
        choice = {
            "name": str(arguments.get("name") or "").strip(),
            "reason": str(arguments.get("reason") or "").strip(),
        }
        output = skill_runtime.load_skill(choice["name"], choice["reason"])
        return True, output, choice
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        return True, tool_error_json(exc, "load_skill"), {}


def _with_skill_tool(tools: list[dict] | None) -> list[dict] | None:
    if not skill_runtime.tool_catalog_prompt():
        return tools
    if tools == []:
        return tools
    current = list(agent_tools(DISCOVERY_TOOLS) if tools is None else tools)
    if any(((tool.get("function") or {}).get("name") or "") == "load_skill" for tool in current):
        return current
    current.append(skill_runtime.tool_schema())
    return current


def _sanitize_tool_call_balance(messages: list[dict]) -> list[dict]:
    """OpenAI 兼容 API 拒绝 'assistant 带 tool_calls 但缺少对应 tool 消息' 的请求。

    防御：扫描最后一条 assistant tool_calls 消息，如果其后没有覆盖全部
    tool_call_id 的 tool 响应（investigation 某些分支可能漏 append），剥离
    tool_calls（保留 content）——宁可丢一次工具意图，也不让整个请求 400。
    正常路径（平衡）不做任何改动。
    """
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        calls = message.get("tool_calls") or []
        call_ids = {str(call.get("id")) for call in calls if call.get("id")}
        if not call_ids:
            continue
        covered: set[str] = set()
        for later in messages[index + 1:]:
            if isinstance(later, dict) and later.get("role") == "tool":
                tool_id = later.get("tool_call_id")
                if tool_id is not None and str(tool_id) in call_ids:
                    covered.add(str(tool_id))
        if covered != call_ids:
            sanitized = dict(message)
            sanitized.pop("tool_calls", None)
            messages[index] = sanitized
        break  # 只检查最后一条 assistant（更早的历史已平衡）
    return messages


def _call_model_once(
    provider: dict,
    model: str,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    tool_choice=None,
) -> dict:
    _sanitize_tool_call_balance(messages)
    output_tokens = _effective_output_tokens(provider)
    if provider.get("auth_type") == "codex_oauth":
        return _call_codex_responses(provider, model, messages, tools, tool_choice)
    url = f"{provider['base_url'].rstrip('/')}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
    }
    attempts = app_settings.get_round_limit("model_request_attempts") or DEFAULT_MODEL_REQUEST_ATTEMPTS
    data = None
    for attempt in _attempt_indexes(attempts, start=0):
        payload = _payload(provider, model, messages, tools, output_tokens, tool_choice)
        body = json.dumps(payload).encode()
        request = Request(url, data=body, headers=headers)
        try:
            with urlopen(request, timeout=90) as response:
                data = json.load(response)
            break
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            model_key = _model_key(provider, model)
            if (
                model_key not in _NON_THINKING_MODELS
                and _thinking_needs_disabled(exc.code, detail)
            ):
                _NON_THINKING_MODELS.add(model_key)
                continue
            if exc.code not in MODEL_RETRY_STATUS_CODES or _last_attempt(attempt, attempts):
                raise ValueError(f"provider request failed ({exc.code}): {detail}") from exc
        except (TimeoutError, socket.timeout) as exc:
            if _last_attempt(attempt, attempts):
                raise ValueError("provider request timed out while reading response") from exc
        except URLError as exc:
            detail = str(exc.reason if hasattr(exc, "reason") else exc)
            if _last_attempt(attempt, attempts):
                raise ValueError(f"provider request failed: {detail}") from exc
        time.sleep(MODEL_RETRY_DELAYS[min(attempt, len(MODEL_RETRY_DELAYS) - 1)])
    if data is None:
        raise ValueError("provider request failed before returning a response")
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0].get("message"), dict):
        raise ValueError("provider returned no assistant message")
    message = choices[0]["message"]
    message["finish_reason"] = choices[0].get("finish_reason")
    message["_usage"] = data.get("usage") or {}
    return message


def _merge_model_usage(total: dict, usage: dict) -> None:
    for key, value in usage.items():
        if isinstance(value, int | float):
            total[key] = total.get(key, 0) + value
        elif key not in total:
            total[key] = value


def tool_arguments(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _effective_output_tokens(provider: dict) -> int:
    configured = app_settings.get_output_limit("llm_output_tokens")
    limits = [
        int(value)
        for value in (configured, provider.get("model_output_tokens"))
        if value is not None and int(value) > 0
    ]
    return min(limits) if limits else 0


def output_truncated(message: dict) -> bool:
    reason = str(message.get("finish_reason") or "").strip().casefold()
    return reason in OUTPUT_TRUNCATION_REASONS


def _call_codex_responses(provider: dict, model: str, messages: list[dict], tools, tool_choice) -> dict:
    access, account_id = providers.codex_access_token(provider)
    headers = {
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json",
        "User-Agent": "StratumCode/0.1",
        "originator": "stratumcode",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    payload = {
        "model": model,
        "input": _responses_input(messages),
        "tools": _responses_tools(agent_tools(DISCOVERY_TOOLS) if tools is None else tools),
        "store": False,
        "stream": True,
    }
    if tool_choice is not None:
        payload["tool_choice"] = _responses_tool_choice(tool_choice)
    req = Request(
        f"{provider['base_url'].rstrip('/')}/responses",
        data=json.dumps(payload).encode(),
        headers=headers,
    )
    try:
        with urlopen(req, timeout=90) as response:
            content_type = response.headers.get("Content-Type", "")
            raw = _read_response_body(response, content_type)
            data = _responses_data(raw, content_type)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise ValueError(f"provider request failed ({exc.code}): {detail}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise ValueError("provider request timed out while reading response") from exc
    except URLError as exc:
        detail = str(exc.reason if hasattr(exc, "reason") else exc)
        raise ValueError(f"provider request failed: {detail}") from exc
    message = _responses_message(data)
    message["_usage"] = _responses_usage(data.get("usage") or {})
    return message


def _read_response_body(response, content_type: str) -> bytes:
    if "text/event-stream" not in str(content_type or ""):
        return response.read()
    deadline = time.monotonic() + MODEL_STREAM_DEADLINE_SECONDS
    blocks = []
    current = []
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("provider stream did not complete before deadline")
        line = response.readline()
        if not line:
            if current:
                blocks.append(b"".join(current))
            break
        current.append(line)
        if line in (b"\n", b"\r\n"):
            block = b"".join(current)
            blocks.append(block)
            if _sse_terminal_event(block):
                break
            current = []
    return b"".join(blocks)


def _sse_terminal_event(block: bytes) -> bool:
    text = block.decode("utf-8", errors="replace")
    return any(
        line.strip() in {"event: response.completed", "event: response.failed", "event: response.incomplete"}
        for line in text.splitlines()
    )


def _responses_input(messages: list[dict]) -> list[dict]:
    items = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            content = content_text(message.get("content"))
            if content:
                items.append({"role": "developer", "content": content})
            continue
        if role in {"user", "assistant"}:
            content = content_text(message.get("content"))
            if content or role == "user":
                items.append({"role": role, "content": content})
            for index, call in enumerate(message.get("tool_calls") or []):
                fn = call.get("function") or {}
                if not fn.get("name"):
                    continue
                items.append({
                    "type": "function_call",
                    "call_id": call.get("id") or f"call_{index}",
                    "name": fn["name"],
                    "arguments": fn.get("arguments") or "{}",
                })
        elif role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": message.get("tool_call_id") or "",
                "output": content_text(message.get("content")),
            })
    return items


def _responses_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    result = []
    for tool in tools:
        fn = (tool or {}).get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        result.append({
            "type": "function",
            "name": name,
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
            "strict": False,
        })
    return result or None


def _responses_tool_choice(tool_choice):
    if isinstance(tool_choice, dict):
        name = ((tool_choice.get("function") or {}).get("name"))
        if name:
            return {"type": "function", "name": name}
    return tool_choice


def _responses_message(data: dict) -> dict:
    content = []
    tool_calls = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("text"):
                    content.append(str(part["text"]))
        elif item.get("type") == "function_call":
            call_id = item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}"
            tool_calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": item.get("name") or "",
                    "arguments": item.get("arguments") or "{}",
                },
            })
    incomplete = data.get("incomplete_details") if isinstance(data.get("incomplete_details"), dict) else {}
    return {
        "content": "\n".join(content).strip(),
        "tool_calls": tool_calls,
        "finish_reason": incomplete.get("reason") or data.get("status"),
    }


def _responses_usage(usage: dict) -> dict:
    return {
        "prompt_tokens": int(usage.get("input_tokens") or 0),
        "completion_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "prompt_tokens_details": {"cached_tokens": int(usage.get("cached_tokens") or 0)},
    }


def _responses_data(raw: bytes, content_type: str = "") -> dict:
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" not in content_type and not text.lstrip().startswith("event:"):
        return json.loads(text)
    final = {}
    text_chunks = []
    for block in text.split("\n\n"):
        data_lines = [
            line[5:].strip()
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        payload = "\n".join(data_lines)
        if payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if isinstance(event.get("response"), dict):
            response = event["response"]
            if response.get("output"):
                final = response
            else:
                final = {**final, **response, "output": final.get("output", [])}
        elif event_type == "response.output_text.delta":
            if event.get("delta"):
                text_chunks.append(str(event["delta"]))
        elif event_type == "response.output_text.done" and not text_chunks:
            if event.get("text"):
                text_chunks.append(str(event["text"]))
        elif event.get("type") == "response.output_item.done":
            final.setdefault("output", []).append(event.get("item") or {})
    if not final:
        raise ValueError("provider returned an empty Codex event stream")
    if text_chunks and not _response_has_text(final):
        final.setdefault("output", []).append({
            "type": "message",
            "content": [{"type": "output_text", "text": "".join(text_chunks)}],
        })
    return final


def _response_has_text(response: dict) -> bool:
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("text"):
                return True
    return False


def _payload(provider: dict, model: str, messages: list[dict], tools, max_tokens: int, tool_choice) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "tools": agent_tools(DISCOVERY_TOOLS) if tools is None else tools,
        "temperature": 0.1,
    }
    if max_tokens > 0:
        payload["max_tokens"] = max_tokens
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if _model_key(provider, model) in _NON_THINKING_MODELS:
        payload["thinking"] = {"type": "disabled"}
    return payload


def _model_key(provider: dict, model: str) -> str:
    return f"{provider.get('base_url', '').rstrip('/')}|{model}"


def _thinking_needs_disabled(status: int, detail: str) -> bool:
    lowered = detail.casefold()
    return status == 400 and "thinking" in lowered and (
        "tool_choice" in lowered
        or ("reasoning_content" in lowered and "passed back" in lowered)
    )


def _attempt_indexes(limit: int, start: int = 0):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


def _last_attempt(attempt: int, limit: int) -> bool:
    return int(limit or 0) > 0 and attempt >= int(limit) - 1


def content_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    return ""


def assistant_visible_text(message: dict) -> str:
    return (
        content_text(message.get("content"))
        or content_text(message.get("reasoning_content"))
        or content_text(message.get("reasoning"))
    )


def assistant_message(message: dict) -> dict:
    result = {"role": "assistant", "content": message.get("content") or ""}
    for key in ("reasoning_content", "reasoning", "tool_calls"):
        if message.get(key):
            result[key] = message[key]
    if "reasoning_content" not in result and isinstance(result.get("reasoning"), str):
        result["reasoning_content"] = result["reasoning"]
    return result


def empty_usage(pricing_rules: list[dict]) -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "currency": pricing_currency(pricing_rules),
    }


def usage_delta(pricing_rules: list[dict], usage: dict) -> dict:
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
    pricing = active_pricing(pricing_rules)
    cost = usage_cost(input_tokens, output_tokens, cached_tokens, pricing) if pricing else 0.0
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "total_tokens": int(usage.get("total_tokens") or input_tokens + output_tokens),
        "cost": round(cost, 6),
        "currency": (pricing or {}).get("currency") or pricing_currency(pricing_rules),
    }


def add_usage(total: dict, delta: dict) -> None:
    for key in ("input_tokens", "output_tokens", "cached_tokens", "total_tokens"):
        total[key] += delta.get(key, 0)
    total["cost"] = round(total["cost"] + delta.get("cost", 0), 6)
    total["currency"] = delta.get("currency") or total["currency"]


def pricing_currency(pricing_rules: list[dict]) -> str:
    pricing = active_pricing(pricing_rules)
    return (pricing or {}).get("currency", "USD")


def active_pricing(rules: list[dict]) -> dict | None:
    if isinstance(rules, dict):
        rules = [rules]
    if not isinstance(rules, list):
        return None
    now = datetime.now(timezone.utc)
    minute = now.hour * 60 + now.minute
    for rule in rules:
        start = minute_of_day(rule.get("start", "00:00"))
        end = minute_of_day(rule.get("end", "24:00"))
        if start <= minute < end or (end < start and (minute >= start or minute < end)):
            return rule
    return rules[0] if rules else None


def minute_of_day(value: str) -> int:
    hour, minute = [int(part) for part in value.split(":", 1)]
    return hour * 60 + minute


def usage_cost(input_tokens: int, output_tokens: int, cached_tokens: int, pricing: dict) -> float:
    uncached_input = max(0, input_tokens - cached_tokens)
    return (
        uncached_input * float(pricing.get("input_per_m") or 0)
        + output_tokens * float(pricing.get("output_per_m") or 0)
        + cached_tokens * float(pricing.get("cache_per_m") or 0)
    ) / 1_000_000


def tool_error_retryable(exc: Exception) -> bool:
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


def tool_error_json(exc: Exception, tool_name: str) -> str:
    return json.dumps({
        "error": {
            "code": exc.__class__.__name__,
            "tool": tool_name or "invalid",
            "message": str(exc),
            "retryable": tool_error_retryable(exc),
        }
    }, ensure_ascii=False)
