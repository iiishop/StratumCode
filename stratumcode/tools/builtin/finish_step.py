from __future__ import annotations

import json
from pathlib import Path

from ... import patch_authorization
from ..spec import ToolDef, ToolResult
from .common import _resolve

FINISH_STEP_VERDICTS = ("already_satisfied", "plan_conflict", "blocked")


async def _finish_step(params: dict, ctx: dict) -> ToolResult:
    root = _resolve(".", ctx)
    verdict = str(params.get("verdict") or "").strip()
    step_id = str(params.get("step_id") or "").strip()
    summary = str(params.get("summary") or "").strip()
    evidence = params.get("evidence") if isinstance(params.get("evidence"), list) else []
    if verdict not in FINISH_STEP_VERDICTS:
        return ToolResult.err("finish_step", f"unsupported verdict: {verdict}", verdict=verdict)
    if verdict == "already_satisfied" and not evidence:
        return ToolResult.err(
            "finish_step",
            "already_satisfied requires at least one evidence item",
            verdict=verdict,
            step_id=step_id,
        )
    try:
        patch_authorization.validate_step_reference(params, Path(root))
        if verdict == "already_satisfied":
            patch_authorization.mark_step_satisfied(str(params.get("authorization_id") or ""), step_id)
    except patch_authorization.AuthorizationError as exc:
        return ToolResult.err("finish_step", exc.message, code=exc.code, verdict=verdict, step_id=step_id)
    payload = {
        "status": "finished",
        "step_id": step_id,
        "verdict": verdict,
        "summary": summary,
        "evidence": evidence,
    }
    return ToolResult.ok(
        f"finish_step {verdict}",
        json.dumps(payload, ensure_ascii=False, indent=2),
        status="finished",
        step_id=step_id,
        verdict=verdict,
    )


finish_step_tool = ToolDef(
    name="finish_step",
    description="Finish an authorized implementation step without applying a file patch when it is already satisfied, conflicts with the plan, or is blocked.",
    parameters={
        "type": "object",
        "properties": {
            "authorization_id": {"type": "string"},
            "plan_hash": {"type": "string"},
            "step_id": {"type": "string"},
            "verdict": {"type": "string", "enum": list(FINISH_STEP_VERDICTS)},
            "summary": {"type": "string"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "line_range": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["reason"],
                },
            },
        },
        "required": ["authorization_id", "plan_hash", "step_id", "verdict", "summary"],
    },
    execute=_finish_step,
    event_type="tool",
)

TOOL = finish_step_tool
