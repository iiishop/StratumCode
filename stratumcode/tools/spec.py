"""Tool spec — the core type shared by all tool sources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    title: str = ""
    output: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def ok(title: str, output: str, **meta) -> "ToolResult":
        return ToolResult(title=title, output=output, metadata=meta)

    @staticmethod
    def err(title: str, output: str) -> "ToolResult":
        return ToolResult(title="[error] " + title, output=output)


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object
    execute: Callable[..., Any]  # async (params: dict, ctx: dict) -> ToolResult
    format_error: Callable[[Exception], str] = lambda e: str(e)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
