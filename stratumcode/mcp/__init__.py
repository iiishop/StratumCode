"""MCP client — connect to external MCP servers, register their tools."""

from __future__ import annotations

from ..tools.spec import ToolDef, ToolResult
from ..tools import registry


def load_from_config(mcp_configs: list[dict]) -> None:
    """Stub — connect to MCP servers as subprocesses over stdio JSON-RPC.
    
    config:
        { "name": "filesystem", "command": "npx", "args": ["-y", "..."], "env": {} }
    """
    for cfg in mcp_configs:
        name = cfg.get("name", "mcp-unknown")
        _register_mcp_stub(name)


def _register_mcp_stub(name: str) -> None:
    async def ping(params: dict, ctx: dict) -> ToolResult:
        return ToolResult.ok("mcp-ping", f"MCP server '{name}' stub")

    registry.register(ToolDef(
        name=f"mcp_{name}_ping",
        description=f"MCP stub — server '{name}' would provide tools here. (MCP integration not implemented)",
        parameters={"type": "object", "properties": {}},
        execute=ping,
    ))
