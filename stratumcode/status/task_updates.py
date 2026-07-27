from __future__ import annotations

from .task_contract import _ensure_task_contract

INVESTIGATION_FALLBACK_REASON = "Investigation completed without a more specific task update."


def _seed_task_updates(analysis: dict, existing: list[dict] | None = None) -> list[dict]:
    analysis = _ensure_task_contract(analysis)
    task_id = analysis["id"]
    root_goal = analysis.get("root_goal_id") or f"{task_id}:goal"
    items = []
    if not analysis.get("root_goal_id"):
        items.append(_task_item(f"{task_id}:goal", "goal", analysis["intent"]["summary"], "active"))
    elif analysis["intent"].get("summary"):
        items.append(_task_item(f"{task_id}:work", "work", analysis["intent"]["summary"], "added", parent_id=root_goal, trace=analysis.get("reused_context_ids", [])))
    items.extend(
        _task_item(f"{task_id}:C{index}", "constraint", text, "known", parent_id=root_goal)
        for index, text in enumerate(analysis.get("constraints", []), start=1)
    )
    items.extend(
        _task_item(f"{task_id}:{item['id']}", "acceptance", item["text"], "pending", parent_id=root_goal)
        for item in analysis.get("acceptance_criteria", [])
    )
    behavior = analysis.get("behavior_contract", {})
    behavior_rows = [
        ("BI", "behavior", text)
        for text in behavior.get("inputs", [])
    ] + [
        ("BO", "behavior", text)
        for text in behavior.get("outputs", [])
    ] + [
        ("BS", "behavior", text)
        for text in behavior.get("success_behaviors", [])
    ] + [
        ("BF", "behavior", text)
        for text in behavior.get("failure_behaviors", [])
    ] + [
        ("BB", "boundary", text)
        for text in behavior.get("boundaries", [])
    ]
    items.extend(
        _task_item(f"{task_id}:{prefix}{index}", kind, text, "pending", parent_id=root_goal)
        for index, (prefix, kind, text) in enumerate(behavior_rows, start=1)
    )
    parent = f"{task_id}:work" if analysis.get("root_goal_id") else root_goal
    items.extend(
        _task_item(f"{task_id}:{item.get('id') or f'U{index}'}", "unknown", item.get("question", ""), _unknown_task_status(item), parent_id=parent)
        for index, item in enumerate(analysis.get("unknowns", []), start=1)
    )
    return _normalize_task_updates(task_id, items, existing or [])


def _task_item(item_id: str, kind: str, text: str, status: str, *, parent_id: str = "", trace: list[str] | None = None) -> dict:
    return {
        "id": item_id,
        "kind": kind,
        "text": text,
        "status": status,
        "parent_id": parent_id,
        "reason": "",
        "trace": trace or [],
    }


def _unknown_task_status(item: dict) -> str:
    strategy = item.get("resolution_strategy")
    if strategy == "clearify":
        return "blocked"
    if strategy == "deferred" or not item.get("blocking"):
        return "deferred"
    return "unknown"


def _normalize_task_updates(analysis_id: str, updates: list[dict], existing: list[dict] | None = None) -> list[dict]:
    result = []
    prior = [_normalize_existing_task(analysis_id, item) for item in existing or [] if isinstance(item, dict)]
    prior = [item for item in prior if item]
    for raw in updates or []:
        if not isinstance(raw, dict) or not str(raw.get("text") or "").strip():
            continue
        if raw.get("kind") == "clue":
            continue
        if goal := _prior_goal_by_id(prior, str(raw.get("id") or "")):
            if not any(_same_task_id(item.get("id"), goal.get("id"), analysis_id) for item in result):
                result.append(goal)
            continue
        item = dict(raw)
        item["id"] = _scoped_id(analysis_id, str(item.get("id") or ""))
        item["target_id"] = _scoped_id(analysis_id, str(item.get("target_id") or ""))
        item.setdefault("kind", "unknown")
        item.setdefault("status", "updated")
        item.setdefault("reason", "")
        item["trace"] = [str(entry) for entry in item.get("trace", [])] if isinstance(item.get("trace"), list) else []
        item["answers"] = _answers(item.get("answers"))
        matched = next((row for row in prior + result if _same_task(row, item, analysis_id)), None)
        if matched:
            if matched.get("kind") == "goal":
                if not any(_same_task(row, matched, analysis_id) for row in result):
                    result.append(dict(matched))
                continue
            item["id"] = matched.get("id") or item["id"]
            if _status_rank(item.get("status")) < _status_rank(matched.get("status")):
                item["status"] = matched.get("status")
            item = _merge_task_payload(matched, item, analysis_id)
        index = next((i for i, row in enumerate(result) if _same_task(row, item, analysis_id)), None)
        if index is None:
            result.append(item)
        elif result[index].get("kind") != "goal":
            if _status_rank(item.get("status")) < _status_rank(result[index].get("status")):
                item["status"] = result[index].get("status")
            result[index] = _merge_task_payload(result[index], item, analysis_id)
    return result


