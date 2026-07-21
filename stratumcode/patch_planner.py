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
DEFAULT_PATCH_JSON_ATTEMPTS = 3


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

    system = {"role": "system", "content": prompt.build_patch_planner_system(app_settings.get_output_language())}
    step_content = []
    tests_or_checks = []
    risks = []
    out_of_scope = []
    acceptance_verification = {}
    skipped_decision_slots = []
    decisions = [
        item for item in design_plan.get("design_decisions", [])
        if isinstance(item, dict)
    ]
    for index, decision in enumerate(decisions, start=1):
        slot = yield from _content_json_stream(
            provider,
            model,
            [
                system,
                {"role": "user", "content": prompt.build_patch_step_slot_user(
                    message,
                    analysis,
                    investigation,
                    design_plan,
                    workspace_dir,
                    slot_index=index,
                    decision=decision,
                )},
            ],
            pricing_rules,
            usage_total,
            run_id,
            f"decision-{index}",
        )
        if not slot:
            continue
        tests_or_checks.extend(_strings(slot.get("tests_or_checks")))
        risks.extend(_strings(slot.get("risks")))
        out_of_scope.extend(_strings(slot.get("out_of_scope")))
        _merge_acceptance_verification(acceptance_verification, slot.get("acceptance_verification"))
        if slot.get("needed") is False:
            skipped_decision_slots.append({
                "decision_slot": index,
                "reason": str(slot.get("skip_reason") or "Model marked this design decision as requiring no code change.").strip(),
            })
            continue
        for step in slot.get("step_content") or []:
            if isinstance(step, dict):
                patched = dict(step)
                patched["decision_slots"] = [index]
                step_content.append(patched)
    plan_content = {
        "summary": _patch_summary(step_content),
        "step_content": step_content,
        "tests_or_checks": tests_or_checks,
        "risks": risks,
        "out_of_scope": out_of_scope,
        "acceptance_verification": [
            {"acceptance_slot": key, "verification": value}
            for key, value in sorted(acceptance_verification.items())
        ],
        "skipped_decision_slots": skipped_decision_slots,
    }
    try:
        plan = _plan_from_content(plan_content, analysis, design_plan, investigation)
    except ValueError as exc:
        yield start_event(f"{run_id}-output", "output", {
            "content": f"Patch planning failed to produce usable step content: {exc}",
            "streaming": False,
        })
        yield {"op": "update", "id": stage_id, "patch": {"state": "error", "phase": "patch_planning_failed"}}
        return
    issues = validate_patch_plan(plan, analysis, design_plan, workspace_dir, investigation)
    if issues:
        yield start_event(f"{run_id}-output", "output", {
            "content": "Patch plan rejected by runtime validator:\n" + "\n".join(f"- {item}" for item in issues),
            "streaming": False,
        })
        yield {"op": "update", "id": stage_id, "patch": {"state": "error", "phase": "patch_validation_failed"}}
        return
    plan["execution_authorization"] = patch_authorization.create_authorization(plan, workspace_dir)
    yield start_event(f"{run_id}-plan", "patch_plan", plan)
    yield {"op": "update", "id": stage_id, "patch": {"state": "done", "phase": "patch_planned"}}
    yield {"op": "done", "patch_plan": plan}


def _attempt_indexes(limit: int, start: int = 1):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


def validate_patch_plan(plan: dict, analysis: dict, design_plan: dict, workspace_dir: str, investigation: dict | None = None) -> list[str]:
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
    skipped_decision_ids = {
        str(item.get("decision_id") or "").strip()
        for item in plan.get("runtime_skipped_decisions") or []
        if isinstance(item, dict) and str(item.get("decision_id") or "").strip()
    }
    unknown_skipped_decisions = sorted(skipped_decision_ids - decision_ids)
    if unknown_skipped_decisions:
        issues.append("runtime_skipped_decisions references unknown design decisions: " + ", ".join(unknown_skipped_decisions))
    required_decision_ids = decision_ids - skipped_decision_ids
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
    no_patch_plan = not steps and bool(skipped_decision_ids)
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
        if not item.get("covered_by") and not no_patch_plan:
            issues.append(f"acceptance_mapping {item.get('acceptance_id') or '?'} has no covered_by steps")
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
        for symbol in _structured_skip_symbols(investigation or {}):
            text = " ".join([
                str(step.get("purpose") or ""),
                str(step.get("target") or ""),
                str(step.get("action") or ""),
                " ".join(step.get("completion_conditions") or []),
            ])
            if _extracts_symbol(text, symbol):
                issues.append(f"step {step_id} tries to extract runtime skip candidate: {symbol}")
        for symbol in _structured_review_symbols(investigation or {}):
            text = " ".join([
                str(step.get("purpose") or ""),
                str(step.get("target") or ""),
                str(step.get("action") or ""),
                " ".join(step.get("completion_conditions") or []),
            ])
            if _extracts_symbol(text, symbol):
                if not _step_has_review_strategy(step, symbol, design_plan):
                    issues.append(f"step {step_id} extracts review candidate without a behavior-preserving design strategy: {symbol}")
    chain_steps = {item.get("step_id") for item in plan.get("responsibility_chain", [])}
    missing_chain = sorted(step_ids - chain_steps)
    if missing_chain:
        issues.append("implementation steps missing responsibility_chain: " + ", ".join(missing_chain))
    covered_decisions = {
        ref
        for step in steps
        for ref in step.get("decision_ids", [])
        if ref
    }
    missing_decisions = sorted(required_decision_ids - covered_decisions)
    if missing_decisions:
        issues.append("design decisions missing implementation step coverage: " + ", ".join(missing_decisions))
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


