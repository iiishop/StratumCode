"""Dependency traversal and deterministic Root Audit gates."""
from __future__ import annotations

import hashlib
import json

from .ids import _normalize_unknown_id, _question_key, _unknowns


def all_unknowns(recorded: dict, analysis: dict | None = None) -> list[dict]:
    nodes = {}
    for source in (analysis or {}, recorded):
        for field in ("unknowns", "new_unknowns"):
            for node in _unknowns(source.get(field)):
                node = dict(node)
                node["id"] = _normalize_unknown_id(node["id"])
                node["parent_id"] = _normalize_unknown_id(node.get("parent_id")) or None
                nodes[node["id"]] = node
    return list(nodes.values())


def ensure_goal_root(recorded: dict, analysis: dict, message: str) -> None:
    """Preserve the analyzer's question forest and stable resumed identities."""
    nodes = all_unknowns(recorded, analysis)
    if nodes:
        validate_tree(nodes)
        return
    goal = str(analysis.get("origin_message") or message or
               (analysis.get("intent") or {}).get("summary") or "").strip()
    # Recovery only: normal roots are question-authored by task analysis.
    root = _unknowns([{
        "id": "U_GOAL1",
        "question": f"What is the evidence-backed solution to this request: {goal}?",
        "domain": "requirement", "parent_id": None, "blocking": True,
        "type": "code_fact", "resolution_strategy": "investigate_project",
        "origin": "investigation_goal",
        "why": "Recover a missing goal question without inventing a detail checklist.",
    }])[0]
    recorded["new_unknowns"] = [root]
    recorded["tree_bootstrapped"] = True


def validate_active_write(arguments: dict, active_id: str) -> None:
    targets = arguments.get("target_unknown_ids") or []
    resolutions = arguments.get("resolutions") or []
    ids = [*targets, *(r.get("unknown_id") for r in resolutions if isinstance(r, dict))]
    if arguments.get("unknown_id"):
        ids.append(arguments["unknown_id"])
    if any(not active_id or _normalize_unknown_id(i) != active_id for i in ids):
        raise ValueError(f"Investigate and resolve only the current Unknown: {active_id or '(Root Audit)'}")


def validate_tree(nodes: list[dict]) -> None:
    by_id = {node["id"]: node for node in nodes}
    for node in nodes:
        seen = set()
        current = node["id"]
        while current:
            if current in seen:
                raise ValueError(f"unknown parent cycle: {current}")
            if current not in by_id:
                raise ValueError(f"unknown parent does not exist: {current}")
            seen.add(current)
            current = by_id[current].get("parent_id")


def resolution_for(node_id: str, recorded: dict) -> dict | None:
    return next((r for r in reversed(recorded.get("resolutions", []))
                 if _normalize_unknown_id(r.get("unknown_id")) == node_id), None)


def is_open(node: dict, recorded: dict) -> bool:
    resolution = resolution_for(node["id"], recorded) or {}
    status = resolution.get("status")
    return not (status == "resolved" or (status == "deferred" and not node.get("blocking")))


def open_blockers(recorded: dict, analysis: dict | None = None) -> list[dict]:
    nodes = all_unknowns(recorded, analysis)
    validate_tree(nodes)
    return [n for n in nodes if n.get("blocking") and is_open(n, recorded)]


def validate_closure(recorded: dict, analysis: dict | None = None) -> None:
    nodes = all_unknowns(recorded, analysis)
    validate_tree(nodes)
    for node in nodes:
        if not is_open(node, recorded):
            children = [n["id"] for n in nodes if n.get("parent_id") == node["id"]
                        and n.get("blocking") and is_open(n, recorded)]
            if children:
                raise ValueError(f"{node['id']} cannot close before blocking children: {', '.join(children)}")
    known = {n["id"] for n in nodes}
    for resolution in recorded.get("resolutions", []):
        if _normalize_unknown_id(resolution.get("unknown_id")) not in known:
            raise ValueError("resolution references an unknown outside the investigation tree")


def reopen_parents(recorded: dict, analysis: dict) -> None:
    """A quality downgrade of a dependency invalidates closed ancestor answers."""
    nodes = all_unknowns(recorded, analysis)
    validate_tree(nodes)
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if is_open(node, recorded):
                continue
            children = [n["id"] for n in nodes if n.get("parent_id") == node["id"]
                        and n.get("blocking") and is_open(n, recorded)]
            if children:
                resolution = resolution_for(node["id"], recorded)
                resolution["status"] = "partially_resolved"
                resolution["reason"] = "Blocking dependencies reopened: " + ", ".join(children)
                changed = True