def _apply_task_updates(
    analysis_id: str,
    current: list[dict],
    updates: list[dict],
    existing: list[dict] | None = None,
) -> dict:
    before = [dict(item) for item in current or [] if isinstance(item, dict)]
    items = _normalize_task_updates(analysis_id, before + list(updates or []), existing or [])
    return {
        "items": items,
        "changes": _task_update_changes(before, items, analysis_id),
    }


def _merge_task_items(old: list[dict], new: list[dict], analysis_id: str = "") -> list[dict]:
    merged = [dict(item) for item in old or [] if isinstance(item, dict)]
    for raw in new or []:
        if not isinstance(raw, dict) or not raw.get("text"):
            continue
        item = dict(raw)
        index = next((i for i, row in enumerate(merged) if _same_task(row, item, analysis_id)), None)
        if index is None:
            merged.append(item)
            continue
        if merged[index].get("kind") == "goal":
            continue
        if _status_rank(item.get("status")) < _status_rank(merged[index].get("status")):
            item["status"] = merged[index].get("status")
        merged[index] = _merge_task_payload(merged[index], item, analysis_id)
    return merged


def _finalize_task_statuses(items: list[dict], investigation: dict) -> list[dict]:
    next_step = ((investigation.get("step_result") or {}).get("next_step") or "").strip()
    if next_step != "done" and not investigation.get("ready_for_patch_planning"):
        return items
    resolved_unknowns = _resolved_unknowns(investigation)
    done_status = "known"
    final = []
    for item in items:
        status = item.get("status") or "unknown"
        kind = item.get("kind")
        resolution = resolved_unknowns.get(_task_id_tail(item.get("id")))
        resolved = bool(resolution)
        clear_unknown = status == "unknown" and resolved
        clear_blocked = status == "blocked" and resolved
        clear_deferred = status == "deferred" and resolved
        enrich_known = (
            item.get("kind") == "unknown"
            and status == "known"
            and resolved
            and (not item.get("answers") or item.get("reason") == INVESTIGATION_FALLBACK_REASON)
        )
        if (
            kind in {"unknown", "clue", "work"}
            and (status in {"pending", "added", "updated", "active"} or clear_unknown or clear_blocked or clear_deferred or enrich_known)
        ):
            resolution_update = _resolution_task_update(item, resolution) if item.get("kind") == "unknown" else None
            if resolution_update:
                item = _merge_task_payload(item, resolution_update)
            else:
                item = {
                    **item,
                    "status": done_status,
                    "reason": item.get("reason") or INVESTIGATION_FALLBACK_REASON,
                }
        elif (
            next_step == "done"
            and kind in {"acceptance", "behavior", "boundary"}
            and status == "pending"
        ):
            item = {
                **item,
                "status": done_status,
                "reason": item.get("reason") or INVESTIGATION_FALLBACK_REASON,
            }
        final.append(item)
    return final


def _resolved_unknown_ids(investigation: dict) -> set[str]:
    return set(_resolved_unknowns(investigation))


def _resolved_unknowns(investigation: dict) -> dict[str, dict]:
    resolved = {}
    for item in investigation.get("resolutions", []):
        if not isinstance(item, dict) or str(item.get("status") or "") != "resolved":
            continue
        unknown_id = str(item.get("unknown_id") or "").strip()
        if unknown_id:
            resolved[unknown_id] = item
    return resolved


def _resolution_task_update(item: dict, resolution: dict | None) -> dict | None:
    if not resolution:
        return None
    trace = _resolution_trace(resolution)
    answer = str(resolution.get("answer") or "").strip()
    reason = str(resolution.get("reason") or "").strip()
    if not (answer or reason or trace):
        return None
    return {
        "id": item.get("id", ""),
        "target_id": item.get("id", ""),
        "kind": "unknown",
        "text": answer or item.get("text", ""),
        "status": "known",
        "reason": reason if item.get("reason") == INVESTIGATION_FALLBACK_REASON else item.get("reason") or reason,
        "trace": trace,
        "answers": [{
            "source": "investigation",
            "text": answer,
            "reason": reason,
            "trace": trace,
        }] if answer else [],
    }


