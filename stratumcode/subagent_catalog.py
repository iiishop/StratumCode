from __future__ import annotations


AVAILABLE_SUBAGENTS = {
    "mcp-installer": {
        "name": "mcp-installer",
        "display_name": "@mcp-installer",
        "task": "Install MCP servers from docs, URLs, or config hints.",
    },
}


def list_available() -> list[dict]:
    return [dict(agent) for agent in AVAILABLE_SUBAGENTS.values()]


def normalize_agent_name(agent: str) -> str:
    return str(agent or "").strip().removeprefix("@").casefold()