def _structured_skip_symbols(investigation: dict) -> list[str]:
    return _structured_symbols_by_action(investigation, "skip")


def _structured_review_symbols(investigation: dict) -> list[str]:
    return _structured_symbols_by_action(investigation, "review")


def _structured_symbols_by_action(investigation: dict, action: str) -> list[str]:
    structured = investigation.get("structured_findings")
    if not isinstance(structured, dict):
        return []
    candidates = structured.get("refactor_candidates") or structured.get("duplicate_candidates") or []
    return [
        str(item.get("symbol") or "").strip()
        for item in candidates
        if isinstance(item, dict)
        and str(item.get("safe_action") or "").strip() == action
        and str(item.get("symbol") or "").strip()
    ]


def _step_has_review_strategy(step: dict, symbol: str, design_plan: dict) -> bool:
    decision_ids = set(step.get("decision_ids") or [])
    for item in design_plan.get("design_decisions") or []:
        if not isinstance(item, dict) or item.get("id") not in decision_ids:
            continue
        if not str(item.get("variant_strategy") or "").strip():
            continue
        decision_text = " ".join([
            str(item.get("decision") or ""),
            " ".join(item.get("because") or []),
            str(item.get("variant_strategy") or ""),
        ])
        if symbol.casefold() in decision_text.casefold():
            return True
    return False


def _extracts_symbol(text: str, symbol: str) -> bool:
    lowered = str(text or "").casefold()
    return symbol.casefold() in lowered and any(
        word in lowered
        for word in ("extract", "shared", "common", "utility", "helper", "提取", "公共", "复用", "共享")
    )


def _json_from_text(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("patch content must be a JSON object")
    return data


def _content_json_stream(
    provider: dict,
    model: str,
    messages: list[dict],
    pricing_rules,
    usage_total: dict,
    run_id: str,
    label: str,
) -> Iterator[dict]:
    last_invalid_key = ""
    repeated_invalid = 0
    attempts = app_settings.get_round_limit("patch_json_attempts") or DEFAULT_PATCH_JSON_ATTEMPTS
    for attempt in _attempt_indexes(attempts):
        assistant = _call_model(provider, model, messages, tools=[], max_tokens=MAX_OUTPUT_TOKENS)
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{label}-{attempt}", "usage", {"delta": usage, "total": usage_total})
        text = _content_text(assistant.get("content"))
        try:
            return _json_from_text(text)
        except (json.JSONDecodeError, ValueError) as exc:
            invalid_key = f"{text[:1000]}::{exc}"
            repeated_invalid = repeated_invalid + 1 if invalid_key == last_invalid_key else 1
            last_invalid_key = invalid_key
            messages.extend([
                {"role": "assistant", "content": text[:4000]},
                {"role": "user", "content": (
                    "The previous response was not valid content JSON: "
                    f"{exc}. Return only the requested slot content JSON. "
                    "Do not write ids, final plan fields, or Markdown."
                )},
            ])
            if repeated_invalid >= DEFAULT_PATCH_JSON_ATTEMPTS:
                return None
    return None


def _patch_summary(steps: list[dict]) -> str:
    files = sorted({str(step.get("file") or "").strip() for step in steps if isinstance(step, dict) and step.get("file")})
    if not files:
        return "No code changes planned."
    return "Plan focused changes in " + ", ".join(files[:6]) + ("." if len(files) <= 6 else ", ...")


def _merge_acceptance_verification(target: dict[int, str], value) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            slot = int(item.get("acceptance_slot"))
        except (TypeError, ValueError):
            continue
        text = str(item.get("verification") or "").strip()
        if slot > 0 and text and slot not in target:
            target[slot] = text


def _acceptance_verification_map(value) -> dict[int, str]:
    result: dict[int, str] = {}
    _merge_acceptance_verification(result, value)
    return result


def _skipped_decisions(value, design_plan: dict) -> list[dict]:
    decision_ids = _known_ids(design_plan.get("design_decisions"))
    result = []
    if not isinstance(value, list):
        return result
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("decision_slot"))
        except (TypeError, ValueError):
            continue
        if 1 <= index <= len(decision_ids):
            result.append({
                "decision_id": decision_ids[index - 1],
                "reason": str(item.get("reason") or "").strip(),
            })
    return result


