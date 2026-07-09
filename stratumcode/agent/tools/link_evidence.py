NAME = "link_evidence"
ORDER = 20
DESCRIPTION = "Describe how one recorded evidence item changes the weight of another."
PARAMETERS = {
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
}
