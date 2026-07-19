from __future__ import annotations

import json
import re
from collections.abc import Iterator
from itertools import count
from pathlib import Path
from uuid import uuid4

from . import app_settings, model_settings, patch_authorization, prompt, providers
from .agent_runtime import (
    add_usage as _add_usage,
    call_model as _call_model,
    content_text as _content_text,
    empty_usage as _empty_usage,
    start_event,
    usage_delta as _usage_delta,
)

MAX_OUTPUT_TOKENS = 6144


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
        {"role": "system", "content": prompt.build_patch_planner_system(app_settings.get_output_language())},
        {"role": "user", "content": prompt.build_patch_planner_user(message, analysis, investigation, design_plan, workspace_dir)},
    ]
    plan = None
    last_error = None
    attempts = app_settings.get_round_limit("patch_json_attempts")
    for attempt in _attempt_indexes(attempts):
        assistant = _call_model(provider, model, messages, tools=[], max_tokens=MAX_OUTPUT_TOKENS)
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{attempt}", "usage", {"delta": usage, "total": usage_total})
        text = _content_text(assistant.get("content"))
        try:
            plan = _normalize_plan(_json_from_text(text))
            plan["project_facts"] = _project_facts(investigation)
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
    for repair_attempt in _attempt_indexes(attempts):
        issues = validate_patch_plan(plan, analysis, design_plan, workspace_dir)
        if not issues:
            break
        if attempts > 0 and repair_attempt == attempts:
            yield start_event(f"{run_id}-output", "output", {
                "content": "Patch plan rejected by runtime validator:\n" + "\n".join(f"- {item}" for item in issues),
                "streaming": False,
            })
            yield {"op": "update", "id": stage_id, "patch": {"state": "error", "phase": "patch_validation_failed"}}
            return
        messages.extend([
            {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)},
            {"role": "user", "content": (
                "The runtime validator rejected the patch plan:\n"
                + "\n".join(f"- {item}" for item in issues)
                + "\nReturn a corrected compact JSON object only. Do not invent IDs; use AC ids for acceptance_ids/acceptance_mapping, RM ids for responsibility_chain.requirement_ids, and DD ids for decision_ids."
            )},
        ])
        assistant = _call_model(provider, model, messages, tools=[], max_tokens=MAX_OUTPUT_TOKENS)
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-repair-{repair_attempt}", "usage", {"delta": usage, "total": usage_total})
        try:
            plan = _normalize_plan(_json_from_text(_content_text(assistant.get("content"))))
            plan["project_facts"] = _project_facts(investigation)
        except (json.JSONDecodeError, ValueError) as exc:
            plan = {**plan, "_repair_error": str(exc)}
    plan["execution_authorization"] = patch_authorization.create_authorization(plan, workspace_dir)
    yield start_event(f"{run_id}-plan", "patch_plan", plan)
    yield {"op": "update", "id": stage_id, "patch": {"state": "done", "phase": "patch_planned"}}
    yield {"op": "done", "patch_plan": plan}