def select_active(traversal, recorded: dict, analysis: dict) -> dict | None:
    nodes = all_unknowns(recorded, analysis)
    validate_tree(nodes)
    by_id = {n["id"]: n for n in nodes}
    blockers = {n["id"] for n in open_blockers(recorded, analysis)}
    current = by_id.get(traversal.active_unknown_id)
    while current and current["id"] not in blockers:
        current = by_id.get(current.get("parent_id"))
        if current and current["id"] in blockers:
            traversal.revisit_unknown_id = current["id"] if not any(
                n.get("parent_id") == current["id"] and n["id"] in blockers for n in nodes
            ) else ""
    if current is None and blockers:
        current = next(n for n in nodes if n["id"] in blockers)
    while current and current["id"] != traversal.revisit_unknown_id:
        child = next((n for n in nodes if n.get("parent_id") == current["id"]
                      and n["id"] in blockers), None)
        if child is None:
            break
        current = child
    path = []
    ancestor = current
    while ancestor:
        path.append(ancestor["id"])
        ancestor = by_id.get(ancestor.get("parent_id"))
    traversal.active_unknown_id = current["id"] if current else ""
    traversal.active_path = list(reversed(path))
    return current


def prepare_new_unknowns(value, recorded: dict, analysis: dict, *, origin="discovery", active_id="") -> list[dict]:
    if value is not None and not isinstance(value, list):
        raise ValueError("new_unknowns must be an array")
    existing = all_unknowns(recorded, analysis)
    used = {n["id"] for n in existing}
    equivalent = {(n["domain"], _question_key(n["question"])): n["id"]
                  for n in existing if is_open(n, recorded)}
    aliases = {}
    result = []
    for raw in value or []:
        if not isinstance(raw, dict) or raw.get("domain") not in {"requirement", "solution"} or "parent_id" not in raw:
            raise ValueError("new_unknowns require explicit domain and parent_id")
        if not str(raw.get("id") or "").strip() or not str(raw.get("question") or "").strip():
            raise ValueError("new_unknowns require nonempty id and question")
        node = _unknowns([raw])[0]
        key = (node["domain"], _question_key(node["question"]))
        supplied = _normalize_unknown_id(node["id"])
        if key in equivalent:
            aliases[supplied] = equivalent[key]
            continue
        if supplied in used:
            raise ValueError(f"new_unknown id already exists: {supplied}; use a fresh id")
        node["id"] = supplied
        node["parent_id"] = _normalize_unknown_id(node.get("parent_id")) or None
        if origin == "root_audit":
            # A global independent gap may be a new root; never attach it to an arbitrary root.
            pass
        elif active_id:
            if node["parent_id"] not in (None, active_id):
                raise ValueError(f"New questions must be discovered dependencies of active Unknown {active_id}")
            node["parent_id"] = active_id
        elif value:
            raise ValueError("New questions require an active Unknown; global gaps belong in Root Audit")
        node["origin"] = origin
        used.add(supplied)
        equivalent[key] = supplied
        result.append(node)
    for node in result:
        node["parent_id"] = aliases.get(node["parent_id"], node["parent_id"])
    validate_tree(existing + result)
    return result


def validate_update(current: dict, candidate: dict, analysis: dict) -> None:
    old = {n["id"]: n for n in all_unknowns(current, analysis)}
    for node in all_unknowns(candidate, analysis):
        prior = old.get(node["id"])
        if prior and any(prior.get(k) != node.get(k) for k in ("question", "domain", "parent_id", "blocking")):
            raise ValueError(f"Unknown {node['id']} identity/dependency/blocking fields cannot be overwritten")
    validate_closure(candidate, analysis)


def audit_signature(recorded: dict, analysis: dict, observations: list[dict]) -> str:
    payload = {"contract": analysis, "nodes": all_unknowns(recorded, analysis),
               "resolutions": recorded.get("resolutions", []), "beliefs": recorded.get("beliefs", []),
               "observations": observations}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def audit_complete(recorded: dict, analysis: dict, observations: list[dict]) -> bool:
    audit = recorded.get("audit_result") or {}
    return bool(audit.get("complete") and all((audit.get(d) or {}).get("complete") for d in ("requirement", "solution"))
                and audit.get("signature") == audit_signature(recorded, analysis, observations)
                and not open_blockers(recorded, analysis))


def normalize_audit(arguments: dict, recorded: dict, analysis: dict, observations: list[dict]) -> dict:
    validate_closure(recorded, analysis)
    blockers = open_blockers(recorded, analysis)
    result = {}
    for domain in ("requirement", "solution"):
        raw = arguments.get(domain)
        if not isinstance(raw, dict) or not isinstance(raw.get("complete"), bool) or not str(raw.get("reason") or "").strip():
            raise ValueError(f"audit {domain} requires complete boolean and reason")
        ids = [n["id"] for n in blockers if n["domain"] == domain]
        if not raw["complete"] and not ids:
            raise ValueError(f"incomplete {domain} audit requires a represented blocking unknown")
        supplied = raw.get("open_unknown_ids", [])
        if not isinstance(supplied, list) or any(_normalize_unknown_id(i) not in ids for i in supplied):
            raise ValueError(f"audit {domain} references invalid open blocking unknowns")
        result[domain] = {"complete": raw["complete"] and not ids, "reason": raw["reason"], "open_unknown_ids": ids}
    result["complete"] = all(result[d]["complete"] for d in ("requirement", "solution")) and not blockers
    result["signature"] = audit_signature(recorded, analysis, observations)
    return result
