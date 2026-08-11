from __future__ import annotations

import json

from ..agent.tools import openai_tool_schema
from .constants import DISCOVERY_CONTRACT_FIELDS, _REPAIR_ALLOWED_TOOL_NAMES


# Phase -> 工具集映射。集中定义，避免主循环内散落的 if/elif。
def _phase_tools(
    phase,
    *,
    tools: list[dict],
    finish_evidence_blocked: bool = False,
) -> list[dict]:
    if phase == "clearify":
        return _named(tools, "clearify")
    if phase == "verify":
        return _named(tools, "subagent")
    if phase == "repair":
        return _named(tools, *(_REPAIR_ALLOWED_TOOL_NAMES | {"finish_investigation"}))
    if phase == "finish_with_evidence_gap":
        return _named(tools, *(_REPAIR_ALLOWED_TOOL_NAMES | {"finish_investigation"}))
    if phase == "finish":
        return [_finish_tool_schema()]
    if phase == "synthesize":
        return [
            _resolve_unknowns_tool_schema(),
            _record_findings_tool_schema(),
            _finish_tool_schema(),
        ]
    if phase == "read_only_finish":
        return [_finish_tool_schema()]
    if phase == "resolve":
        return [
            _resolve_unknowns_tool_schema(),
            _record_findings_tool_schema(),
            _finish_tool_schema(),
        ]
    if phase == "discovery_required":
        return [
            tool for tool in tools
            if ((tool.get("function") or {}).get("name") or "")
            not in {
                "clearify",
                "finish_investigation",
                "record_investigation_findings",
                "resolve_unknowns",
                "subagent",
            }
        ]
    return list(tools)


def _named(tools: list[dict], *names: str) -> list[dict]:
    return [
        tool for tool in tools
        if ((tool.get("function") or {}).get("name") or "") in names
    ]


def _phase_tool_choice(phase) -> str | dict:
    forced = {
        "clearify": "clearify",
        "verify": "subagent",
        # REPAIR 不强制 record：repair 提示词要求"先用 read/grep 取证、
        # 再 record 追加缺失项"。强制 record 会让模型无法取证，只能空转
        # 重写结论（U1/U2/U3 类 REPAIR 死循环根因）。工具集仍限定在
        # _REPAIR_ALLOWED_TOOL_NAMES + finish，模型必须推进 repair。
        "finish": "finish_investigation",
        "read_only_finish": "finish_investigation",
    }
    name = forced.get(phase)
    if name:
        return {"type": "function", "function": {"name": name}}
    if phase == "repair":
        return "required"
    return "required"


def _investigation_tool_schema(name: str, description: str, parameters: dict) -> dict:
    schema = json.loads(json.dumps(parameters))
    properties = schema.setdefault("properties", {})
    properties["target_unknown_ids"] = {
        "type": "array",
        "items": {"type": "string"},
        "description": "Task contract unknown IDs this tool call is intended to resolve or reduce.",
    }
    properties["reason"] = {
        "type": "string",
        "description": "One short sentence explaining why this call helps those unknowns.",
    }
    properties["orientation"] = {
        "type": "boolean",
        "description": "True only for a broad first-pass orientation call that cannot yet target a specific unknown.",
    }
    if name != "clearify":
        properties["hypothesis"] = {
            "type": "string",
            "description": "One falsifiable claim this call will test. Do not use a generic exploration goal.",
        }
        properties["expected_observation"] = {
            "type": "string",
            "description": "The concrete result that would support, oppose, or narrow the hypothesis.",
        }
        properties["decision_impact"] = {
            "type": "string",
            "description": "How the result will update a target unknown, belief, or next investigation branch.",
        }
        properties["stop_condition"] = {
            "type": "string",
            "description": "When this line of investigation should stop or switch to recording/resolution.",
        }
    required = schema.setdefault("required", [])
    for field in ("target_unknown_ids", "reason"):
        if field not in required:
            required.append(field)
    if name != "clearify":
        for field in DISCOVERY_CONTRACT_FIELDS:
            if field not in required:
                required.append(field)
    return openai_tool_schema(name, description, schema)


def _record_findings_tool_schema() -> dict:
    return openai_tool_schema(
        "record_investigation_findings",
        "Start runtime slot-based recording of grounded findings before finishing.",
        {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "One short reason why current observations should be recorded now. Do not include findings JSON; runtime will request slots.",
                },
            },
            "required": ["reason"],
        },
    )


def _resolve_unknowns_tool_schema() -> dict:
    return openai_tool_schema(
        "resolve_unknowns",
        "Record explicit resolutions for investigation unknowns using existing observation or belief ids only.",
        {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "One short reason why these unknowns can be resolved now.",
                },
                "resolutions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "unknown_id": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["resolved", "partially_resolved", "needs_clearify", "deferred"],
                            },
                            "kind": {
                                "type": "string",
                                "enum": ["direct_fact", "derived_inference", "user_decision", "deferred"],
                                "description": "Use direct_fact only when the cited observation directly states the answer; use derived_inference for cross-file or causal reasoning.",
                            },
                            "answer": {"type": "string"},
                            "observation_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Preferred. Existing observation refs such as obs_1 from the runtime prompt, or exact observation ids.",
                            },
                            "evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Legacy alias for observation_ids. Prefer observation_ids for new calls.",
                            },
                            "belief_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Existing belief ids that support the answer.",
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["unknown_id", "status", "answer", "reason"],
                    },
                },
            },
            "required": ["reason", "resolutions"],
        },
    )


def _finish_tool_schema() -> dict:
    return openai_tool_schema(
        "finish_investigation",
        "Finish investigation using previously recorded findings.",
        {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Visible one-sentence reason why the investigation is complete or must hand off.",
                },
                "summary": {"type": "string"},
                "patch_planning_facts": {"type": "array", "items": {"type": "string"}},
                "patch_planning_context": {"type": "array", "items": {"type": "string"}},
                "recommended_next_step": {
                    "type": "string",
                    "enum": ["patch_planning", "continue_investigation", "done"],
                },
                "bugfix_readiness": {
                    "type": "object",
                    "description": "Required when finishing an implement bugfix for patch planning.",
                    "properties": {
                        "failure_reproduced_or_observed": {"type": "boolean"},
                        "root_cause_or_failing_boundary_identified": {"type": "boolean"},
                        "patch_target_identified": {"type": "boolean"},
                        "expected_behavior_change_defined": {"type": "boolean"},
                        "validation_scenario_defined": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "required": ["reason", "recommended_next_step"],
        },
    )