def _resolution_trace(resolution: dict) -> list[str]:
    evidence = resolution.get("evidence") if isinstance(resolution.get("evidence"), list) else []
    belief_ids = resolution.get("belief_ids") if isinstance(resolution.get("belief_ids"), list) else []
    return [str(item).strip() for item in (evidence or belief_ids) if str(item).strip()][:6]


def _normalize_existing_task(analysis_id: str, item: dict) -> dict | None:
    item_id = str(item.get("id") or "")
    if item.get("kind") == "goal":
        return dict(item)
    if item_id.startswith(f"{analysis_id}:"):
        result = dict(item)
        result["target_id"] = _scoped_id(analysis_id, str(result.get("target_id") or ""))
        return result
    return None


def _prior_goal_by_id(prior: list[dict], item_id: str) -> dict | None:
    if not item_id:
        return None
    return next((dict(item) for item in prior if item.get("kind") == "goal" and item.get("id") == item_id), None)


def _same_task(left: dict, right: dict, analysis_id: str = "") -> bool:
    analysis_id = analysis_id or _task_id_scope(right.get("id")) or _task_id_scope(left.get("id"))
    if _same_task_id(left.get("id"), right.get("id"), analysis_id):
        return True
    if _same_task_id(left.get("id"), right.get("target_id"), analysis_id) or _same_task_id(right.get("id"), left.get("target_id"), analysis_id):
        return True
    return False


def _task_update_changes(before: list[dict], after: list[dict], analysis_id: str) -> list[dict]:
    changes = []
    for item in after:
        matched = next((row for row in before if _same_task(row, item, analysis_id)), None)
        if not matched:
            changes.append({"action": "add", "before": None, "item": item})
            continue
        if matched.get("status") != item.get("status"):
            changes.append({"action": "status", "before": matched, "item": item})
        elif _task_payload(matched) != _task_payload(item):
            changes.append({"action": "update", "before": matched, "item": item})
    return changes


def _task_payload(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "target_id": item.get("target_id"),
        "kind": item.get("kind"),
        "text": item.get("text"),
        "reason": item.get("reason"),
        "trace": [str(entry) for entry in item.get("trace", [])] if isinstance(item.get("trace"), list) else [],
        "answers": _answers(item.get("answers")),
    }


def _status_rank(status: str | None) -> int:
    return {"unknown": 0, "pending": 0, "added": 1, "updated": 1, "deferred": 2, "blocked": 2, "active": 2, "known": 3}.get(status or "", 0)


def _task_id_scope(value: str | None) -> str:
    value = str(value or "")
    prefix = value.split(":", 1)[0] if ":" in value else ""
    return prefix if prefix.startswith("task-") else ""


def _task_id_tail(value: str | None) -> str:
    value = str(value or "")
    return value.split(":", 1)[1] if _task_id_scope(value) else value


def _same_task_id(left: str | None, right: str | None, analysis_id: str = "") -> bool:
    left = str(left or "")
    right = str(right or "")
    if not left or not right:
        return False
    if left == right:
        return True
    left_scope = _task_id_scope(left)
    right_scope = _task_id_scope(right)
    if left_scope and right_scope:
        return False
    if left_scope and left_scope != analysis_id:
        return False
    if right_scope and right_scope != analysis_id:
        return False
    return _task_id_tail(left) == _task_id_tail(right)


