NAME = "record_evidence"
ORDER = 10
DESCRIPTION = "Record a verifiable finding from a previous tool result."
PARAMETERS = {
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
        "observation_id": {
            "type": "string",
            "description": "Observation id; defaults to source_tool_call_id.",
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
}
