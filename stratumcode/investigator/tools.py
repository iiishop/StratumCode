from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator

from ..agent.tools import openai_tool_schema
from ..agent_runtime import start_event
from ..tools import registry
from .constants import DISCOVERY_CONTRACT_FIELDS, INVESTIGATION_CAPABILITY, _REPAIR_ALLOWED_TOOL_NAMES
from .evidence import _observation_evidence_excerpt
from .ids import _known_unknowns_by_canonical_id, _normalize_unknown_id, _target_unknown_ids


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


def _tool_call_subject(name: str, arguments: dict) -> str:
    if name in {"read", "glob", "grep", "code_nav", "lsp_tool", "webfetch"}:
        value = arguments.get("path") or arguments.get("pattern") or arguments.get("query") or arguments.get("url")
        if not value and name == "lsp_tool":
            value = arguments.get("language") or arguments.get("server") or arguments.get("action")
        if not value and name == "grep":
            patterns = arguments.get("patterns") if isinstance(arguments.get("patterns"), list) else []
            if patterns:
                value = f"{len(patterns)} patterns"
        return f"({value})" if value else ""
    if name == "subagent":
        value = arguments.get("agent") or arguments.get("name")
        return f"({value})" if value else ""
    if name == "resolve_unknowns":
        ids = [
            str(item.get("unknown_id") or item.get("id") or "").strip()
            for item in arguments.get("resolutions", [])
            if isinstance(item, dict)
        ]
        ids = [item for item in ids if item]
        return f"({', '.join(ids[:4])})" if ids else ""
    if name in {"record_investigation_findings", "finish_investigation"}:
        summary = str(arguments.get("summary") or "").strip()
        return f"({summary[:80]})" if summary else ""
    return ""


def _tool_cache_key(name: str, arguments: dict) -> str:
    ignored = (
        set()
        if name in {"record_investigation_findings", "resolve_unknowns", "finish_investigation"}
        else {"reason", "investigation_contract", "discovery_contract", *DISCOVERY_CONTRACT_FIELDS}
    )
    comparable = {
        key: value
        for key, value in arguments.items()
        if key not in ignored
    }
    return f"{name}:{json.dumps(comparable, ensure_ascii=False, sort_keys=True)}"


def _run_tool_stream(name: str, call_id: str, arguments: dict, workspace_dir: str, analysis: dict | None = None, *, relax_discovery_contract: bool = False) -> Iterator[dict]:
    registered_tool = registry.get(name)
    if (
        registered_tool is None
        or INVESTIGATION_CAPABILITY not in registered_tool.capabilities
    ):
        raise ValueError(f"unknown investigation tool: {name or 'tool'}")
    target_unknown_ids = _target_unknown_ids(arguments)
    reason = str(arguments.pop("reason", "") or "").strip()
    orientation = bool(arguments.pop("orientation", False))
    discovery_contract = _extract_discovery_contract(arguments)
    arguments.pop("target_unknown_ids", None)
    _validate_tool_contract(
        name,
        target_unknown_ids=target_unknown_ids,
        reason=reason,
        orientation=orientation,
        discovery_contract=discovery_contract,
        analysis=analysis,
        relax_discovery_contract=relax_discovery_contract,
    )

    if name == "subagent" and (arguments.get("agent") or arguments.get("name")):
        agent = str(arguments.get("agent") or arguments.get("name"))
        if agent.strip().removeprefix("@").casefold() == "hypothesis-verifier":
            from .. import subagents

            task = str(arguments.get("task") or "")
            _reject_batched_hypothesis(task)
            done = {}
            for packet in subagents.run_stream(agent, task, workspace_dir):
                if packet.get("op") == "done":
                    done = packet
                else:
                    yield packet
            if done.get("error"):
                raise ValueError(str(done["error"]))
            if not done:
                raise ValueError("hypothesis verifier returned no result")
            done["target_unknown_ids"] = target_unknown_ids
            done["reason"] = reason
            done["orientation"] = orientation
            done["investigation_contract"] = discovery_contract
            return json.dumps(done, ensure_ascii=False)

    tool = registered_tool
    yield start_event(call_id, registry.event_type(name), {
        "name": name,
        "description": tool.description,
        "status": "running",
        "open": False,
        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
        "output": "",
        "target_unknown_ids": target_unknown_ids,
        "reason": reason,
        "orientation": orientation,
        "investigation_contract": discovery_contract,
    })
    result = asyncio.run(tool.execute(arguments, {"directory": workspace_dir}))
    yield {"op": "update", "id": call_id, "patch": {
        "status": "error" if result.title.startswith("[error]") else "done",
        "title": result.title,
        "output": result.output,
        "metadata": {
            **result.metadata,
            "target_unknown_ids": target_unknown_ids,
            "reason": reason,
            "orientation": orientation,
            "investigation_contract": discovery_contract,
        },
    }}
    return json.dumps({
        "tool_call_id": call_id,
        "target_unknown_ids": target_unknown_ids,
        "reason": reason,
        "orientation": orientation,
        "investigation_contract": discovery_contract,
        "title": result.title,
        "output": result.output,
        "metadata": {
            **result.metadata,
            "target_unknown_ids": target_unknown_ids,
            "reason": reason,
            "orientation": orientation,
            "investigation_contract": discovery_contract,
        },
    }, ensure_ascii=False)


