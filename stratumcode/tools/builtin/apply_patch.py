from __future__ import annotations

import json

from ...patch_engine import PatchError, apply_patch_request
from ..spec import ToolDef, ToolResult
from .common import _resolve


async def _apply_patch(params: dict, ctx: dict) -> ToolResult:
    root = _resolve(".", ctx)
    try:
        result = apply_patch_request(params, root)
    except PatchError as exc:
        return ToolResult.err("apply_patch", exc.message, code=exc.code)
    return ToolResult.ok(
        "patch applied",
        json.dumps(result, ensure_ascii=False, indent=2),
        **{key: result.get(key) for key in ("status", "patch_id", "authorization_id", "step_id", "rollback_id")},
    )


apply_patch_tool = ToolDef(
    name="apply_patch",
    description="Apply structured, snapshot-checked file patch operations. Supports replace_exact, insert_before, insert_after, delete_exact, and create_file.",
    parameters={
        "type": "object",
        "properties": {
            "authorization_id": {"type": "string"},
            "plan_hash": {"type": "string"},
            "patch_id": {"type": "string"},
            "step_id": {"type": "string"},
            "reason": {"type": "string"},
            "acceptance_ids": {"type": "array", "items": {"type": "string"}},
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "snapshot_id": {"type": "string"},
                        "mode": {"type": "string", "enum": ["modify", "create"]},
                        "content": {"type": "string"},
                        "operations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "op": {"type": "string", "enum": ["replace_exact", "insert_before", "insert_after", "delete_exact"]},
                                    "old_text": {"type": "string"},
                                    "new_text": {"type": "string"},
                                    "anchor": {"type": "string"},
                                    "text": {"type": "string"},
                                    "expected_count": {"type": "integer"},
                                },
                                "required": ["op"],
                            },
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        "required": ["authorization_id", "plan_hash", "patch_id", "step_id", "reason", "files"],
    },
    execute=_apply_patch,
)

TOOL = apply_patch_tool
