from __future__ import annotations

from uuid import uuid4


def queue_clearify(
    run,
    question: str,
    *,
    reason: str = "",
    unknown_id: str = "",
    unknown_type: str = "product_decision",
) -> dict:
    question = " ".join(str(question or "").split())
    if not question:
        raise ValueError("clearify question is required")
    if unknown_type != "product_decision":
        raise ValueError("clearify only accepts product_decision unknowns")
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


def queue_investigation_unknown(run, question: str, *, reason: str = "", unknown_id: str = "") -> dict:
    question = " ".join(str(question or "").split())
    if not question:
        raise ValueError("investigation question is required")
    unknown_id = str(unknown_id or "").strip() or f"investigate-{uuid4().hex[:8]}"
    item = {
        "id": unknown_id,
        "question": question,
        "blocking": True,
        "type": "code_fact",
        "resolution_strategy": "investigate_project",
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
            "unresolved_unknown_ids": [item["id"]],
        },
    })
    run.last_investigation = investigation
    if reason:
        run.continuation_context.append(str(reason).strip())
    return item


def _same_unknown_id(a: str, b: str) -> bool:
    a = str(a or "").strip()
    b = str(b or "").strip()
    return bool(a) and bool(b) and (a == b or a.rsplit(":", 1)[-1] == b.rsplit(":", 1)[-1])


def _answer_text(payload: dict) -> str:
    text = str(payload.get("response") or payload.get("selected_option_label") or "").strip()
    return " ".join(text.split())


def apply_clearify_answer(run, *, resume_state: str, unknown_id: str, answer_payload: dict) -> None:
    """把用户对 clearify 问题的回答注入 run，供 resume 后的下一轮使用。

    - 统一写入 run.continuation_context（模型上下文可见）
    - investigating 恢复时额外注入 CLEARIFY_RESOLUTION_REASON resolution，
      让 investigator 的 answered_by_previous_round 机制把该 unknown 标记为已答，
      避免模型下一轮重复提问。
    """
    from .. import investigator

    text = _answer_text(answer_payload)
    if not text:
        raise ValueError("clearify answer is empty")
    run.continuation_context.append(f"[User answered clearify] {text}")

    if resume_state != "investigating" or not unknown_id:
        return

    resolution = {
        "unknown_id": unknown_id,
        "status": "resolved",
        "answer": text,
        "evidence": [],
        "belief_ids": [],
        "reason": investigator.CLEARIFY_RESOLUTION_REASON,
    }
    investigation = dict(run.last_investigation or {})
    resolutions = [
        dict(item)
        for item in investigation.get("resolutions", [])
        if isinstance(item, dict)
        and not _same_unknown_id(item.get("unknown_id"), unknown_id)
    ]
    resolutions.append(resolution)
    investigation["resolutions"] = resolutions
    run.last_investigation = investigation
    run.findings.append(f"User answered {unknown_id}: {text}")
