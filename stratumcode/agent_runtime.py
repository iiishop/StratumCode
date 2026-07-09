from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .agent.tools import agent_tools
from .agent.policy import DISCOVERY_TOOLS

MAX_MODEL_OUTPUT_TOKENS = 2048
MODEL_RETRY_STATUS_CODES = {429, 502, 503, 504}
MODEL_RETRY_DELAYS = (0.5, 1.0)


def start_event(event_id: str, event_type: str, data: dict) -> dict:
    return {"op": "start", "id": event_id, "event": event_type, "data": data}


def call_model(
    provider: dict,
    model: str,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "tools": agent_tools(DISCOVERY_TOOLS) if tools is None else tools,
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
