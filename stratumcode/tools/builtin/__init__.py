"""Builtin tools discovered from sibling modules."""

from __future__ import annotations

from importlib import import_module
from pkgutil import iter_modules

from ..spec import ToolDef


def _discover() -> list[ToolDef]:
    tools: list[ToolDef] = []
    for module_info in iter_modules(__path__):
        name = module_info.name
        if name.startswith("_") or name == "common":
            continue
        module = import_module(f"{__name__}.{name}")
        tool = getattr(module, "TOOL", None)
        if tool is None:
            continue
        if not isinstance(tool, ToolDef):
            raise TypeError(f"{module.__name__}.TOOL must be a ToolDef")
        tools.append(tool)
    return sorted(tools, key=lambda tool: tool.name)


BUILTIN: list[ToolDef] = _discover()

# Backwards-compatible imports for tests and internal callers.
from .common import _expand_braces, _ignored, _resolve  # noqa: E402,F401
from .glob import _glob, glob_tool  # noqa: E402,F401
from .grep import _grep, grep_tool  # noqa: E402,F401
from .read import _read, read_tool  # noqa: E402,F401
from .subagent import _subagent, subagent_tool  # noqa: E402,F401
from .webfetch import _validate_web_url, _webfetch, webfetch_tool  # noqa: E402,F401
from .websearch import _BingParser, _DuckDuckGoParser, _decode_ddg_url, _websearch, websearch_tool  # noqa: E402,F401


__all__ = [
    "BUILTIN",
    "_BingParser",
    "_DuckDuckGoParser",
    "_decode_ddg_url",
    "_expand_braces",
    "_glob",
    "_grep",
    "_ignored",
    "_read",
    "_resolve",
    "_subagent",
    "_validate_web_url",
    "_webfetch",
    "_websearch",
    "glob_tool",
    "grep_tool",
    "read_tool",
    "subagent_tool",
    "webfetch_tool",
    "websearch_tool",
]