def _answers(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    answers = []
    for raw in value:
        if not isinstance(raw, dict):
            raw = {"text": raw}
        text = str(raw.get("text") or raw.get("answer") or "").strip()
        if not text:
            continue
        trace = raw.get("trace") if isinstance(raw.get("trace"), list) else []
        answers.append({
            "source": str(raw.get("source") or "investigation").strip(),
            "text": text,
            "reason": str(raw.get("reason") or "").strip(),
            "trace": [str(item) for item in trace],
        })
    return answers


def _merge_answers(old, new) -> list[dict]:
    merged = []
    seen = set()
    for answer in _answers(old) + _answers(new):
        key = (answer["source"], answer["text"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(answer)
    return merged


def _scoped_id(analysis_id: str, item_id: str) -> str:
    if not item_id:
        return ""
    return f"{analysis_id}:{_task_id_tail(item_id)}"


def _scoped_items(analysis_id: str, items: list[dict]) -> list[dict]:
    scoped = []
    for item in items or []:
        if isinstance(item, dict):
            next_item = dict(item)
            next_item["id"] = _scoped_id(analysis_id, str(next_item.get("id") or ""))
            scoped.append(next_item)
    return scoped


def _merge_items_by_id(old: list[dict], new: list[dict]) -> list[dict]:
    merged = [dict(item) for item in old or [] if isinstance(item, dict)]
    by_id = {item.get("id"): index for index, item in enumerate(merged) if item.get("id")}
    for item in new or []:
        if not isinstance(item, dict):
            continue
        item = dict(item)
        item_id = item.get("id")
        if item_id and item_id in by_id:
            merged[by_id[item_id]] = _merge_task_payload(merged[by_id[item_id]], item)
        else:
            if item_id:
                by_id[item_id] = len(merged)
            merged.append(item)
    return merged


def _merge_task_payload(existing: dict, incoming: dict, analysis_id: str = "") -> dict:
    merged = {
        **existing,
        **incoming,
        "id": existing.get("id") or incoming.get("id"),
        "answers": _merge_answers(existing.get("answers"), incoming.get("answers")),
    }
    if existing.get("kind") == "unknown" and incoming.get("kind") == "unknown":
        question = str(existing.get("text") or "").strip()
        replacement = str(incoming.get("text") or "").strip()
        if question and replacement and replacement != question:
            merged["text"] = question
            if _looks_like_question(replacement) and not _answers(incoming.get("answers")):
                merged["status"] = existing.get("status") or merged.get("status")
                merged["reason"] = existing.get("reason") or ""
            merged["answers"] = _merge_answers(
                merged.get("answers"),
                _replacement_answer(incoming, replacement, analysis_id),
            )
        elif question:
            merged["text"] = question
    return merged


def _replacement_answer(item: dict, text: str, analysis_id: str = "") -> list[dict]:
    if (
        not text
        or _looks_like_question(text)
        or _same_task_id(text, item.get("id"), analysis_id)
        or _same_task_id(text, item.get("target_id"), analysis_id)
    ):
        return []
    return [{
        "source": str(item.get("answer_source") or "investigation").strip(),
        "text": text,
        "reason": str(item.get("reason") or "").strip(),
        "trace": [str(entry) for entry in item.get("trace", [])] if isinstance(item.get("trace"), list) else [],
    }]


def _looks_like_question(text: str) -> bool:
    return "?" in text or "？" in text


def _merge_findings(old: list[str], new: list[str]) -> list[str]:
    merged = list(old or [])
    seen = set(merged)
    for item in new or []:
        if item and item not in seen:
            merged.append(item)
            seen.add(item)
    return merged


def _investigation_continuation_findings(investigation: dict | None) -> list[str]:
    if not investigation:
        return []
    lines = ["CURRENT INVESTIGATION FINDINGS (do not repeat; continue only unresolved unknowns):"]
    if investigation.get("summary"):
        lines.append(f"Summary: {investigation['summary']}")
    step = investigation.get("step_result") if isinstance(investigation.get("step_result"), dict) else {}
    if step.get("continue_reason"):
        lines.append(f"Continue because: {step['continue_reason']}")
    for item in investigation.get("beliefs", [])[:8] if isinstance(investigation.get("beliefs"), list) else []:
        if isinstance(item, dict) and item.get("statement"):
            lines.append(f"- {item.get('id') or 'belief'}: {item['statement']}")
    for item in investigation.get("task_updates", [])[:8] if isinstance(investigation.get("task_updates"), list) else []:
        if isinstance(item, dict) and item.get("status") == "unknown" and item.get("text"):
            lines.append(f"- unresolved {item.get('id') or 'unknown'}: {item['text']}")
    return lines


def _beliefs_as_knowledge(analysis_id: str, beliefs: list[dict]) -> list[dict]:
    items = []
    for index, belief in enumerate(beliefs or [], start=1):
        if isinstance(belief, dict) and belief.get("statement"):
            evidence = belief.get("evidence") if isinstance(belief.get("evidence"), list) else []
            items.append({
                "id": f"{analysis_id}:B{index}",
                "turn_id": analysis_id,
                "statement": belief["statement"],
                "status": belief.get("status", "supported"),
                "observation_ids": [_scoped_id(analysis_id, str(item)) for item in evidence],
            })
    return items
