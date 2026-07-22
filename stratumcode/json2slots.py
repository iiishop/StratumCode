from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import TypeAlias

JSONValue: TypeAlias = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
SlotAsker: TypeAlias = Callable[[str, str], JSONValue]

DEFAULT_PLACEHOLDERS = (None, "", "___", "____", "...")


def json2slots(
    template: JSONValue,
    ask: SlotAsker,
    *,
    placeholders: tuple[object, ...] = DEFAULT_PLACEHOLDERS,
    context: str = "",
) -> JSONValue:
    """Fill placeholder leaves in a JSON template by asking for one slot at a time."""
    return _fill(template, ask, tuple(placeholders), context, "")


def _fill(value: JSONValue, ask: SlotAsker, placeholders: tuple[object, ...], context: str, path: str) -> JSONValue:
    if _is_placeholder(value, placeholders):
        return _clean_answer(ask(path or "$", _slot_prompt(path or "$", context)))
    if isinstance(value, dict):
        return {
            key: _fill(item, ask, placeholders, context, _child_path(path, key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _fill(item, ask, placeholders, context, f"{path or '$'}[{index}]")
            for index, item in enumerate(value)
        ]
    return value


def _is_placeholder(value: JSONValue, placeholders: tuple[object, ...]) -> bool:
    if value in placeholders:
        return True
    return isinstance(value, str) and value.strip() in placeholders


def _slot_prompt(path: str, context: str) -> str:
    lines = [
        "Fill one JSON slot.",
        f"slot_path: {path}",
        "Return only the value for this slot. Do not return the full JSON object.",
    ]
    if context:
        lines.append(f"context: {context}")
    return "\n".join(lines)


def _clean_answer(value: JSONValue) -> JSONValue:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _child_path(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else key


def _demo() -> None:
    answers = {"aaa": '"alpha"', "bbb.ccc": "42", "items[0]": "```json\ntrue\n```"}
    calls = []

    def ask(path: str, prompt: str) -> JSONValue:
        calls.append((path, prompt))
        return answers[path]

    template: JSONValue = {"aaa": "____", "bbb": {"ccc": None, "kept": "set"}, "items": ["___"]}
    result = json2slots(template, ask, context="demo")
    assert result == {"aaa": "alpha", "bbb": {"ccc": 42, "kept": "set"}, "items": [True]}
    assert [path for path, _ in calls] == ["aaa", "bbb.ccc", "items[0]"]
    assert template == {"aaa": "____", "bbb": {"ccc": None, "kept": "set"}, "items": ["___"]}


if __name__ == "__main__":
    _demo()