def _attempt_indexes(limit: int, start: int = 1):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


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
    requirement_ids = {
        str(item.get("id") or "").strip()
        for item in design_plan.get("requirement_model", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    fact_ids = {
        str(item.get("id") or "").strip()
        for item in plan.get("project_facts", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    steps = plan.get("implementation_steps") or []
    seen_step_ids = set()
    duplicate_step_ids = set()
    for item in steps:
        step_id = item.get("id")
        if step_id in seen_step_ids:
            duplicate_step_ids.add(step_id)
        seen_step_ids.add(step_id)
    if duplicate_step_ids:
        issues.append("duplicate implementation step ids: " + ", ".join(sorted(duplicate_step_ids)))
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
        if not step.get("purpose"):
            issues.append(f"step {step_id} has no purpose")
        if not step.get("file"):
            issues.append(f"step {step_id} has no file")
            continue
        if not step.get("target"):
            issues.append(f"step {step_id} has no target")
        try:
            target = (workspace / step["file"]).resolve()
            if workspace not in (target, *target.parents):
                issues.append(f"step {step_id} file is outside workspace")
        except OSError:
            issues.append(f"step {step_id} file path is invalid")
        if not step.get("action"):
            issues.append(f"step {step_id} has no action")
        if not step.get("required_behavior_if_removed"):
            issues.append(f"step {step_id} has no required_behavior_if_removed")
        if not step.get("completion_conditions"):
            issues.append(f"step {step_id} has no completion_conditions")
        if not step.get("minimality_check"):
            issues.append(f"step {step_id} has no minimality_check")
        if criteria_ids or decision_ids:
            if not (set(step.get("acceptance_ids") or []) & criteria_ids or set(step.get("decision_ids") or []) & decision_ids):
                issues.append(f"step {step_id} does not cite a valid AC or design decision")
        if fact_ids and not step.get("project_fact_ids"):
            issues.append(f"step {step_id} has no project_fact_ids")
        for ref in step.get("acceptance_ids") or []:
            if ref not in criteria_ids:
                issues.append(f"step {step_id} references unknown acceptance id: {ref}")
        for ref in step.get("decision_ids") or []:
            if ref not in decision_ids:
                issues.append(f"step {step_id} references unknown design decision: {ref}")
        for ref in step.get("project_fact_ids") or []:
            if fact_ids and ref not in fact_ids:
                issues.append(f"step {step_id} references unknown project fact id: {ref}")
    chain_steps = {item.get("step_id") for item in plan.get("responsibility_chain", [])}
    missing_chain = sorted(step_ids - chain_steps)
    if missing_chain:
        issues.append("implementation steps missing responsibility_chain: " + ", ".join(missing_chain))
    for item in plan.get("responsibility_chain", []):
        if item.get("step_id") not in step_ids:
            issues.append(f"responsibility_chain references unknown step: {item.get('step_id')}")
        if not item.get("removal_breaks"):
            issues.append(f"responsibility_chain {item.get('step_id') or '?'} has no removal_breaks")
        if fact_ids and not item.get("project_fact_ids"):
            issues.append(f"responsibility_chain {item.get('step_id') or '?'} has no project_fact_ids")
        for req in item.get("requirement_ids", []):
            if req not in criteria_ids and req not in requirement_ids:
                issues.append(f"responsibility_chain references unknown requirement or acceptance id: {req}")
        for decision in item.get("decision_ids", []):
            if decision not in decision_ids:
                issues.append(f"responsibility_chain references unknown design decision: {decision}")
        for ref in item.get("project_fact_ids") or []:
            if fact_ids and ref not in fact_ids:
                issues.append(f"responsibility_chain references unknown project fact id: {ref}")
    if analysis.get("intent", {}).get("type") in {"feature", "bugfix"} and not plan.get("tests_or_checks"):
        issues.append("feature/bugfix patch plan requires at least one test or check")
    return issues


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
        "project_facts": _project_facts(data),
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
            "purpose": str(item.get("purpose") or "").strip(),
            "file": str(item.get("file") or "").strip(),
            "target": str(item.get("target") or "").strip(),
            "action": str(item.get("action") or "").strip(),
            "acceptance_ids": _strings(item.get("acceptance_ids")),
            "decision_ids": _strings(item.get("decision_ids")),
            "project_fact_ids": _strings(item.get("project_fact_ids")),
            "required_behavior_if_removed": str(item.get("required_behavior_if_removed") or "").strip(),
            "completion_conditions": _strings(item.get("completion_conditions")),
            "out_of_scope": _strings(item.get("out_of_scope")),
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
            "project_fact_ids": _strings(item.get("project_fact_ids")),
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


def _project_facts(investigation: dict) -> list[dict]:
    facts = investigation.get("patch_planning_facts") or investigation.get("patch_planning_context") or []
    return [
        {"id": f"PF{index}", "text": text}
        for index, text in enumerate(_strings(facts), start=1)
    ]
