from __future__ import annotations

from uuid import uuid4


def queue_clearify(run, question: str, *, reason: str = "", unknown_id: str = "") -> dict:
    question = " ".join(str(question or "").split())
    if not question:
        raise ValueError("clearify question is required")
    unknown_id = str(unknown_id or "").strip() or f"clearify-{uuid4().hex[:8]}"
    item = {
        "id": unknown_id,
        "question": question,
        "blocking": True,
        "type": "product_decision",
        "resolution_strategy": "clearify",
    }
    investigation = dict(run.last_investigation or {})
    unknowns = [
        dict(value) for value in investigation.get("unknowns", [])
        if isinstance(value, dict) and value.get("id") != item["id"]
    ]
    investigation.update({
        "unknowns": unknowns + [item],
        "ready_for_patch_planning": False,
        "step_result": {
            "next_step": "continue_investigation",
            "continue_reason": question,
            "target_unknown_ids": [item["id"]],
        },
    })
    run.last_investigation = investigation
    if reason:
        run.continuation_context.append(str(reason).strip())
    return item
