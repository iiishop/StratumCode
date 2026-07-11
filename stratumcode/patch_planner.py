from __future__ import annotations

import json
import platform
import re
from collections.abc import Iterator
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

MAX_OUTPUT_TOKENS = 3072


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

    assistant = _call_model(
        provider,
        model,
        [
            {"role": "system", "content": _system_prompt(app_settings.get_output_language())},
            {"role": "user", "content": _user_prompt(message, analysis, investigation, design_plan, workspace_dir)},
        ],
        tools=[],
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
        _add_usage(usage_total, usage)
        yield start_event(f"{run_id}-usage", "usage", {"delta": usage, "total": usage_total})

    plan = _normalize_plan(_json_from_text(_content_text(assistant.get("content"))))
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


def _user_prompt(message: str, analysis: dict, investigation: dict, design_plan: dict, workspace_dir: str) -> str:
    return json.dumps({
        "platform": platform.system(),
        "workspace_root": workspace_dir,
        "user_request": message,
        "task": {
            "intent": analysis.get("intent", {}),
            "acceptance_criteria": analysis.get("acceptance_criteria", []),
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
