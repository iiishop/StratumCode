from __future__ import annotations


AVAILABLE_SUBAGENTS = {
    "mcp-installer": {
        "name": "mcp-installer",
        "display_name": "@mcp-installer",
        "task": "Install MCP servers from docs, URLs, or config hints.",
    },
    "hypothesis-verifier": {
        "name": "hypothesis-verifier",
        "display_name": "@hypothesis-verifier",
        "task": "Verify code hypotheses by gathering and recording grounded evidence.",
    },
}


def list_available() -> list[dict]:
    return [dict(agent) for agent in AVAILABLE_SUBAGENTS.values()]


def normalize_agent_name(agent: str) -> str:
    return str(agent or "").strip().removeprefix("@").casefold()