def _extract_discovery_contract(arguments: dict) -> dict[str, str]:
    nested = arguments.pop("investigation_contract", None)
    if not isinstance(nested, dict):
        nested = arguments.pop("discovery_contract", None)
    if not isinstance(nested, dict):
        nested = {}
    return {
        field: str(arguments.pop(field, "") or nested.get(field) or "").strip()
        for field in DISCOVERY_CONTRACT_FIELDS
    }


def _validate_tool_contract(
    name: str,
    *,
    target_unknown_ids: list[str],
    reason: str,
    orientation: bool,
    analysis: dict | None,
    discovery_contract: dict[str, str] | None = None,
    relax_discovery_contract: bool = False,
) -> None:
    if name == "lsp_tool":
        return
    if not reason:
        raise ValueError(f"{name} requires reason")
    discovery_contract = discovery_contract or {}
    if name != "clearify" and not relax_discovery_contract:
        missing = [
            field for field in DISCOVERY_CONTRACT_FIELDS
            if not str(discovery_contract.get(field) or "").strip()
        ]
        if missing:
            raise ValueError(
                f"{name} requires discovery contract fields: " + ", ".join(missing)
            )
    known = _known_unknowns_by_canonical_id(analysis)
    if not orientation and not target_unknown_ids:
        raise ValueError(f"{name} requires target_unknown_ids unless orientation is true")
    unknown = [
        item for item in target_unknown_ids
        if known and _normalize_unknown_id(item) not in known
    ]
    if unknown:
        valid_ids = sorted(known) if known else []
        raise ValueError(
            f"{name} target_unknown_ids not in task contract: {', '.join(unknown)}. "
            "Use exact unknown ids from the task contract unknowns list"
            + (f": {', '.join(valid_ids[:12])}" + ("..." if len(valid_ids) > 12 else "") if valid_ids else "")
        )
    if name == "clearify":
        # engineering_decision（如 CLI 输入格式约定）同样是用户偏好型决策，
        # _pending_clearify_unknown 已允许它们进入 CLEARIFY 阶段——工具校验必须同步放宽。
        invalid = [
            item for item in target_unknown_ids
            if known.get(_normalize_unknown_id(item), {}).get("type") not in ("product_decision", "engineering_decision")
        ]
        if invalid:
            raise ValueError(
                "clearify requires product_decision or engineering_decision targets: " + ", ".join(invalid)
            )


def _tool_observation(name: str, call_id: str, output: str) -> dict:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    raw_metadata = data.get("metadata")
    metadata: dict = raw_metadata if isinstance(raw_metadata, dict) else {}
    evidence = str(data.get("output") or output)
    return {
        "id": call_id,
        "tool": name,
        "title": str(data.get("title") or name),
        "summary": _short_observation(evidence),
        "evidence_excerpt": _observation_evidence_excerpt(evidence),
        "_grounding_evidence": evidence,
        "verification": data.get("run") if isinstance(data.get("run"), dict) else {},
        "target_unknown_ids": data.get("target_unknown_ids") or metadata.get("target_unknown_ids") or [],
        "reason": data.get("reason") or metadata.get("reason") or "",
        "orientation": bool(data.get("orientation") or metadata.get("orientation")),
        "investigation_contract": (
            data.get("investigation_contract")
            if isinstance(data.get("investigation_contract"), dict)
            else metadata.get("investigation_contract")
            if isinstance(metadata.get("investigation_contract"), dict)
            else {}
        ),
        "path": metadata.get("path", ""),
        "mtime_ns": metadata.get("mtime_ns"),
        "size": metadata.get("size"),
    }


def _short_observation(value) -> str:
    text = " ".join(str(value or "").split())
    return text[:240]


def _reject_batched_hypothesis(task: str) -> None:
    text = " ".join((task or "").split())
    numbered = len(re.findall(r"(?:^|\s)[1-7]\.\s", text))
    if numbered >= 2:
        raise ValueError(
            "hypothesis-verifier accepts exactly one atomic belief; split this numbered list into separate calls"
        )
    lowered = text.casefold()
    if any(phrase in lowered for phrase in (
        "following clauses",
        "for each clause",
        "all of these",
        "the following hypotheses",
        "verify these",
    )):
        raise ValueError(
            "hypothesis-verifier accepts exactly one atomic belief; verify one belief at a time"
        )


def _tool_arguments(raw: str | None) -> dict:
    try:
        arguments = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid tool JSON: {exc}") from exc
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    return arguments
