from __future__ import annotations

from ..spec import ToolDef, ToolResult


async def _clearify(params: dict, ctx: dict) -> ToolResult:
    return ToolResult.err(
        "clearify",
        "clearify is handled by the investigation runtime and cannot be run directly.",
    )


TOOL = ToolDef(
    name="clearify",
    description="Ask the user one blocking clarification question and resume with their answer.",
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The exact user-facing question to ask.",
            },
            "options": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "label": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["label", "value"],
                },
                "description": "Exactly three candidate answers.",
            },
        },
        "required": ["question", "options"],
    },
    execute=_clearify,
)
