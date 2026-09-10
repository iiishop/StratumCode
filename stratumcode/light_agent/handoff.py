"""Lossless answer projection: keep conclusions, not duplicate execution traces."""
from __future__ import annotations

import json


def answer_handoff(call: dict, output: str) -> str:
    if call.get("function", {}).get("name") != "run_investigation":
        return output
    try:
        payload = json.loads(output)
    except (TypeError, ValueError):
        return output
    if not isinstance(payload, dict) or not isinstance(payload.get("investigation"), dict):
        return output
    if payload.get("analysis", {}).get("execution_mode") == "implement":
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
    return json.dumps({"analysis": payload.get("analysis", {}), "investigation": projected}, ensure_ascii=False)
