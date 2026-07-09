NAME = "conclude"
ORDER = 30
DESCRIPTION = "Finish the evidence stage after checking plausible counter-evidence."
PARAMETERS = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "Concise synthesis grounded in recorded evidence ids",
        },
    },
    "required": ["summary"],
}
