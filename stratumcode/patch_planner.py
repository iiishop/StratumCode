from __future__ import annotations

import json
import platform
import re
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from . import app_settings, model_settings, providers
from .agent_runtime import (
    add_usage as _add_usage,
    call_model as _call_model,
    content_text as _content_text,
    empty_usage as _empty_usage,
    start_event,
    usage_delta as _usage_delta,
)

MAX_OUTPUT_TOKENS = 6144
MAX_JSON_ATTEMPTS = 2


def patch_planning_stream(
    *,
    message: str,
    analysis: dict,
    investigation: dict,
    design_plan: dict,
    workspace_dir: str,
) -> Iterator[dict]:
    setting = model_settings.resolve(model_settings.DEFAULT_STAGE)
    if setting is None:
        raise ValueError("No model configured for patch planning. Configure a default model in Providers.")

    provider = setting["provider"]
    model = setting["model_id"]
    pricing_rules = providers.get_model_pricing(provider["id"], model)
    usage_total = _empty_usage(pricing_rules)
    run_id = uuid4().hex[:10]
    stage_id = f"{run_id}-stage"

    yield start_event(stage_id, "stage", {
        "name": "patch_planning",
        "label": "Plan justified code changes",
        "state": "running",
        "phase": "patch_planning",
        "model": model,
        "context_length": providers.model_context_length(provider["base_url"], provider["api_key"], model),
        "provider": provider["name"],
        "inherited": setting["inherited"],
    })

    messages = [
        {"role": "system", "content": _system_prompt(app_settings.get_output_language())},
        {"role": "user", "content": _user_prompt(message, analysis, investigation, design_plan, workspace_dir)},
    ]
    plan = None
    last_error = None
    for attempt in range(1, MAX_JSON_ATTEMPTS + 1):
        assistant = _call_model(provider, model, messages, tools=[], max_tokens=MAX_OUTPUT_TOKENS)
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{attempt}", "usage", {"delta": usage, "total": usage_total})
        text = _content_text(assistant.get("content"))
        try:
            plan = _normalize_plan(_json_from_text(text))
            break
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            messages.extend([
                {"role": "assistant", "content": text[:4000]},
                {"role": "user", "content": (
                    "The previous response was not valid JSON: "
                    f"{exc}. Return a compact valid JSON object only. "
                    "Keep arrays short and do not include Markdown."
                )},
            ])
    if plan is None:
        yield start_event(f"{run_id}-output", "output", {
            "content": f"Patch planning failed to produce valid JSON after retry: {last_error}",
            "streaming": False,
        })
        yield {"op": "update", "id": stage_id, "patch": {"state": "error", "phase": "patch_planning_failed"}}
        return
    issues = validate_patch_plan(plan, analysis, design_plan, workspace_dir)
    if issues:
        yield start_event(f"{run_id}-output", "output", {
            "content": "Patch plan rejected by runtime validator:\n" + "\n".join(f"- {item}" for item in issues),
            "streaming": False,
        })
        yield {"op": "update", "id": stage_id, "patch": {"state": "error", "phase": "patch_validation_failed"}}
        return
    yield start_event(f"{run_id}-plan", "patch_plan", plan)
    yield {"op": "update", "id": stage_id, "patch": {"state": "done", "phase": "patch_planned"}}
    yield {"op": "done", "patch_plan": plan}


def _system_prompt(language: str) -> str:
    return f"""\
You are StratumCode's Patch Planner. Write user-visible strings in {language}.
Return one JSON object only. Do not use Markdown.

Turn an approved design into a minimal, justified implementation plan.
Do not investigate, do not edit files, and do not add behavior not present in
the design plan. Every implementation step must have a responsibility chain.

Schema:
{{
  "summary": "one short sentence",
  "files_to_change": ["workspace-relative path"],
  "implementation_steps": [
    {{
      "id": "IS1",
      "file": "path",
      "target": "function/class/component/route",
      "action": "specific code-level action",
      "because": ["AC1", "DD1", "project fact"],
      "required_behavior_if_removed": "what breaks if this step is deleted",
      "minimality_check": "what this step deliberately does not do"
    }}
  ],
  "responsibility_chain": [
    {{"step_id": "IS1", "requirement_ids": ["AC1"], "decision_ids": ["DD1"], "project_facts": ["fact"], "removal_breaks": "behavior"}}
  ],
  "acceptance_mapping": [
    {{"acceptance_id": "AC1", "covered_by": ["IS1"], "verification": "check that proves it"}}
  ],
  "tests_or_checks": ["command or manual check"],
  "risks": ["small risk or empty"],
  "out_of_scope": ["behavior intentionally not implemented"]
}}

Rules:
- Every implementation step must cite at least one acceptance criterion or design decision in because.
- Every step must explain what breaks if removed.
- Every acceptance criterion must appear in acceptance_mapping.
- Prefer the fewest steps that cover the accepted design.
- If no test framework exists, use one minimal runnable check.
"""