def _plan_from_content(data: dict, analysis: dict, design_plan: dict, investigation: dict) -> dict:
    facts = _project_facts(investigation)
    steps = _runtime_steps(data.get("step_content"), analysis, design_plan, facts)
    skipped_decisions = _skipped_decisions(data.get("skipped_decision_slots"), design_plan)
    if not steps and not skipped_decisions:
        raise ValueError("step_content must contain at least one implementation step")
    step_ids = [step["id"] for step in steps]
    criteria = [
        item for item in analysis.get("acceptance_criteria", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    verification = _acceptance_verification_map(data.get("acceptance_verification"))
    return {
        "summary": str(data.get("summary") or "").strip(),
        "files_to_change": sorted({step["file"] for step in steps if step.get("file")}),
        "implementation_steps": steps,
        "project_facts": facts,
        "responsibility_chain": [
            {
                "step_id": step["id"],
                "requirement_ids": step.get("acceptance_ids") or [],
                "decision_ids": step.get("decision_ids") or [],
                "project_fact_ids": step.get("project_fact_ids") or [],
                "removal_breaks": step.get("required_behavior_if_removed") or step.get("purpose") or "",
            }
            for step in steps
        ],
        "acceptance_mapping": [
            {
                "acceptance_id": str(item.get("id") or "").strip(),
                "covered_by": [step["id"] for step in steps if str(item.get("id") or "").strip() in set(step.get("acceptance_ids") or [])],
                "verification": verification.get(index + 1, "") or "Run the listed checks and confirm the requested behavior.",
            }
            for index, item in enumerate(criteria)
        ],
        "tests_or_checks": _strings(data.get("tests_or_checks")),
        "risks": _strings(data.get("risks")),
        "out_of_scope": _strings(data.get("out_of_scope")),
        "runtime_skipped_decisions": skipped_decisions,
    }


def _runtime_steps(value, analysis: dict, design_plan: dict, facts: list[dict]) -> list[dict]:
    if not isinstance(value, list):
        return []
    criteria_ids = _known_ids(analysis.get("acceptance_criteria"))
    decision_ids = _known_ids(design_plan.get("design_decisions"))
    fact_ids = _known_ids(facts)
    steps = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict) or not item.get("file"):
            continue
        acceptance_ids = _slot_ids(item.get("acceptance_slots"), criteria_ids)
        decision_refs = _slot_ids(item.get("decision_slots"), decision_ids)
        fact_refs = _slot_ids(item.get("project_fact_slots"), fact_ids)
        steps.append({
            "id": f"IS{index}",
            "purpose": str(item.get("purpose") or "").strip(),
            "file": str(item.get("file") or "").strip(),
            "target": str(item.get("target") or "").strip(),
            "action": str(item.get("action") or "").strip(),
            "acceptance_ids": acceptance_ids,
            "decision_ids": decision_refs,
            "project_fact_ids": fact_refs or (fact_ids if len(fact_ids) == 1 else []),
            "required_behavior_if_removed": str(item.get("required_behavior_if_removed") or "").strip(),
            "completion_conditions": _strings(item.get("completion_conditions")),
            "out_of_scope": _strings(item.get("out_of_scope")),
            "minimality_check": str(item.get("minimality_check") or "").strip(),
        })
    return steps


def _slot_ids(value, ids: list[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for raw in value:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= index <= len(ids):
            result.append(ids[index - 1])
    return result


def _known_ids(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item.get("id") or "").strip()
        for item in value
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for raw in value if (item := str(raw).strip())]


def _project_facts(investigation: dict) -> list[dict]:
    facts = investigation.get("patch_planning_facts") or investigation.get("patch_planning_context") or []
    return [
        {"id": f"PF{index}", "text": text}
        for index, text in enumerate(_strings(facts), start=1)
    ]
