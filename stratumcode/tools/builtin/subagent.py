from __future__ import annotations

from ...subagent_catalog import AVAILABLE_SUBAGENTS, normalize_agent_name
from ..spec import ToolDef, ToolResult


async def _subagent(params: dict, ctx: dict) -> ToolResult:
    agent = str(params.get("agent") or params.get("name") or "mcp-installer").strip()
    task = str(params.get("task") or params.get("hint") or params.get("url") or "").strip()
    if not task:
        return ToolResult.err("subagent", "task is required")

    from ... import subagents

    agent_name = normalize_agent_name(agent)
    agent_info = AVAILABLE_SUBAGENTS.get(agent_name)
    if agent_info is None:
        return ToolResult.err("subagent", f"unknown subagent: {agent}")

    done = {}
    for packet in subagents.run_stream(agent_name, task, ctx.get("directory", ".")):
        if packet.get("op") == "done":
            done = packet
    if done.get("error"):
        return ToolResult.err("subagent", done["error"])
    server = done.get("server") or {}
    display_name = agent_info.get("display_name") or f"@{agent_name}"
    output = (
        f"{display_name} completed. Installed {server.get('name')} MCP server with "
        f"{len(server.get('tools') or [])} tools. Status: {server.get('status')}."
    )
    return ToolResult.ok(output, output, subagent=agent_name, server=server)


subagent_tool = ToolDef(
    name="subagent",
    description="Delegate a task to one focused subagent. Available agents: mcp-installer.",
    parameters={
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "enum": list(AVAILABLE_SUBAGENTS),
                "description": "Subagent to run.",
            },
            "task": {
                "type": "string",
                "description": "Task or installation hint for the selected subagent.",
            },
        },
        "required": ["agent", "task"],
    },
    execute=_subagent,
)

TOOL = subagent_tool
