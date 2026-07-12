NAME = "report_step"
ORDER = 40
DESCRIPTION = "Report the structured result of this evidence step and what the loop should do next."
PARAMETERS = {
    "type": "object",
    "properties": {
        "next_step": {
            "type": "string",
            "enum": ["continue_investigation", "ask_user", "write_code", "done", "failed"],
        },
        "continue_reason": {
            "type": "string",
            "description": "Why this next_step is correct. Must agree with next_step.",
        },
        "target_unknown_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Required only for continue_investigation.",
        },
        "unknowns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "blocking": {"type": "boolean"},
                    "resolution_strategy": {
                        "type": "string",
                        "enum": ["investigate_project", "ask_user", "deferred"],
                    },
                    "resolved_by_evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "resolved_by_belief_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["id", "question", "blocking", "resolution_strategy"],
            },
        },
        "beliefs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "statement": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [
                            "unverified",
                            "plausible",
                            "supported",
                            "strongly_supported",
                            "runtime_confirmed",
                            "contradicted",
                            "invalidated",
                        ],
                    },
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "observation_ids": {"type": "array", "items": {"type": "string"}},
                    "resolves_unknown_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "statement", "status"],
            },
        },
    },
    "required": ["next_step", "continue_reason"],
}
