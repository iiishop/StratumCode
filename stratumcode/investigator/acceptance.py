"""Commit-time acceptance of changed resolutions, not whole-tree re-auditing."""
from __future__ import annotations

from .domain import _beliefs, _validate_resolution_refs
from .finalize import _apply_direct_resolution_gate, _enforce_resolution_evidence
from .tree import all_unknowns, validate_update


def accept_resolutions(current: dict, candidate: dict, analysis: dict,
                       observations: list[dict], *, strict_grounding: bool) -> dict:
    previous = {r["unknown_id"]: r for r in current.get("resolutions", [])}
    changed = [dict(r) for r in candidate.get("resolutions", [])
               if r != previous.get(r["unknown_id"])]
    if changed:
        _validate_resolution_refs(changed, _beliefs(candidate.get("beliefs")), observations)
        changed = _enforce_resolution_evidence(changed, all_unknowns(candidate, analysis), strict=True)
        scoped = {**candidate, "resolutions": changed}
        gated = _apply_direct_resolution_gate(scoped, observations, strict_grounding=strict_grounding)
        replacements = {r["unknown_id"]: r for r in gated["resolutions"]}
        candidate["resolutions"] = [replacements.get(r["unknown_id"], r) for r in candidate.get("resolutions", [])]
    validate_update(current, candidate, analysis)
    return candidate
