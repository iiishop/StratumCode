from __future__ import annotations


AVAILABLE_SUBAGENTS = {
    "mcp-installer": {
        "name": "mcp-installer",
        "display_name": "@mcp-installer",
        "task": "Install MCP servers from docs, URLs, or config hints.",
        "guide": (
            "This subagent installs and configures MCP servers from hints. "
            "Best for: skills about MCP server setup, model-context-protocol "
            "configuration, and server discovery; skills this agent can use to "
            "pick, validate, and wire up an MCP integration."
        ),
    },
    "hypothesis-verifier": {
        "name": "hypothesis-verifier",
        "display_name": "@hypothesis-verifier",
        "task": "Verify code hypotheses by gathering and recording grounded evidence.",
        "guide": (
            "This subagent verifies code hypotheses by collecting grounded "
            "evidence. Best for: skills about evidence gathering, read/grep "
            "verification workflows, grounding and observation recording "
            "conventions."
        ),
    },
    "skill-placer": {
        "name": "skill-placer",
        "display_name": "@skill-placer",
        "task": "Decide which skill target (global, state, or subagent) best fits a skill.",
        "guide": (
            "This subagent reads a skill and the current skill-target guides, "
            "then recommends the best placement: global, a specific agent "
            "state, or a specific subagent. Best for: skills about the "
            "StratumCode skill-target system, placement heuristics, and "
            "skill-library organization."
        ),
    },
}


def list_available() -> list[dict]:
    return [dict(agent) for agent in AVAILABLE_SUBAGENTS.values()]


def normalize_agent_name(agent: str) -> str:
    return str(agent or "").strip().removeprefix("@").casefold()
