from __future__ import annotations

import json

from ...patch_engine import list_patch_transactions
from ..spec import ToolDef, ToolResult
from .common import _resolve


async def _patch_history(params: dict, ctx: dict) -> ToolResult:
    root = _resolve(".", ctx)
    limit = params.get("limit", 50)
    if not isinstance(limit, int):
        limit = 50
    items = list_patch_transactions(root, limit=limit)
    return ToolResult.ok(
        f"{len(items)} patch transactions",
        json.dumps({"items": items}, ensure_ascii=False, indent=2),
        count=len(items),
    )


patch_history_tool = ToolDef(
    name="patch_history",
    description="List structured patch transactions for the current workspace, including rollback_id and transaction state.",
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
    },
    execute=_patch_history,
)

TOOL = patch_history_tool
