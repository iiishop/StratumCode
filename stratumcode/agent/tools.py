from __future__ import annotations

from functools import lru_cache

from ..tools import registry

CONTROL_TOOL_NAMES = ("record_evidence", "link_evidence", "conclude")
COMPACT_DESCRIPTIONS = {
    "read": "Read a workspace file.",
    "glob": "List workspace files by glob.",
    "grep": "Search workspace files with regex.",
    "webfetch": "Fetch URL text.",
    "websearch": "Search the public web.",
}


def _compact_schema(value):
    if isinstance(value, dict):
        return {
            key: _compact_schema(item)
            for key, item in value.items()
            if key != "description"
        }
    if isinstance(value, list):
        return [_compact_schema(item) for item in value]
    return value


def openai_tool_schema(name: str, description: str, parameters: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": COMPACT_DESCRIPTIONS.get(name, description),
            "parameters": _compact_schema(parameters),
        },
    }


CONTROL_TOOLS = (
    openai_tool_schema(
        "record_evidence",
        "Record a verifiable finding from a previous tool result.",
        {
            "type": "object",
            "properties": {
                "evidence_id": {"type": "string", "description": "Stable short id, e.g. E1"},
                "claim": {"type": "string"},
                "stance": {"type": "string", "enum": ["support", "oppose"]},
                "strength": {"type": "number", "minimum": 0, "maximum": 1},
                "source_type": {"type": "string", "enum": ["runtime", "code", "document", "web"]},
                "source_uri": {
                    "type": "string",
                    "description": "Workspace-relative path with lines, or full URL",
                },
                "excerpt": {
                    "type": "string",
                    "description": "Exact excerpt copied from the cited tool output",
                },
                "source_tool_call_id": {
                    "type": "string",
                    "description": "The id of the tool call that produced this excerpt",
                },
            },
            "required": [
                "evidence_id",
                "claim",
                "stance",
                "strength",
                "source_type",
                "source_uri",
                "excerpt",
                "source_tool_call_id",
            ],
        },
    ),
    openai_tool_schema(
        "link_evidence",
        "Describe how one recorded evidence item changes the weight of another.",
        {
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "target_id": {"type": "string"},
                "relation": {"type": "string", "enum": ["corroborates", "contradicts", "qualifies"]},
                "relationship": {
                    "type": "string",
                    "enum": ["corroborates", "contradicts", "qualifies"],
                    "description": "Alias for relation.",
                },
                "impact": {"type": "number", "minimum": 0, "maximum": 1},
                "explanation": {"type": "string"},
            },
            "required": ["source_id", "target_id", "relation", "impact", "explanation"],
        },
    ),
    openai_tool_schema(
        "conclude",
        "Finish the evidence stage after checking plausible counter-evidence.",
        {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Concise synthesis grounded in recorded evidence ids",
                },
            },
            "required": ["summary"],
        },
    ),
)


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
