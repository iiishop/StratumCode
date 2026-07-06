"""Tool registry — unified listing from all sources."""

from __future__ import annotations

from .spec import ToolDef
from .builtin import BUILTIN


_registry: dict[str, ToolDef] = {}


def _register_builtins() -> None:
    for t in BUILTIN:
        _registry[t.name] = t


def register(tool: ToolDef, *, replace: bool = False) -> None:
    if not isinstance(tool, ToolDef):
        raise TypeError("registered tools must be ToolDef instances")
    if tool.name in _registry and not replace:
        raise ValueError(f"tool already registered: {tool.name}")
    _registry[tool.name] = tool


def list_all() -> list[ToolDef]:
    return sorted(_registry.values(), key=lambda t: t.name)


def get(name: str) -> ToolDef | None:
    return _registry.get(name)


def describe() -> str:
    """Compact text description for the system prompt."""
    lines = []
    for t in list_all():
        lines.append(f"- {t.name}: {t.description}")
    return "\n".join(lines)


# ── init on import ──
_register_builtins()
