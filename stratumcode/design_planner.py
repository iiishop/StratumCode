from __future__ import annotations

import json
import re
from collections.abc import Iterator
from itertools import count
from uuid import uuid4

from . import app_settings, model_settings, prompt, providers
from .agent_runtime import (
    add_usage as _add_usage,
    call_model as _call_model,
    content_text as _content_text,
    empty_usage as _empty_usage,
    start_event,
    usage_delta as _usage_delta,
)

MAX_OUTPUT_TOKENS = 6144


def design_planning_stream(
    *,
    message: str,
    analysis: dict,
    investigation: dict,
    workspace_dir: str,
) -> Iterator[dict]:
    setting = model_settings.resolve(model_settings.DEFAULT_STAGE)
    if setting is None:
        raise ValueError("No model configured for design planning. Configure a default model in Providers.")

    provider = setting["provider"]
    model = setting["model_id"]
    pricing_rules = providers.get_model_pricing(provider["id"], model)
    usage_total = _empty_usage(pricing_rules)
    run_id = uuid4().hex[:10]
    stage_id = f"{run_id}-stage"

    yield start_event(stage_id, "stage", {
        "name": "design_planning",
        "label": "Derive implementation design",
        "state": "running",
        "phase": "design_planning",
        "model": model,
        "context_length": providers.model_context_length(provider["base_url"], provider["api_key"], model),
        "provider": provider["name"],
        "inherited": setting["inherited"],
    })

    messages = [
        {"role": "system", "content": prompt.build_design_planner_system(app_settings.get_output_language())},
        {"role": "user", "content": prompt.build_design_planner_user(message, analysis, investigation, workspace_dir)},
    ]
    plan = None
    last_error = None
    for attempt in _attempt_indexes(app_settings.get_round_limit("design_json_attempts")):
        assistant = _call_model(provider, model, messages, tools=[], max_tokens=MAX_OUTPUT_TOKENS)
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{attempt}", "usage", {"delta": usage, "total": usage_total})
        text = _content_text(assistant.get("content"))
        try:
            plan = _normalize_design(_json_from_text(text))
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
            "content": f"Design planning failed to produce valid JSON after retry: {last_error}",
            "streaming": False,
        })
        yield {"op": "update", "id": stage_id, "patch": {"state": "error", "phase": "design_planning_failed"}}
        return
    issues = validate_design_plan(plan, analysis, investigation)
    if issues:
        yield start_event(f"{run_id}-output", "output", {
            "content": "Design plan rejected by runtime validator:\n" + "\n".join(f"- {item}" for item in issues),
            "streaming": False,
        })
        yield {"op": "update", "id": stage_id, "patch": {"state": "error", "phase": "design_validation_failed"}}
        return
    yield start_event(f"{run_id}-design", "design_plan", plan)
    yield {"op": "update", "id": stage_id, "patch": {"state": "done", "phase": "design_planned"}}
    yield {"op": "done", "design_plan": plan}


def blocking_gap(plan: dict) -> dict | None:
    return next((gap for gap in plan.get("decision_gaps", []) if gap.get("blocks_implementation")), None)


def _attempt_indexes(limit: int, start: int = 1):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


def validate_design_plan(plan: dict, analysis: dict, investigation: dict) -> list[str]:
    issues = []
    criteria = [item for item in analysis.get("acceptance_criteria", []) if isinstance(item, dict)]
    requirements = plan.get("requirement_model") or []
    alignments = plan.get("project_alignment") or []
    decisions = plan.get("design_decisions") or []
    requirement_ids = {item.get("id") for item in requirements if item.get("id")}
    aligned_ids = {item.get("requirement_id") for item in alignments if item.get("requirement_id")}
    if criteria and len(requirements) < len(criteria):
        issues.append("not every acceptance criterion is represented in requirement_model")
    missing_alignment = sorted(item for item in requirement_ids if item not in aligned_ids)
    if missing_alignment:
        issues.append("requirements missing project_alignment: " + ", ".join(missing_alignment))
    invalid_alignment = sorted(item for item in aligned_ids if item not in requirement_ids)
    if invalid_alignment:
        issues.append("project_alignment references unknown requirements: " + ", ".join(invalid_alignment))
    for item in alignments:
        if item.get("status") == "matched" and not item.get("evidence"):
            issues.append(f"matched alignment {item.get('requirement_id') or '?'} has no evidence")
    for item in decisions:
        if not item.get("because"):
            issues.append(f"design decision {item.get('id') or item.get('decision') or '?'} has no because")
    if not (investigation.get("patch_planning_facts") or investigation.get("patch_planning_context")):
        issues.append("design plan has no grounded investigation facts to rely on")
    return issues


