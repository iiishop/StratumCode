"""Lossless answer projection: keep conclusions, not duplicate execution traces."""
from __future__ import annotations

import json


def answer_handoff(call: dict, output: str, *, artifact_ref: str = "") -> str:
    name = call.get("function", {}).get("name")
    if name not in {"run_investigation", "run_full_pipeline", "run_write_loop"}:
        return output
    try:
        payload = json.loads(output)
    except (TypeError, ValueError):
        return output
    if not isinstance(payload, dict):
        return output
    if name == "run_write_loop":
        return json.dumps({key: value for key, value in payload.items() if key not in {"events", "memory"}}, ensure_ascii=False)
    if not isinstance(payload.get("investigation"), dict):
        return output
    if not artifact_ref and (name == "run_full_pipeline" or payload.get("analysis", {}).get("execution_mode") == "implement"):
        # Write tools still consume the full investigation contract.
        return output
    investigation = payload["investigation"]
    fields = ("summary", "resolutions", "unknowns", "follow_ups", "audit_result",
              "runtime_failure", "recovery_reason", "open_questions", "user_decisions_required")
    projected = {key: investigation[key] for key in fields if key in investigation}
    projected["evidence_locations"] = [
        {key: observation[key] for key in ("id", "tool", "path", "start_line", "end_line", "target_unknown_ids") if key in observation}
        for observation in investigation.get("observations", []) if isinstance(observation, dict)
    ]
    # Beliefs not represented by any resolution remain available to the answer author.
    cited = {ref for r in investigation.get("resolutions", []) for ref in r.get("belief_ids", [])}
    projected["belief_sources"] = [{key: b[key] for key in ("id", "evidence", "status") if key in b}
                                   for b in investigation.get("beliefs", []) if b.get("id") in cited]
    projected["additional_findings"] = [b for b in investigation.get("beliefs", []) if b.get("id") not in cited]
    result = {"analysis": payload.get("analysis", {}), "investigation": projected}
    if artifact_ref:
        result["investigation_source"] = artifact_ref
    if name == "run_full_pipeline":
        result.update({key: payload[key] for key in ("subtask", "final", "validation_result", "error", "state", "changed_files") if key in payload})
        plan = payload.get("patch_plan")
        if isinstance(plan, dict):
            result["proposed_plan"] = {key: plan[key] for key in ("summary", "files_to_change", "_repair_issues", "risks", "out_of_scope") if key in plan}
    return json.dumps(result, ensure_ascii=False)