def validate_patch_plan(plan: dict, analysis: dict, design_plan: dict, workspace_dir: str) -> list[str]:
    issues = []
    criteria_ids = {
        str(item.get("id") or "").strip()
        for item in analysis.get("acceptance_criteria", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    decision_ids = {
        str(item.get("id") or "").strip()
        for item in design_plan.get("design_decisions", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    steps = plan.get("implementation_steps") or []
    step_ids = {item.get("id") for item in steps if item.get("id")}
    step_files = {item.get("file") for item in steps if item.get("file")}
    files = set(plan.get("files_to_change") or [])
    if files != step_files:
        issues.append("files_to_change must match implementation step files")
    missing_ac = sorted(criteria_ids - {
        item.get("acceptance_id")
        for item in plan.get("acceptance_mapping", [])
        if item.get("acceptance_id")
    })
    if missing_ac:
        issues.append("acceptance criteria missing acceptance_mapping: " + ", ".join(missing_ac))
    for item in plan.get("acceptance_mapping", []):
        if item.get("acceptance_id") not in criteria_ids:
            issues.append(f"acceptance_mapping references unknown acceptance id: {item.get('acceptance_id')}")
        missing_steps = [step for step in item.get("covered_by", []) if step not in step_ids]
        if missing_steps:
            issues.append("acceptance_mapping references unknown steps: " + ", ".join(missing_steps))
        if not item.get("verification"):
            issues.append(f"acceptance_mapping {item.get('acceptance_id') or '?'} has no verification")
    workspace = Path(workspace_dir or ".").resolve()
    for step in steps:
        step_id = step.get("id") or "?"
        if not step.get("id"):
            issues.append("implementation step is missing id")
        if not step.get("file"):
            issues.append(f"step {step_id} has no file")
            continue
        try:
            target = (workspace / step["file"]).resolve()
            if workspace not in (target, *target.parents):
                issues.append(f"step {step_id} file is outside workspace")
        except OSError:
            issues.append(f"step {step_id} file path is invalid")
        if not step.get("because"):
            issues.append(f"step {step_id} has no because")
        if not step.get("required_behavior_if_removed"):
            issues.append(f"step {step_id} has no required_behavior_if_removed")
        if not step.get("minimality_check"):
            issues.append(f"step {step_id} has no minimality_check")
        refs = set(step.get("because") or [])
        if criteria_ids or decision_ids:
            valid = criteria_ids | decision_ids
            if not any(_cites(ref, valid) for ref in refs):
                issues.append(f"step {step_id} because does not cite a valid AC or design decision")
    for item in plan.get("responsibility_chain", []):
        if item.get("step_id") not in step_ids:
            issues.append(f"responsibility_chain references unknown step: {item.get('step_id')}")
        for req in item.get("requirement_ids", []):
            if req not in criteria_ids:
                issues.append(f"responsibility_chain references unknown acceptance id: {req}")
        for decision in item.get("decision_ids", []):
            if decision not in decision_ids:
                issues.append(f"responsibility_chain references unknown design decision: {decision}")
    if analysis.get("intent", {}).get("type") in {"feature", "bugfix"} and not plan.get("tests_or_checks"):
        issues.append("feature/bugfix patch plan requires at least one test or check")
    return issues


def _cites(value: str, ids: set[str]) -> bool:
    text = str(value or "")
    return any(item and item in text for item in ids)


def _user_prompt(message: str, analysis: dict, investigation: dict, design_plan: dict, workspace_dir: str) -> str:
    return json.dumps({
        "platform": platform.system(),
        "workspace_root": workspace_dir,
        "user_request": message,
        "task": {
            "intent": analysis.get("intent", {}),
            "acceptance_criteria": analysis.get("acceptance_criteria", []),
            "behavior_contract": analysis.get("behavior_contract", {}),
            "constraints": analysis.get("constraints", []),
            "scope": analysis.get("scope", {}),
        },
        "investigation": {
            "summary": investigation.get("summary", ""),
            "patch_planning_facts": investigation.get("patch_planning_facts") or investigation.get("patch_planning_context") or [],
            "beliefs": investigation.get("beliefs", []),
            "resolutions": investigation.get("resolutions", []),
        },
        "design_plan": design_plan,
    }, ensure_ascii=False, indent=2)


def _json_from_text(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("patch plan must be a JSON object")
    return data


def _normalize_plan(data: dict) -> dict:
    return {
        "summary": str(data.get("summary") or "").strip(),
        "files_to_change": _strings(data.get("files_to_change")),
        "implementation_steps": _implementation_steps(data.get("implementation_steps")),
        "responsibility_chain": _responsibility_chain(data.get("responsibility_chain")),
        "acceptance_mapping": _acceptance_mapping(data.get("acceptance_mapping")),
        "tests_or_checks": _strings(data.get("tests_or_checks")),
        "risks": _strings(data.get("risks")),
        "out_of_scope": _strings(data.get("out_of_scope")),
    }


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for raw in value if (item := str(raw).strip())]


def _implementation_steps(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [
        {
            "id": str(item.get("id") or "").strip(),
            "file": str(item.get("file") or "").strip(),
            "target": str(item.get("target") or "").strip(),
            "action": str(item.get("action") or "").strip(),
            "because": _strings(item.get("because")),
            "required_behavior_if_removed": str(item.get("required_behavior_if_removed") or "").strip(),
            "minimality_check": str(item.get("minimality_check") or "").strip(),
        }
        for item in value
        if isinstance(item, dict) and (item.get("action") or item.get("file"))
    ]


def _responsibility_chain(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [
        {
            "step_id": str(item.get("step_id") or "").strip(),
            "requirement_ids": _strings(item.get("requirement_ids")),
            "decision_ids": _strings(item.get("decision_ids")),
            "project_facts": _strings(item.get("project_facts")),
            "removal_breaks": str(item.get("removal_breaks") or "").strip(),
        }
        for item in value
        if isinstance(item, dict) and (item.get("step_id") or item.get("removal_breaks"))
    ]


def _acceptance_mapping(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [
        {
            "acceptance_id": str(item.get("acceptance_id") or item.get("id") or "").strip(),
            "covered_by": _strings(item.get("covered_by")),
            "verification": str(item.get("verification") or item.get("plan") or "").strip(),
        }
        for item in value
        if isinstance(item, dict) and (item.get("acceptance_id") or item.get("id") or item.get("verification"))
    ]