def _legacy_user_question(gap: dict, *, analysis_id: str, origin_message: str) -> dict:
    gap_id = gap.get("id") or "design-gap"
    question = gap.get("question") or "请明确这个设计决策？"
    return {
        "id": gap_id,
        "analysis_id": analysis_id,
        "question": question,
        "origin_message": origin_message,
        "reason": gap.get("why", ""),
        "why_it_matters": gap.get("why", ""),
        "blocks_next_step": "patch_planning",
        "target_unknown_ids": [gap_id],
        "unknown_id": gap_id,
        "linked_unknown": {
            "id": gap_id,
            "question": question,
            "blocking": True,
            "resolution_strategy": "ask_user",
        },
        "options": [
            {"label": "采用最佳实践", "description": f"按当前项目事实选择最小实现：{question}"},
            {"label": "继续调查", "description": f"暂不做选择，继续寻找项目证据：{question}"},
        ],
        "custom_allowed": True,
    }


def user_question(gap: dict, *, analysis_id: str, origin_message: str) -> dict:
    gap_id = gap.get("id") or "design-gap"
    question = gap.get("question") or "Please clarify this design decision."
    why = gap.get("why", "")
    return {
        "id": gap_id,
        "analysis_id": analysis_id,
        "question": question,
        "origin_message": origin_message,
        "reason": why,
        "why_it_matters": why,
        "blocks_next_step": "patch_planning",
        "target_unknown_ids": [gap_id],
        "unknown_id": gap_id,
        "linked_unknown": {
            "id": gap_id,
            "question": question,
            "blocking": True,
            "resolution_strategy": "ask_user",
        },
        "options": [
            {
                "id": "best_judgment",
                "label": "Use best engineering judgment",
                "description": f"Choose the smallest design supported by current facts: {question}",
                "value": "Use best engineering judgment.",
            },
            {
                "id": "continue_investigation",
                "label": "Continue investigation",
                "description": f"Do not decide yet; look for more project evidence: {question}",
                "value": "Continue investigation.",
            },
        ],
        "custom_allowed": True,
    }


def _json_from_text(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("design plan must be a JSON object")
    return data


def _normalize_design(data: dict) -> dict:
    return {
        "summary": str(data.get("summary") or "").strip(),
        "requirement_model": _requirement_model(data.get("requirement_model")),
        "project_alignment": _project_alignment(data.get("project_alignment")),
        "decision_gaps": _decision_gaps(data.get("decision_gaps")),
        "design_decisions": _design_decisions(data.get("design_decisions")),
        "out_of_scope": _strings(data.get("out_of_scope")),
    }


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for raw in value if (item := str(raw).strip())]


def _requirement_model(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [
        {
            "id": str(item.get("id") or "").strip(),
            "concept": str(item.get("concept") or "").strip(),
            "behavior": str(item.get("behavior") or "").strip(),
            "source": str(item.get("source") or "").strip(),
        }
        for item in value
        if isinstance(item, dict) and (item.get("concept") or item.get("behavior"))
    ]


def _project_alignment(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [
        {
            "requirement_id": str(item.get("requirement_id") or "").strip(),
            "status": _one_of(item.get("status"), {"matched", "missing", "ambiguous"}, "ambiguous"),
            "project_fact": str(item.get("project_fact") or "").strip(),
            "evidence": _strings(item.get("evidence")),
        }
        for item in value
        if isinstance(item, dict) and (item.get("requirement_id") or item.get("project_fact"))
    ]


def _decision_gaps(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [
        {
            "id": str(item.get("id") or "").strip(),
            "question": str(item.get("question") or "").strip(),
            "blocks_implementation": bool(item.get("blocks_implementation")),
            "why": str(item.get("why") or "").strip(),
        }
        for item in value
        if isinstance(item, dict) and (item.get("question") or item.get("why"))
    ]


def _design_decisions(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [
        {
            "id": str(item.get("id") or "").strip(),
            "decision": str(item.get("decision") or "").strip(),
            "because": _strings(item.get("because")),
        }
        for item in value
        if isinstance(item, dict) and item.get("decision")
    ]


def _one_of(value, allowed: set[str], fallback: str) -> str:
    value = str(value or "").strip()
    return value if value in allowed else fallback
