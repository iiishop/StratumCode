from __future__ import annotations

from .task_contract import _ensure_task_contract

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
    items.extend(
        _task_item(f"{task_id}:H{index}", "hypothesis", item["text"], "unknown", parent_id=root_goal)
        for index, item in enumerate(analysis.get("hypotheses", []), start=1)
    )
    items.extend(
        _task_item(f"{task_id}:L{index}", "clue", clue.get("path") or clue.get("value"), "pending", parent_id=root_goal)
        for index, clue in enumerate(analysis.get("clues", []), start=1)
    )
    parent = f"{task_id}:work" if analysis.get("root_goal_id") else root_goal
    items.extend(
        _task_item(f"{task_id}:{item.get('id') or f'U{index}'}", "unknown", item.get("question", ""), "unknown", parent_id=parent)
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


def _normalize_task_updates(analysis_id: str, updates: list[dict], existing: list[dict] | None = None) -> list[dict]:
    result = []
    prior = [dict(item) for item in existing or [] if isinstance(item, dict)]
    for raw in updates or []:
        if not isinstance(raw, dict) or not str(raw.get("text") or "").strip():
            continue
        item = dict(raw)
        item["id"] = _scoped_id(analysis_id, str(item.get("id") or ""))
        item.setdefault("kind", "unknown")
        item.setdefault("status", "updated")
        item.setdefault("reason", "")
        item["trace"] = [str(entry) for entry in item.get("trace", [])] if isinstance(item.get("trace"), list) else []
        item["answers"] = _answers(item.get("answers"))
        matched = next((row for row in prior + result if _same_task(row, item)), None)
        if matched:
            if matched.get("kind") == "goal":
                item = dict(matched)
                item["trace"] = [str(entry) for entry in item.get("trace", [])] if isinstance(item.get("trace"), list) else []
                item["answers"] = _answers(item.get("answers"))
                result.append(item)
                continue
            item["id"] = matched.get("id") or item["id"]
            item["answers"] = _merge_answers(matched.get("answers"), item.get("answers"))
        index = next((i for i, row in enumerate(result) if _same_task(row, item)), None)
        if index is None:
            result.append(item)
        elif result[index].get("kind") != "goal":
            result[index] = {
                **result[index],
                **item,
                "id": result[index].get("id") or item["id"],
                "answers": _merge_answers(result[index].get("answers"), item.get("answers")),
            }
    return result


def _finalize_task_statuses(items: list[dict], investigation: dict) -> list[dict]:
    next_step = ((investigation.get("step_result") or {}).get("next_step") or "").strip()
    if next_step == "ask_user" or (next_step != "done" and not investigation.get("ready_for_patch_planning")):
        return items
    resolved_ids = _resolved_unknown_ids(investigation)
    done_status = "known"
    final = []
    for item in items:
        status = item.get("status") or "unknown"
        resolved = _task_id_tail(item.get("id")) in resolved_ids
        clear_unknown = status == "unknown" and resolved
        clear_blocked = status == "blocked" and resolved
        if (
            item.get("kind") in {"unknown", "hypothesis", "clue", "work"}
            and (status in {"pending", "added", "updated", "active"} or clear_unknown or clear_blocked)
        ):
            item = {
                **item,
                "status": done_status,
                "reason": item.get("reason") or "Investigation completed without a more specific task update.",
            }
        final.append(item)
    return final


def _resolved_unknown_ids(investigation: dict) -> set[str]:
    return {
        str(item.get("unknown_id") or "").strip()
        for item in investigation.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") == "resolved"
        and str(item.get("unknown_id") or "").strip()
    }


def _same_task(left: dict, right: dict) -> bool:
    if _same_task_id(left.get("id"), right.get("id")):
        return True
    if _same_task_id(left.get("id"), right.get("target_id")) or _same_task_id(right.get("id"), left.get("target_id")):
        return True
    left_trace = left.get("trace") if isinstance(left.get("trace"), list) else []
    right_trace = right.get("trace") if isinstance(right.get("trace"), list) else []
    left_ids = [left.get("id"), *left_trace]
    right_ids = [right.get("id"), *right_trace]
    if any(_same_task_id(left_id, right_id) for left_id in left_ids for right_id in right_ids):
        return True
    return False


def _task_id_tail(value: str | None) -> str:
    return str(value or "").rsplit(":", 1)[-1]


def _same_task_id(left: str | None, right: str | None) -> bool:
    left = str(left or "")
    right = str(right or "")
    if not left or not right:
        return False
    if left == right:
        return True
    if ":" in left and ":" in right:
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
    return item_id if ":" in item_id else f"{analysis_id}:{item_id}"


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
            merged[by_id[item_id]] = {**merged[by_id[item_id]], **item}
        else:
            if item_id:
                by_id[item_id] = len(merged)
            merged.append(item)
    return merged


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
