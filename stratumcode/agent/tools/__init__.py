from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from pkgutil import iter_modules

from ...tools import registry


COMPACT_DESCRIPTIONS = {
    "read": "Read a workspace file.",
    "glob": "List workspace files by glob.",
    "grep": "Search workspace files with regex.",
    "webfetch": "Fetch URL text.",
    "websearch": "Search the public web.",
    "subagent": "Delegate to a focused subagent.",
}


def _compact_schema(value, *, keep_descriptions: bool = False):
    if isinstance(value, dict):
        return {
            key: _compact_schema(item, keep_descriptions=keep_descriptions)
            for key, item in value.items()
            if keep_descriptions or key != "description"
        }
    if isinstance(value, list):
        return [_compact_schema(item, keep_descriptions=keep_descriptions) for item in value]
    return value


def openai_tool_schema(name: str, description: str, parameters: dict) -> dict:
    compact_builtin = name in COMPACT_DESCRIPTIONS
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": COMPACT_DESCRIPTIONS.get(name, description),
            "parameters": _compact_schema(parameters, keep_descriptions=not compact_builtin),
        },
    }


def _discover_control_tools() -> tuple[dict, ...]:
    tools = []
    for module_info in iter_modules(__path__):
        name = module_info.name
        if name.startswith("_"):
            continue
        module = import_module(f"{__name__}.{name}")
        tools.append(
            (
                getattr(module, "ORDER", 100),
                module.NAME,
                openai_tool_schema(module.NAME, module.DESCRIPTION, module.PARAMETERS),
            )
        )
    return tuple(tool for _, _, tool in sorted(tools))


CONTROL_TOOLS = _discover_control_tools()
CONTROL_TOOL_NAMES = tuple(tool["function"]["name"] for tool in CONTROL_TOOLS)


@lru_cache(maxsize=8)
def _all_agent_tools(discovery_names: tuple[str, ...]) -> tuple[dict, ...]:
    tools = [
        openai_tool_schema(tool.name, tool.description, tool.parameters)
        for name in discovery_names
        if (tool := registry.get(name))
    ]
    tools.extend(CONTROL_TOOLS)
    return tuple(tools)


def agent_tools(discovery_names: tuple[str, ...], allowed: tuple[str, ...] | None = None) -> list[dict]:
    tools = list(_all_agent_tools(discovery_names))
    if allowed is None:
        return tools
    allowed_names = set(allowed)
    return [tool for tool in tools if tool["function"]["name"] in allowed_names]
