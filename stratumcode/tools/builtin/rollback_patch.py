from __future__ import annotations

import json

from ...patch_engine import PatchError, rollback_patch
from ..spec import ToolDef, ToolResult


async def _rollback_patch(params: dict, ctx: dict) -> ToolResult:
    try:
        result = rollback_patch(str(params.get("rollback_id") or ""), force=bool(params.get("force", False)))
    except PatchError as exc:
        return ToolResult.err("rollback_patch", exc.message, code=exc.code)
    return ToolResult.ok(
        "patch rolled back",
        json.dumps(result, ensure_ascii=False, indent=2),
        status=result.get("status"),
        rollback_id=result.get("rollback_id"),
    )


rollback_patch_tool = ToolDef(
    name="rollback_patch",
    description="Rollback a committed structured patch by rollback_id. Refuses rollback if target files changed unless force is true.",
    parameters={
        "type": "object",
        "properties": {
            "rollback_id": {"type": "string"},
            "force": {"type": "boolean"},
        },
        "required": ["rollback_id"],
    },
    execute=_rollback_patch,
)

TOOL = rollback_patch_tool
