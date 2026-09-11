"""Separate out-of-scope work from the current investigation, without executing it."""
from __future__ import annotations


def partition_follow_ups(*sources: dict) -> tuple[list[dict], list[dict]]:
    nodes, follow_ups, resolutions = {}, {}, {}
    for source in sources:
        for item in source.get("follow_ups", []):
            follow_ups[item["id"]] = dict(item)
        for field in ("unknowns", "new_unknowns"):
            for item in source.get(field, []):
                if isinstance(item, dict) and item.get("id"):
                    nodes[item["id"]] = dict(item)
        for item in source.get("resolutions", []):
            resolutions[item.get("unknown_id")] = item
    for node_id, node in nodes.items():
        resolution = resolutions.get(node_id, {})
        if node.get("resolution_strategy") != "deferred" and not (
            resolution.get("status") == "deferred" and not node.get("blocking", True)
        ):
            continue
        item_id = f"FOLLOWUP:{node_id}"
        follow_ups[item_id] = {
            **follow_ups.get(item_id, {}),
            "id": item_id, "question": node.get("question", ""),
            "status": "pending", "source_unknown_id": node_id,
            "parent_id": node.get("parent_id"),
            "reason": resolution.get("reason") or node.get("why") or "Outside this investigation's scope",
            "required_capability": node.get("required_capability") or "Must be scoped before execution",
            "acceptance_criteria_ids": node.get("acceptance_criteria_ids", []),
            "source_unknown": node,
            "source_resolution": resolution or follow_ups.get(item_id, {}).get("source_resolution", {}),
        }
    transferred = {item.get("source_unknown_id") for item in follow_ups.values()}
    active = []
    for node_id, node in nodes.items():
        if node_id in transferred:
            continue
        parent, seen = node.get("parent_id"), set()
        while parent in transferred and parent not in seen:
            seen.add(parent)
            parent = nodes.get(parent, {}).get("parent_id")
        node["parent_id"] = parent
        active.append(node)
    return active, list(follow_ups.values())
