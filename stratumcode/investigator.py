from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import sys
import time
from collections.abc import Iterator
from enum import StrEnum
from functools import lru_cache
from itertools import count
from pathlib import Path
from uuid import uuid4

from . import app_settings, clearify_runtime, model_settings, prompt, providers, skill_runtime
from .agent.tools import openai_tool_schema
from .agent_runtime import (
    add_usage as _add_usage,
    call_model as _call_model,
    assistant_message as _assistant_message,
    assistant_visible_text as _assistant_visible_text,
    content_text as _content_text,
    empty_usage as _empty_usage,
    execute_skill_tool_call,
    start_event,
    tool_error_json,
    usage_delta as _usage_delta,
)
from .json2slots import JSONValue, json2slots
from .status.task_contract import (
    LEGACY_ASK_USER_STRATEGY,
    LEGACY_NEEDS_USER_STATUS,
    _unknowns as _contract_unknowns,
)
from .status.task_analysis import _analysis_requests_implementation, _json_candidates
from .status.task_updates import _unknown_task_status
from .tools import registry

INVESTIGATION_CAPABILITY = "investigation"
PROJECT_EVIDENCE_CAPABILITY = "investigation.project_evidence"
MAX_REPEATED_TOOL_ERRORS = 3
READ_ONLY_SUMMARY_MIN_RESOLUTION_RATIO = 0.35
MAX_REPEATED_RECORD_NO_PROGRESS = 3
MAX_DUPLICATE_NO_PROGRESS = 2
MAX_PENDING_DISCOVERY_OBSERVATIONS = 8
REQUIRED_FINDING_SLOT_ATTEMPTS = 2
REQUIRED_AUDIT_ATTEMPTS = 2
# During semantic repair the model may still gather missing evidence, but
# must not finish or resolve until the audit passes.
_REPAIR_ALLOWED_TOOL_NAMES = frozenset({
    "read",
    "grep",
    "glob",
    "code_nav",
    "lsp_tool",
    "record_investigation_findings",
})
OBSERVATION_EVIDENCE_CHARS = 8000
GROUNDING_LITERAL_SPAN_CONTEXT_LINES = 2
GROUNDING_LITERAL_SPAN_MAX_LINES = 16
GROUNDING_LITERAL_SPAN_MAX_LINE_CHARS = 360
GROUNDING_LITERAL_SPAN_MAX_ITEMS = 12
PROJECT_FILE_SCAN_LIMIT = 20000
DISCOVERY_CONTRACT_FIELDS = (
    "hypothesis",
    "expected_observation",
    "decision_impact",
    "stop_condition",
)
FINDING_FIELDS = (
    "beliefs",
    "resolutions",
    "new_unknowns",
    "unknowns",
    "user_decisions_required",
    "task_updates",
)
RESOLUTION_KINDS = {"direct_fact", "derived_inference", "user_decision", "deferred"}
SEMANTIC_AUDIT_KINDS = {"derived_inference"}
# Compatibility hook for integrations that patched this set before tool capabilities existed.
PROJECT_EVIDENCE_TOOLS: set[str] = set()
CLEARIFY_RESOLUTION_REASON = "Answered by the user through clearify."
CLEARIFY_UNRESOLVED_REASON = "User could not answer through clearify; continue project investigation."
GROUNDING_LITERAL_REASON_PREFIX = "Cited observations do not contain the claimed code literal(s):"
STATE_WRITE_REASON_PREFIX = "Cited observations contain state writes omitted from the resolution:"

# 否定性结论（absence）特征词：答案声称"不存在/未找到/未定义"时，
# grounding 检查降级（见 _resolution_is_absence_claim）。
_NEGATIVE_CLAIM_RE = re.compile(
    r"(未找到|未发现|未定义|未描述|未提供|未提及|未记录|没有找到|不存在|"
    r"没有独立|无独立|没有任何|均未|"
    r"not found|does not exist|absent|no evidence|undocumented)"
)

# read_only 模式下按设计不可得的"运行时证据"要求（审计模型常误提）：
# 这类 missing 在只读调查中被过滤，不进入 REPAIR（见 _apply_investigation_audit）。
_RUNTIME_EVIDENCE_RE = re.compile(
    r"(运行时|runtime|测试|测试用例|日志|复现|reproduce|"
    r"实际运行|运行表现|可观察.*运行|运行.*验证|跑一下|执行.*验证|"
    r"可复现行为)"
)
RECORD_RECOVERY_REASON = "Record pending observations and required resolutions."


class InvestigationPhase(StrEnum):
    """互斥的调查阶段：一个时刻只能处于一个 phase。

    由 _investigation_directive 计算，主循环按 phase 取工具集。
    枚举互斥保证 REPAIR 与 FINISH 不可能同时成立——
    消除了旧 if/elif 链中 already_resolved 与 repair 列表
    靠分支顺序"碰巧"互斥的隐式耦合（d5eef05a 根因）。
    """
    CLEARIFY = "clearify"
    VERIFY = "verify"
    REPAIR = "repair"
    FINISH = "finish"
    FINISH_WITH_EVIDENCE_GAP = "finish_with_evidence_gap"
    SYNTHESIZE = "synthesize"
    READ_ONLY_FINISH = "read_only_finish"
    RESOLVE = "resolve"
    DISCOVERY_REQUIRED = "discovery_required"
    DISCOVER = "discover"


# Phase -> 工具集映射。集中定义，避免主循环内散落的 if/elif。
def _phase_tools(
    phase: InvestigationPhase,
    *,
    tools: list[dict],
    finish_evidence_blocked: bool = False,
) -> list[dict]:
    def _named(*names: str) -> list[dict]:
        return [
            tool for tool in tools
            if ((tool.get("function") or {}).get("name") or "") in names
        ]
    if phase == InvestigationPhase.CLEARIFY:
        return _named("clearify")
    if phase == InvestigationPhase.VERIFY:
        return _named("subagent")
    if phase == InvestigationPhase.REPAIR:
        return _named(*(_REPAIR_ALLOWED_TOOL_NAMES | {"finish_investigation"}))
    if phase == InvestigationPhase.FINISH_WITH_EVIDENCE_GAP:
        return _named(*(_REPAIR_ALLOWED_TOOL_NAMES | {"finish_investigation"}))
    if phase == InvestigationPhase.FINISH:
        return [_finish_tool_schema()]
    if phase == InvestigationPhase.SYNTHESIZE:
        return [
            _resolve_unknowns_tool_schema(),
            _record_findings_tool_schema(),
            _finish_tool_schema(),
        ]
    if phase == InvestigationPhase.READ_ONLY_FINISH:
        return [_finish_tool_schema()]
    if phase == InvestigationPhase.RESOLVE:
        return [
            _resolve_unknowns_tool_schema(),
            _record_findings_tool_schema(),
            _finish_tool_schema(),
        ]
    if phase == InvestigationPhase.DISCOVERY_REQUIRED:
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


def _phase_tool_choice(phase: InvestigationPhase) -> str | dict:
    forced = {
        InvestigationPhase.CLEARIFY: "clearify",
        InvestigationPhase.VERIFY: "subagent",
        # REPAIR 不强制 record：repair 提示词要求"先用 read/grep 取证、
        # 再 record 追加缺失项"。强制 record 会让模型无法取证，只能空转
        # 重写结论（U1/U2/U3 类 REPAIR 死循环根因）。工具集仍限定在
        # _REPAIR_ALLOWED_TOOL_NAMES + finish，模型必须推进 repair。
        InvestigationPhase.FINISH: "finish_investigation",
        InvestigationPhase.READ_ONLY_FINISH: "finish_investigation",
    }
    name = forced.get(phase)
    if name:
        return {"type": "function", "function": {"name": name}}
    if phase == InvestigationPhase.REPAIR:
        return "required"
    return "required"


def _investigation_directive(
    *,
    recorded_findings: dict,
    analysis: dict,
    tools: list[dict],
    observations: list[dict],
    semantic_repair_required_ids: set[str],
    resolution_required_ids: list[str],
    discovery_required_ids: list[str],
    clearify_unknown: dict | None,
    verification_request: dict | None,
    finish_evidence_blocked: bool,
    force_synthesis_reason: str,
    semantic_gate_enabled: bool,
    read_only_no_unknowns: bool,
) -> tuple[InvestigationPhase, list[dict], str | dict, str | None]:
    """集中计算当前调查阶段与对应工具集（唯一决策入口）。

    等价性：映射旧主循环 L192-302 的五个 if/elif 分支——
      clearify -> CLEARIFY
      verification -> VERIFY
      semantic_repair_required_ids -> REPAIR
      _recorded_resolves_initial_unknowns -> FINISH / FINISH_WITH_EVIDENCE_GAP
      force_synthesis / read_only -> SYNTHESIZE / READ_ONLY_FINISH
      resolution_required_ids -> RESOLVE
      默认 -> DISCOVER
    行为不变，仅决策集中。互斥由 InvestigationPhase 枚举结构性保证。
    返回 (phase, current_tools, current_tool_choice, prompt)
    """
    prompt: str | None = None
    if clearify_unknown:
        prompt = _clearify_required_prompt(clearify_unknown)
        return (
            InvestigationPhase.CLEARIFY,
            _phase_tools(InvestigationPhase.CLEARIFY, tools=tools),
            _phase_tool_choice(InvestigationPhase.CLEARIFY),
            prompt,
        )
    pending_evidence = _clearify_pending_evidence_unknowns(recorded_findings)
    if pending_evidence:
        prompt = (
            "The user has already answered the pending product decision(s), but "
            "those clearify answers have no code evidence yet. Use "
            "read/grep/glob/code_nav to locate the actual implementation sites, "
            "record the evidence, then call finish_investigation again. "
            "Pending evidence: "
            f"{json.dumps(pending_evidence, ensure_ascii=False)}"
        )
        return (
            InvestigationPhase.FINISH_WITH_EVIDENCE_GAP,
            _phase_tools(
                InvestigationPhase.FINISH_WITH_EVIDENCE_GAP,
                tools=tools,
                finish_evidence_blocked=True,
            ),
            _phase_tool_choice(InvestigationPhase.FINISH_WITH_EVIDENCE_GAP),
            prompt,
        )
    if verification_request:
        prompt = (
            "The semantic quality gate requires independent verification before this "
            "resolution can reach Design. Call subagent with agent hypothesis-verifier "
            f"for unknown {verification_request['unknown_id']} and the exact atomic "
            f"hypothesis: {verification_request['hypothesis']}"
        )
        return (
            InvestigationPhase.VERIFY,
            _phase_tools(InvestigationPhase.VERIFY, tools=tools),
            _phase_tool_choice(InvestigationPhase.VERIFY),
            prompt,
        )
    if semantic_repair_required_ids:
        repair = _semantic_repair_payload(recorded_findings, semantic_repair_required_ids)
        prompt = (
            "The semantic quality gate accepted the existing recorded findings except "
            "for the listed missing requirements. Do not regenerate, restate, or replace "
            "already recorded ids. If you need more evidence, use read/grep/glob/code_nav "
            "first to obtain the missing observations, then call record_investigation_findings "
            "to append only the missing belief(s), then add a minimal resolution patch with "
            "repair_mode=append_missing_only and only the new belief_ids/evidence. "
            "After the missing list is addressed, call finish_investigation to let the "
            "quality gate re-audit the resolutions. The missing list: "
            f"{json.dumps(repair, ensure_ascii=False)}"
        )
        return (
            InvestigationPhase.REPAIR,
            _phase_tools(InvestigationPhase.REPAIR, tools=tools),
            _phase_tool_choice(InvestigationPhase.REPAIR),
            prompt,
        )
    if _recorded_resolves_initial_unknowns(
        recorded_findings,
        analysis,
        repair_ids=semantic_repair_required_ids,
    ):
        if finish_evidence_blocked:
            prompt = (
                "The previous finish attempt was rejected because a resolution "
                "references a file that was never read. Use read/grep/glob/code_nav "
                "to obtain the missing observations, then call finish_investigation again."
            )
            return (
                InvestigationPhase.FINISH_WITH_EVIDENCE_GAP,
                _phase_tools(
                    InvestigationPhase.FINISH_WITH_EVIDENCE_GAP,
                    tools=tools,
                    finish_evidence_blocked=True,
                ),
                _phase_tool_choice(InvestigationPhase.FINISH_WITH_EVIDENCE_GAP),
                prompt,
            )
        return (
            InvestigationPhase.FINISH,
            _phase_tools(InvestigationPhase.FINISH, tools=tools),
            _phase_tool_choice(InvestigationPhase.FINISH),
            None,
        )
    if force_synthesis_reason and not discovery_required_ids:
        prompt = force_synthesis_reason
        return (
            InvestigationPhase.SYNTHESIZE,
            _phase_tools(InvestigationPhase.SYNTHESIZE, tools=tools),
            _phase_tool_choice(InvestigationPhase.SYNTHESIZE),
            prompt,
        )
    if read_only_no_unknowns:
        prompt = (
            "The task contract has no project facts to investigate. Answer the user's "
            "request directly in finish_investigation.summary. The summary must satisfy "
            "the acceptance criteria; do not merely classify, restate, or explain why "
            "the request does not require project inspection. Do not mention the current "
            "workspace, speculate about its code, or offer additional project work unless "
            "the user requested it."
        )
        return (
            InvestigationPhase.READ_ONLY_FINISH,
            _phase_tools(InvestigationPhase.READ_ONLY_FINISH, tools=tools),
            _phase_tool_choice(InvestigationPhase.READ_ONLY_FINISH),
            prompt,
        )
    if resolution_required_ids:
        prompt = _resolution_required_prompt(
            resolution_required_ids,
            recorded_findings,
            observations=observations,
            analysis=analysis,
        )
        return (
            InvestigationPhase.RESOLVE,
            _phase_tools(InvestigationPhase.RESOLVE, tools=tools),
            _phase_tool_choice(InvestigationPhase.RESOLVE),
            prompt,
        )
    if discovery_required_ids:
        prompt = (
            "These blocking project unknowns still lack enough project evidence: "
            + ", ".join(discovery_required_ids)
            + ". Continue discovery with read/grep/glob/code_nav/lsp_tool. "
            "Do not call record_investigation_findings, resolve_unknowns, or "
            "finish_investigation until a new observation materially narrows one "
            "of these unknowns."
        )
        return (
            InvestigationPhase.DISCOVERY_REQUIRED,
            _phase_tools(InvestigationPhase.DISCOVERY_REQUIRED, tools=tools),
            _phase_tool_choice(InvestigationPhase.DISCOVERY_REQUIRED),
            prompt,
        )
    return (
        InvestigationPhase.DISCOVER,
        _phase_tools(InvestigationPhase.DISCOVER, tools=tools),
        _phase_tool_choice(InvestigationPhase.DISCOVER),
        _discover_lsp_first_prompt(analysis),
    )


def _discover_lsp_first_prompt(analysis: dict) -> str | None:
    if not any(
        isinstance(item, dict)
        and item.get("resolution_strategy") == "investigate_project"
        for item in analysis.get("unknowns", [])
    ):
        return None
    return (
        "Discovery routing: for source-code questions, prefer LSP-first "
        "navigation before whole-file reads. Use code_nav symbols for a known "
        "file, code_nav inspect/definition/references for a known symbol, then "
        "read only the relevant line ranges needed as grounding evidence. If "
        "code_nav reports an unavailable language server, use lsp_tool "
        "status/install once for that language; if LSP remains unavailable, "
        "fall back to grep/read and record that fallback. For cross-file, "
        "parent/caller, consumer, or state-transition claims, gather semantic "
        "references or the corresponding caller/consumer observations before "
        "resolving."
    )


def investigation_stream(
    *,
    message: str,
    analysis: dict,
    context: list[str],
    workspace_dir: str,
    max_rounds: int | None = None,
    findings: list[str] | None = None,
    previous_observations: list[dict] | None = None,
    previous_knowledge: list[dict] | None = None,
    previous_findings: dict | None = None,
    preserve_grounding_evidence: bool = False,
) -> Iterator[dict]:
    setting = (
        model_settings.resolve(model_settings.DEFAULT_STAGE)
        or model_settings.resolve(model_settings.EVIDENCE_STAGE)
    )
    if setting is None:
        raise ValueError(
            "No model configured for investigation. Configure a default or evidence model in Providers."
        )

    provider = setting["provider"]
    model = setting["model_id"]
    pricing_rules = providers.get_model_pricing(provider["id"], model)
    usage_total = _empty_usage(pricing_rules)
    effort_profile = app_settings.get_effort_profile(analysis.get("effort"))
    quality_gate = str(analysis.get("quality_gate") or effort_profile["quality_gate"]).strip().casefold()
    semantic_gate_enabled = quality_gate != "basic"
    subagent_enabled = bool(effort_profile["subagent_enabled"])
    rounds_per_unknown = (
        int(effort_profile["investigation_rounds"] or 0)
        if analysis.get("effort") and effort_profile["investigation_rounds"]
        else int(app_settings.get_round_limit("investigation_rounds") or 0)
    )
    if rounds_per_unknown <= 0:
        rounds_per_unknown = 2
    min_rounds = _minimum_investigation_rounds(analysis, rounds_per_unknown)
    # 方案 A：不设总轮数上限——调查由收敛条件（blocking unknown 全部解决 +
    # ready for patch planning）驱动结束；min_rounds 只是"至少跑 N×unknowns 轮"
    # 的深度底线。防死循环由 pass 级保护（_MAX_INVESTIGATION_PASSES）、
    # record 无进展检测与状态机 phase 强制推进承担。
    max_rounds = int(max_rounds or 0) if max_rounds is not None else 0
    run_id = uuid4().hex[:10]
    stage_id = f"{run_id}-stage"
    yield start_event(stage_id, "stage", {
        "name": "investigation",
        "label": "Investigate before patch planning",
        "state": "running",
        "phase": "understand",
        "model": model,
        "context_length": providers.model_context_length(provider["base_url"], provider["api_key"], model),
        "provider": provider["name"],
        "inherited": setting["inherited"],
    })

    messages = [
        {"role": "system", "content": prompt.build_investigation_static(app_settings.get_output_language())},
        {
            "role": "user",
            "content": prompt.build_investigation_context(
                analysis=analysis,
                message=message,
                directory=workspace_dir,
                platform=platform.system(),
                model=model,
                context=context,
                max_rounds=max_rounds,
            ),
        },
        {"role": "user", "content": message},
    ]
    if findings:
        messages.insert(2, {"role": "user", "content": "\n".join(findings)})
    prior_lines = _previous_context(previous_observations, previous_knowledge)
    if prior_lines:
        messages.insert(2, {"role": "user", "content": "\n".join(prior_lines)})
    tools = _investigation_tools()
    final = None
    observations = [
        dict(item)
        for item in previous_observations or []
        if isinstance(item, dict) and item.get("fresh", True)
    ]
    tool_cache = {}
    tool_cache_observation_ids: dict[str, str] = {}
    audit_cache: dict[str, dict] = {}
    recorded_findings = _continued_recorded_findings(previous_findings, observations)
    finalization_reason = "Investigation model stopped before finish_investigation; summarizing observed facts."
    pending_observation_ids: list[str] = []
    repeated_tool_error_name = ""
    repeated_tool_error_count = 0
    already_resolved_error_count = 0
    failed_tool_cache: dict[str, str] = {}
    stop_investigation = False
    verification_queue: list[dict] = []
    attempted_verifications: set[tuple[str, str]] = set()
    clearify_questions: dict[str, str] = {}
    last_quality_audit: dict = {}
    last_record_signature = _recorded_findings_signature(recorded_findings)
    repeated_record_no_progress = 0
    duplicate_no_progress_signature = ""
    duplicate_no_progress_count = 0
    duplicate_no_progress_total = 0
    force_synthesis_reason = ""
    force_discovery_ids: list[str] = []
    finish_evidence_blocked = False

    for round_index in _round_indexes(max_rounds, start=0):
        thinking_id = f"{run_id}-thinking-{round_index}"
        yield start_event(thinking_id, "thinking", {"text": "", "done": False, "open": True})
        current_tools = tools
        current_tool_choice = "required"
        clearify_unknown = _pending_clearify_unknown(
            recorded_findings,
            analysis,
            clearify_questions,
        )
        verification_request = verification_queue[0] if verification_queue else None
        semantic_repair_required_ids = (
            _semantic_repair_resolution_ids(recorded_findings)
            if semantic_gate_enabled
            else set()
        )
        resolution_required_ids = _unknowns_needing_resolution(recorded_findings, observations, analysis)
        resolution_required_ids = _dedupe_strings([
            *resolution_required_ids,
            *sorted(semantic_repair_required_ids),
            *(
                _pending_observation_unknown_ids(
                    observations,
                    pending_observation_ids,
                    analysis,
                    recorded_findings,
                )
                if len(pending_observation_ids) >= MAX_PENDING_DISCOVERY_OBSERVATIONS
                else []
            ),
        ])
        discovery_required_ids = list(force_discovery_ids)
        if force_synthesis_reason and not discovery_required_ids:
            discovery_required_ids = _unknowns_missing_project_evidence(
                recorded_findings,
                observations,
                analysis,
            )
        current_phase, current_tools, current_tool_choice, directive_prompt = _investigation_directive(
            recorded_findings=recorded_findings,
            analysis=analysis,
            tools=tools,
            observations=observations,
            semantic_repair_required_ids=semantic_repair_required_ids,
            resolution_required_ids=resolution_required_ids,
            discovery_required_ids=discovery_required_ids,
            clearify_unknown=clearify_unknown,
            verification_request=verification_request,
            finish_evidence_blocked=finish_evidence_blocked,
            force_synthesis_reason=force_synthesis_reason,
            semantic_gate_enabled=semantic_gate_enabled,
            read_only_no_unknowns=(
                analysis.get("execution_mode") == "read_only"
                and not analysis.get("unknowns")
            ),
        )
        # 方案 A：最少调查轮数未达到时，禁止提前结束（fast 也要跑
        # rounds_per_unknown × blocking_unknown 轮）。结束类 phase
        # （FINISH/READ_ONLY_FINISH/SYNTHESIZE）强制降级为 DISCOVERY_REQUIRED，
        # 且原 directive（如"调用 finish_investigation"）不再下发。
        budget_floor_active = (
            round_index < min_rounds
            and current_phase in (
                InvestigationPhase.FINISH,
                InvestigationPhase.READ_ONLY_FINISH,
                InvestigationPhase.SYNTHESIZE,
            )
        )
        if directive_prompt and not budget_floor_active:
            messages.append({"role": "user", "content": directive_prompt})
        if budget_floor_active:
            current_phase = InvestigationPhase.DISCOVERY_REQUIRED
            current_tools = _phase_tools(current_phase, tools=tools)
            current_tool_choice = "required"
            messages.append({
                "role": "user",
                "content": _minimum_rounds_prompt(min_rounds - round_index),
            })
        allowed_tool_names = {
            str(((tool.get("function") or {}).get("name")) or "")
            for tool in current_tools
            if isinstance(tool, dict)
        }
        try:
            assistant = _call_model(provider, model, messages, tools=current_tools, tool_choice=current_tool_choice)
        except ValueError as exc:
            reason = str(exc)
            yield {"op": "update", "id": thinking_id, "patch": {
                "text": reason,
                "done": True,
                "open": False,
            }}
            yield start_event(f"{run_id}-provider-error", "output", {
                "content": f"Provider request failed: {reason}",
                "streaming": False,
            })
            yield {"op": "update", "id": stage_id, "patch": {"state": "failed", "phase": "provider_error"}}
            return
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{round_index}", "usage", {
                "delta": usage,
                "total": usage_total,
            })

        tool_calls = assistant.get("tool_calls") or []
        content = _assistant_visible_text(assistant) or _tool_call_summary(tool_calls)
        messages.append(_assistant_message(assistant))
        yield {"op": "update", "id": thinking_id, "patch": {
            "text": content,
            "done": True,
            "open": bool(tool_calls),
        }}

        if not tool_calls:
            messages.append({"role": "user", "content": (
                "You did not call a tool. Continue by making an actual tool call, "
                "or call finish_investigation if the investigation is complete. "
                "Do not describe intended tool use in prose."
            )})
            continue

        round_error_names: set[str] = set()
        asked_clearify_ids: set[str] = set()
        for raw_call in tool_calls:
            call_id = raw_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = raw_call.get("function") or {}
            name = function.get("name") or ""
            arguments = {}
            if name == "load_skill":
                _, output, _ = execute_skill_tool_call(raw_call)
                yield from skill_runtime.pop_events()
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": output,
                })
                continue
            try:
                arguments = _investigation_tool_arguments(
                    name,
                    function.get("arguments"),
                    pending_observation_ids=pending_observation_ids,
                    resolution_required_ids=resolution_required_ids,
                )
                if name == "record_investigation_findings":
                    arguments = _record_arguments(arguments)
                if verification_request and name == "subagent":
                    arguments = {
                        "agent": "hypothesis-verifier",
                        "task": json.dumps({
                            "hypothesis": verification_request["hypothesis"],
                            "context": [
                                verification_request.get("reason", ""),
                                f"Target contract unknown: {verification_request['unknown_id']}",
                            ],
                        }, ensure_ascii=False),
                        "target_unknown_ids": [verification_request["unknown_id"]],
                        "reason": verification_request.get("reason")
                        or "Independently verify the material investigation inference.",
                        "hypothesis": verification_request["hypothesis"],
                        "expected_observation": (
                            "The verifier returns a supported, opposed, or inconclusive "
                            "verdict with evidence for the atomic hypothesis."
                        ),
                        "decision_impact": (
                            "The target resolution can be accepted, rejected, or kept "
                            "partial without repeating the same investigation."
                        ),
                        "stop_condition": (
                            "Stop after one independent verdict for this atomic hypothesis."
                        ),
                    }
                failed_key = _tool_cache_key(name, arguments)
                if failed_key in failed_tool_cache:
                    error_name = name or "invalid"
                    if error_name not in round_error_names:
                        repeated_tool_error_name = error_name
                        repeated_tool_error_count += 1
                        round_error_names.add(error_name)
                    output = json.dumps({
                        "error": {
                            "code": "duplicate_failed_tool_call",
                            "tool": name or "invalid",
                            "retryable": False,
                            "message": (
                                "The same tool arguments already failed. "
                                "Choose a different valid action; do not retry this call."
                            ),
                        },
                    }, ensure_ascii=False)
                    yield start_event(call_id, registry.event_type(name), {
                        "name": name or "invalid",
                        "description": "Investigation tool",
                        "status": "error",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                        "deduplicated": True,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                    if repeated_tool_error_count >= MAX_REPEATED_TOOL_ERRORS:
                        finalization_reason = (
                            "Runtime stopped an identical failed tool call loop: "
                            f"{name or 'invalid'}."
                        )
                        stop_investigation = True
                        break
                    continue
                if name not in allowed_tool_names:
                    if _recorded_resolves_initial_unknowns(
                        recorded_findings,
                        analysis,
                        repair_ids=semantic_repair_required_ids,
                    ) and name != "finish_investigation":
                        output = json.dumps({
                            "error": "investigation_already_resolved",
                            "retryable": True,
                            "required_tool": "finish_investigation",
                            "message": (
                                "All initial unknowns are resolved. Call finish_investigation now; "
                                "do not repeat discovery or recording tools."
                            ),
                        }, ensure_ascii=False)
                        yield start_event(call_id, registry.event_type(name), {
                            "name": name or "invalid",
                            "description": "Investigation tool",
                            "status": "error",
                            "open": False,
                            "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                            "output": output,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        already_resolved_error_count += 1
                        if already_resolved_error_count >= 2:
                            # Hard-lock: force the model to call finish_investigation
                            current_tool_choice = {"type": "function", "function": {"name": "finish_investigation"}}
                        continue
                    if resolution_required_ids and not semantic_repair_required_ids:
                        # 非 REPAIR 场景：初始 unknowns 已有证据记录，先 resolve
                        # 再发现。REPAIR 阶段（semantic_repair_required_ids 非空）
                        # 必须允许 read/grep 补证据——repair 提示词明确要求先取证
                        # 再 record，拦截 discovery 会让模型空转重写结论而死循环。
                        required_tool = (
                            "resolve_unknowns"
                            if "resolve_unknowns" in allowed_tool_names
                            else "record_investigation_findings"
                            if "record_investigation_findings" in allowed_tool_names
                            else ""
                        )
                        output = json.dumps({
                            "error": "resolution_required",
                            "retryable": True,
                            **({"required_tool": required_tool} if required_tool else {}),
                            "target_unknown_ids": resolution_required_ids,
                            "message": (
                                "Existing project evidence is already recorded for these unknowns. "
                                "Resolve explicit unknowns before calling more discovery tools."
                            ),
                        }, ensure_ascii=False)
                        yield start_event(call_id, registry.event_type(name), {
                            "name": name or "invalid",
                            "description": "Investigation tool",
                            "status": "error",
                            "open": False,
                            "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                            "output": output,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        already_resolved_error_count += 1
                        if already_resolved_error_count >= 2:
                            # Hard-lock: force the model to call finish_investigation
                            current_tool_choice = {"type": "function", "function": {"name": "finish_investigation"}}
                        continue
                    output = _tool_blocked_error_json(
                        name,
                        allowed_tools=sorted(allowed_tool_names),
                    )
                    yield start_event(call_id, registry.event_type(name), {
                        "name": name or "invalid",
                        "description": "Investigation tool",
                        "status": "error",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                    messages.append({"role": "user", "content": (
                        "The tool was blocked by the current investigation state. "
                        "Choose one of the allowed tools from the tool result; do not retry "
                        "the blocked discovery call with the same arguments."
                    )})
                    continue
                if name == "resolve_unknowns":
                    arguments = _resolve_unknown_arguments(arguments)
                    _require_control_reason(arguments, name)
                    resolutions = _resolutions(arguments.get("resolutions"))
                    if not resolutions:
                        raise ValueError("resolve_unknowns requires at least one valid resolution")
                    resolutions = _canonicalize_resolution_unknown_ids(
                        resolutions,
                        _analysis_with_recorded_unknowns(
                            analysis,
                            recorded_findings,
                        ),
                    )
                    _validate_resolution_refs(
                        resolutions,
                        _beliefs(recorded_findings.get("beliefs")),
                        observations,
                    )
                    recorded_findings = _merge_recorded_findings(
                        recorded_findings,
                        {"resolutions": resolutions},
                    )
                    recorded_findings = _bind_grounding_evidence(
                        recorded_findings,
                        observations,
                    )
                    resolved_observation_ids = {
                        evidence_id
                        for resolution in resolutions
                        for evidence_id in resolution.get("evidence", [])
                    }
                    pending_observation_ids = [
                        item for item in pending_observation_ids
                        if item not in resolved_observation_ids
                    ]
                    duplicate_no_progress_signature = ""
                    duplicate_no_progress_count = 0
                    duplicate_no_progress_total = 0
                    force_synthesis_reason = ""
                    semantic_repair_required_ids = _semantic_repair_resolution_ids(
                        recorded_findings,
                    )
                    task_updates = _investigation_task_updates(
                        None,
                        _initial_unknowns(_analysis_with_recorded_unknowns(analysis, recorded_findings)),
                        resolutions,
                    )
                    output = json.dumps({
                        "resolved": True,
                        "counts": {"resolutions": len(resolutions)},
                        "unknown_ids": [item["unknown_id"] for item in resolutions],
                    }, ensure_ascii=False)
                    yield start_event(call_id, "tool", {
                        "name": name,
                        "description": "Resolve investigation unknowns",
                        "status": "done",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                        "symbol": "R",
                    })
                    if task_updates:
                        yield start_event(f"{call_id}-task-update", "task_update", {
                            "analysis_id": analysis.get("id", ""),
                            "items": task_updates,
                        })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                    continue
                if name == "record_investigation_findings":
                    _require_control_reason(arguments, name)
                    if semantic_repair_required_ids and isinstance(arguments.get("resolutions"), list) and arguments["resolutions"]:
                        _require_repair_resolutions(arguments, semantic_repair_required_ids)
                    if (
                        not pending_observation_ids
                        and not resolution_required_ids
                        and not _has_finding_fields(arguments)
                    ):
                        output = json.dumps({
                            "recorded": False,
                            "code": "nothing_to_record",
                            "next_action": "finish_investigation",
                            "message": "No pending observations or unresolved evidence-backed resolutions are available to record.",
                        }, ensure_ascii=False)
                        yield start_event(call_id, "tool", {
                            "name": name,
                            "description": "Record investigation findings",
                            "status": "done",
                            "open": False,
                            "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                            "output": output,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        already_resolved_error_count += 1
                        if already_resolved_error_count >= 2:
                            # Hard-lock: force the model to call finish_investigation
                            current_tool_choice = {"type": "function", "function": {"name": "finish_investigation"}}
                        continue
                    if (
                        not _has_finding_fields(arguments)
                        or (
                            analysis.get("_canonicalized")
                            and pending_observation_ids
                            and not _record_consumes_observations(arguments, pending_observation_ids)
                        )
                    ):
                        arguments = yield from _record_findings_by_slots(
                            provider=provider,
                            model=model,
                            messages=messages[:-1],
                            pricing_rules=pricing_rules,
                            usage_total=usage_total,
                            run_id=run_id,
                            reason=str(arguments.get("reason") or "").strip(),
                            analysis=analysis,
                            observations=observations,
                            recorded_findings=recorded_findings,
                            pending_observation_ids=pending_observation_ids,
                            required_resolution_ids=resolution_required_ids,
                        )
                    if _empty_discovery_recording(
                        arguments,
                        pending_observation_ids,
                        resolution_required_ids,
                    ):
                        output = json.dumps({
                            "recorded": False,
                            "code": "no_material_findings",
                            "pending_observation_ids": pending_observation_ids,
                            "next_action": "continue_discovery",
                        }, ensure_ascii=False)
                        yield start_event(call_id, "tool", {
                            "name": name,
                            "description": "Record investigation findings",
                            "status": "done",
                            "open": False,
                            "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                            "output": output,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        already_resolved_error_count += 1
                        if already_resolved_error_count >= 2:
                            # Hard-lock: force the model to call finish_investigation
                            current_tool_choice = {"type": "function", "function": {"name": "finish_investigation"}}
                        continue
                    _require_finding_fields(arguments)
                    _reject_empty_repair(arguments, recorded_findings)
                    # 剥离模型提交的 repair 诊断字段：repair_mode/semantic_missing
                    # 只能由 audit 质量门打标。REPAIR 阶段模型会从上下文把上一轮
                    # missing 原样抄进提交的 resolution，不清理则 merge 后
                    # _semantic_repair_resolution_ids 永远判该 unknown 待修，
                    # 即使证据已补齐也无限 REPAIR（U4 类死循环根因）。
                    arguments = _strip_submitted_repair_diagnostics(arguments)
                    recorded_findings = _merge_recorded_findings(recorded_findings, arguments)
                    recorded_findings = _bind_grounding_evidence(
                        recorded_findings,
                        observations,
                    )
                    pending_observation_ids.clear()
                    record_signature = _recorded_findings_signature(recorded_findings)
                    if record_signature == last_record_signature:
                        repeated_record_no_progress += 1
                    else:
                        repeated_record_no_progress = 0
                        duplicate_no_progress_signature = ""
                        duplicate_no_progress_count = 0
                        duplicate_no_progress_total = 0
                        force_synthesis_reason = ""
                        force_discovery_ids = []
                    last_record_signature = record_signature
                    if repeated_record_no_progress:
                        force_discovery_ids = _unknowns_missing_project_evidence(
                            recorded_findings,
                            observations,
                            analysis,
                        )
                    if repeated_record_no_progress >= MAX_REPEATED_RECORD_NO_PROGRESS:
                        finalization_reason = (
                            "Runtime stopped after repeated record_investigation_findings "
                            "calls produced no semantic progress."
                        )
                        stop_investigation = True
                    task_updates = _record_task_updates(recorded_findings)
                    output = json.dumps({
                        "recorded": True,
                        "counts": {field: len(recorded_findings.get(field, [])) for field in FINDING_FIELDS},
                        **({"stalled": True} if stop_investigation else {}),
                    }, ensure_ascii=False)
                    yield start_event(call_id, "tool", {
                        "name": name,
                        "description": "Record investigation findings",
                        "status": "done",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                    })
                    if task_updates:
                        yield start_event(f"{call_id}-task-update", "task_update", {
                            "analysis_id": analysis.get("id", ""),
                            "items": task_updates,
                        })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                    if stop_investigation:
                        yield start_event(f"{run_id}-safety-record-no-progress", "safety_stop", {
                            "reason": "record_no_progress",
                            "message": finalization_reason,
                            "tool": name,
                        })
                        break
                    continue
                if name == "finish_investigation":
                    _require_control_reason(arguments, name)
                    recorded_findings = _apply_direct_resolution_gate(
                        recorded_findings,
                        observations,
                        strict_grounding=semantic_gate_enabled and _analysis_requests_implementation(analysis),
                    )
                    semantic_repair_required_ids = (
                        _semantic_repair_resolution_ids(recorded_findings)
                        if semantic_gate_enabled
                        else set()
                    )
                    if (
                        semantic_gate_enabled
                        and
                        analysis.get("_canonicalized")
                        and not _audit_covers_resolutions(
                            last_quality_audit,
                            recorded_findings,
                            analysis,
                        )
                    ):
                        last_quality_audit = yield from _audit_recorded_findings(
                            provider=provider,
                            model=model,
                            messages=[
                                item for item in messages[:-1]
                                if item.get("role") != "system"
                            ],
                            pricing_rules=pricing_rules,
                            usage_total=usage_total,
                            run_id=run_id,
                            analysis=analysis,
                            observations=observations,
                            recorded_findings=recorded_findings,
                            audit_cache=audit_cache,
                        )
                        recorded_findings, requests, questions = _apply_investigation_audit(
                            recorded_findings,
                            last_quality_audit,
                            observations=observations,
                            strict_grounding=_analysis_requests_implementation(analysis),
                            allow_verification=subagent_enabled and _analysis_requests_implementation(analysis),
                            analysis=analysis,
                        )
                        semantic_repair_required_ids = _semantic_repair_resolution_ids(
                            recorded_findings,
                        )
                        attempted = attempted_verifications | {
                            (item.get("unknown_id"), item.get("hypothesis"))
                            for item in verification_queue
                        }
                        verification_queue.extend(
                            item for item in requests
                            if (item.get("unknown_id"), item.get("hypothesis")) not in attempted
                            and _unknown_blocks_finish(item.get("unknown_id"), analysis, recorded_findings)
                        )
                        clearify_questions.update({
                            unknown_id: question
                            for unknown_id, question in questions.items()
                            if _unknown_blocks_finish(unknown_id, analysis, recorded_findings)
                        })
                    pending_resolution_statuses = {
                        str(item.get("status") or "")
                        for item in recorded_findings.get("resolutions", [])
                        if isinstance(item, dict)
                        and _unknown_blocks_finish(item.get("unknown_id"), analysis, recorded_findings)
                    }
                    if (
                        verification_queue
                        or clearify_questions
                        or pending_resolution_statuses & {"partially_resolved", "needs_clearify"}
                    ):
                        repair_payload = _semantic_repair_payload(
                            recorded_findings,
                            _semantic_repair_resolution_ids(recorded_findings),
                        )
                        output = json.dumps({
                            "finished": False,
                            "reason": "semantic_quality_gate",
                            "repair": repair_payload,
                            "next_action": (
                                "clearify"
                                if "needs_clearify" in pending_resolution_statuses or clearify_questions
                                else "verify_hypothesis"
                                if verification_queue
                                else "continue_investigation"
                            ),
                        }, ensure_ascii=False)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        messages.append({"role": "user", "content": (
                            "The semantic quality gate did not authorize every resolution. "
                            "Follow its next action; do not finish or reuse the rejected conclusion."
                        )})
                        break
                    final = _finish_payload(
                        _finish_arguments(
                            recorded_findings,
                            arguments,
                            prefer_finish_summary=not _analysis_requests_implementation(analysis),
                        ),
                        analysis=analysis,
                        observations=observations,
                        repair_conflicts=True,
                        workspace_dir=workspace_dir,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(final, ensure_ascii=False),
                    })
                    if final.get("resolution_repair"):
                        finalization_reason = "Investigation findings need explicit resolutions before finalizing."
                        messages.append({
                            "role": "user",
                            "content": _resolution_repair_prompt(final["resolution_repair"]),
                        })
                        final = None
                        stop_investigation = True
                    break
                if name == "clearify":
                    if clearify_unknown:
                        arguments["target_unknown_ids"] = [clearify_unknown["id"]]
                        arguments.setdefault("reason", "Resolve the blocking product decision.")
                        arguments.setdefault("question", clearify_unknown["question"])
                    target_ids = _target_unknown_ids(arguments)
                    target_ids = [
                        item for item in target_ids
                        if not any(_same_unknown_id(item, asked_id) for asked_id in asked_clearify_ids)
                    ]
                    arguments["target_unknown_ids"] = target_ids
                    if not target_ids:
                        output = json.dumps({
                            "skipped": True,
                            "reason": "These clearify unknowns were already asked in the current round.",
                        }, ensure_ascii=False)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        already_resolved_error_count += 1
                        if already_resolved_error_count >= 2:
                            # Hard-lock: force the model to call finish_investigation
                            current_tool_choice = {"type": "function", "function": {"name": "finish_investigation"}}
                        continue
                    _validate_tool_contract(
                        name,
                        target_unknown_ids=target_ids,
                        reason=str(arguments.get("reason") or "").strip(),
                        orientation=bool(arguments.get("orientation", False)),
                        analysis=_analysis_with_recorded_unknowns(
                            analysis,
                            recorded_findings,
                        ),
                    )
                    answered_by_previous_round = {
                        str(item.get("unknown_id") or "").strip()
                        for item in recorded_findings.get("resolutions", [])
                        if isinstance(item, dict)
                        and (
                            item.get("reason") in (CLEARIFY_RESOLUTION_REASON, CLEARIFY_UNRESOLVED_REASON)
                            or (
                                str(item.get("status") or "") in ("resolved", "partially_resolved")
                                and str(item.get("answer") or "").strip()
                            )
                        )
                    }
                    target_ids = [
                        item for item in target_ids
                        if not any(
                            _same_unknown_id(item, answered_id)
                            for answered_id in answered_by_previous_round
                        )
                    ]
                    arguments["target_unknown_ids"] = target_ids
                    if not target_ids:
                        output = json.dumps({
                            "skipped": True,
                            "reason": "These clearify unknowns already have authoritative user answers.",
                        }, ensure_ascii=False)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })
                        already_resolved_error_count += 1
                        if already_resolved_error_count >= 2:
                            # Hard-lock: force the model to call finish_investigation
                            current_tool_choice = {"type": "function", "function": {"name": "finish_investigation"}}
                        continue
                    question_id = clearify_runtime.create_pending()
                    yield start_event(question_id, "user_question", _clearify_question(
                        arguments,
                        question_id=question_id,
                        analysis=analysis,
                    ))
                    answer = clearify_runtime.wait(question_id)
                    output = _clearify_tool_result(answer)
                    resolution_records = _clearify_resolution_records(arguments, answer)
                    repeated_tool_error_name = ""
                    repeated_tool_error_count = 0
                    if resolution_records:
                        for resolution in resolution_records:
                            asked_clearify_ids.add(resolution["unknown_id"])
                            for question_id in list(clearify_questions):
                                if _same_unknown_id(question_id, resolution["unknown_id"]):
                                    clearify_questions.pop(question_id, None)
                        recorded_findings = _merge_recorded_findings(
                            recorded_findings,
                            {"resolutions": resolution_records},
                        )
                    yield start_event(call_id, "tool", {
                        "name": name,
                        "description": "Ask the user for clarification",
                        "status": "done",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                    })
                    if resolution_records:
                        yield start_event(f"{call_id}-task-update", "task_update", {
                            "analysis_id": analysis.get("id", ""),
                            "items": _investigation_task_updates(
                                None,
                                _initial_unknowns(_analysis_with_recorded_unknowns(analysis, recorded_findings)),
                                resolution_records,
                            ),
                        })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                    continue
                cache_key = _tool_cache_key(name, arguments)
                if cache_key in tool_cache:
                    cached_observation_id = tool_cache_observation_ids.get(cache_key, "")
                    duplicate_no_progress_total += 1
                    if duplicate_no_progress_signature == cache_key:
                        duplicate_no_progress_count += 1
                    else:
                        duplicate_no_progress_signature = cache_key
                        duplicate_no_progress_count = 1
                    next_action = (
                        "resolve_unknowns"
                        if pending_observation_ids or cached_observation_id
                        else "choose_different_evidence"
                    )
                    output = _duplicate_no_progress_json(
                        name,
                        duplicate_count=duplicate_no_progress_total,
                        cached_observation_id=cached_observation_id,
                        required_next_action=next_action,
                    )
                    if duplicate_no_progress_total >= MAX_DUPLICATE_NO_PROGRESS:
                        force_synthesis_reason = _duplicate_no_progress_prompt(
                            name,
                            cached_observation_id,
                            pending_observation_ids,
                        )
                    yield start_event(call_id, registry.event_type(name), {
                        "name": name,
                        "description": "Investigation tool",
                        "status": "no_progress",
                        "open": False,
                        "input": json.dumps(arguments, ensure_ascii=False, indent=2),
                        "output": output,
                        "deduplicated": True,
                        "cached_observation_id": cached_observation_id,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                    if duplicate_no_progress_total >= MAX_REPEATED_TOOL_ERRORS:
                        finalization_reason = (
                            "Runtime stopped after repeated duplicate no-progress tool calls: "
                            f"{name or 'invalid'}."
                        )
                        stop_investigation = True
                        yield start_event(f"{run_id}-safety-duplicate-no-progress", "safety_stop", {
                            "reason": "duplicate_no_progress",
                            "message": finalization_reason,
                            "tool": name or "invalid",
                            "cached_observation_id": cached_observation_id,
                        })
                        break
                    continue
                output = yield from _run_tool_stream(
                    name,
                    call_id,
                    arguments,
                    workspace_dir,
                    _analysis_with_recorded_unknowns(
                        analysis,
                        recorded_findings,
                    ),
                    relax_discovery_contract=(
                        current_phase == InvestigationPhase.REPAIR
                        and name in _REPAIR_ALLOWED_TOOL_NAMES - {"record_investigation_findings"}
                    ),
                )
                repeated_tool_error_name = ""
                repeated_tool_error_count = 0
                tool_cache[cache_key] = output
                observation = _tool_observation(name, call_id, output)
                observations.append(observation)
                tool_cache_observation_ids[cache_key] = observation["id"]
                pending_observation_ids.append(observation["id"])
                duplicate_no_progress_signature = ""
                duplicate_no_progress_count = 0
                duplicate_no_progress_total = 0
                force_synthesis_reason = ""
                force_discovery_ids = []
                if verification_request and _is_hypothesis_verifier_call(name, arguments):
                    attempted_verifications.add((
                        verification_request["unknown_id"],
                        verification_request["hypothesis"],
                    ))
                    verification_queue.pop(0)
            except Exception as exc:
                if (
                    name == "finish_investigation"
                    and isinstance(exc, ValueError)
                    and ("references file" in str(exc) or "claims behavior" in str(exc))
                ):
                    finish_evidence_blocked = True
                raw_arguments = function.get("arguments") or "{}"
                partial_arguments = _partial_tool_arguments(raw_arguments)
                if name == "record_investigation_findings":
                    partial_arguments = _record_arguments(partial_arguments)
                if name == "resolve_unknowns":
                    partial_arguments = _resolve_unknown_arguments(partial_arguments)
                    salvaged_resolutions = _salvage_resolution_candidates(
                        partial_arguments,
                        observations,
                    )
                    if salvaged_resolutions:
                        recorded_findings = _merge_recorded_findings(
                            recorded_findings,
                            {"resolutions": salvaged_resolutions},
                        )
                        recorded_findings = _bind_grounding_evidence(
                            recorded_findings,
                            observations,
                        )
                if name == "record_investigation_findings" and _has_finding_fields(partial_arguments):
                    recorded_findings = _merge_recorded_findings(recorded_findings, partial_arguments)
                    pending_observation_ids.clear()
                    last_quality_audit = {}
                output = _tool_repair_error_json(
                    exc,
                    name,
                    raw_arguments,
                    partial_arguments,
                    observations=observations,
                )
                error_name = name or "invalid"
                failed_tool_cache[_tool_cache_key(
                    error_name,
                    arguments or partial_arguments,
                )] = output
                if error_name not in round_error_names:
                    if error_name == repeated_tool_error_name:
                        repeated_tool_error_count += 1
                    else:
                        repeated_tool_error_name = error_name
                        repeated_tool_error_count = 1
                    round_error_names.add(error_name)
                yield start_event(call_id, registry.event_type(name), {
                    "name": name or "invalid",
                    "description": "Investigation tool",
                    "status": "error",
                    "open": False,
                    "input": raw_arguments,
                    "output": output,
                })
                if repeated_tool_error_count >= MAX_REPEATED_TOOL_ERRORS:
                    finalization_reason = (
                        "Runtime recovered after repeated tool argument errors: "
                        f"{name or 'invalid'} failed with {exc}."
                    )
                    if name == "record_investigation_findings":
                        final = _runtime_recovered_investigation(
                            finalization_reason,
                            analysis,
                            observations,
                            recorded_findings,
                        )
                    stop_investigation = True
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": output,
            })
            if stop_investigation:
                yield start_event(f"{run_id}-safety-repeated-tool-error", "safety_stop", {
                    "reason": "repeated_tool_error",
                    "message": finalization_reason,
                    "tool": name or "invalid",
                    "visibility": "diagnostic",
                })
                break
        if final is not None:
            break
        if stop_investigation:
            break
    else:
        finalization_reason = "Investigation step limit reached. Summarizing observed facts."

    if final is None and stop_investigation:
        final = _runtime_recovered_investigation(
            finalization_reason,
            analysis,
            observations,
            recorded_findings,
        )
    elif final is None:
        final = yield from _finalize_investigation(
            provider=provider,
            model=model,
            messages=messages,
            pricing_rules=pricing_rules,
            usage_total=usage_total,
            run_id=run_id,
            analysis=analysis,
            observations=observations,
            recorded_findings=recorded_findings,
            audit_cache=audit_cache,
            reason=finalization_reason,
            workspace_dir=workspace_dir,
        )
    if last_quality_audit:
        final["quality_audit"] = last_quality_audit
    final["observations"] = _final_observations(
        observations + [
            item for item in final.get("observations", [])
            if isinstance(item, dict)
        ],
        preserve_grounding_evidence=preserve_grounding_evidence,
    )

    implementation_intent = _analysis_requests_implementation(analysis)
    yield {"op": "update", "id": stage_id, "patch": {
        "state": "done",
        "phase": "patch_planning_ready" if final.get("ready_for_patch_planning") and implementation_intent else "done",
    }}
    step = _step_result(final, implementation_intent=implementation_intent)
    final["step_result"] = step
    yield start_event(f"{run_id}-step-result", "step_result", step)
    if final.get("task_updates"):
        yield start_event(f"{run_id}-task-update", "task_update", {
            "analysis_id": analysis.get("id", ""),
            "items": final["task_updates"],
        })
    yield start_event(f"{run_id}-output", "output", {
        "content": _summary(final),
        "streaming": False,
        "visibility": "diagnostic" if final.get("runtime_recovered") else "default",
    })
    yield {"op": "done", "investigation": final}


def _investigation_tools() -> list[dict]:
    tools = [
        _investigation_tool_schema(tool.name, tool.description, tool.parameters)
        for tool in registry.list_for_capability(INVESTIGATION_CAPABILITY)
    ]
    tools.append(_resolve_unknowns_tool_schema())
    tools.append(_record_findings_tool_schema())
    tools.append(_finish_tool_schema())
    return tools


def _round_indexes(limit: int, start: int = 0):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


def _blocking_investigable_count(analysis: dict | None) -> int:
    """初始任务契约中需调查的 blocking unknown 数（调查深度基准）。"""
    if not isinstance(analysis, dict):
        return 0
    return sum(
        1
        for item in _initial_unknowns(analysis)
        if item.get("resolution_strategy") == "investigate_project" and item.get("blocking")
    )


def _minimum_investigation_rounds(analysis: dict | None, rounds_per_unknown: int) -> int:
    """方案 A：最少调查轮数 = rounds_per_unknown × blocking unknown 数。"""
    return _blocking_investigable_count(analysis) * max(1, rounds_per_unknown)


def _minimum_rounds_prompt(remaining_rounds: int) -> str:
    return (
        f"Investigation budget: at least {remaining_rounds} more round(s) remain "
        "before you may finish. Keep gathering evidence: cross-check every task "
        "unknown against project observations, verify each resolution, and confirm "
        "the acceptance criteria are grounded. Do not finish yet."
    )


def _tool_call_summary(tool_calls: list[dict]) -> str:
    items = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        name = function.get("name") or "tool"
        try:
            arguments = _tool_arguments(function.get("arguments"))
        except ValueError:
            arguments = {}
        reason = str(arguments.get("reason") or arguments.get("operation_summary") or "").strip()
        targets = arguments.get("target_unknown_ids") if isinstance(arguments.get("target_unknown_ids"), list) else []
        subject = _tool_call_subject(name, arguments)
        line = f"{name}{subject}"
        if targets:
            line += f" for {', '.join(str(item) for item in targets if str(item).strip())}"
        if reason:
            line += f": {reason}"
        items.append(line)
    if not items:
        return ""
    return "Calling tools:\n" + "\n".join(f"- {item}" for item in items)


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


def _duplicate_no_progress_json(
    tool_name: str,
    *,
    duplicate_count: int,
    cached_observation_id: str,
    required_next_action: str,
) -> str:
    return json.dumps({
        "code": "duplicate_no_progress",
        "tool": tool_name or "invalid",
        "cached_observation_id": cached_observation_id,
        "duplicate_count": duplicate_count,
        "retryable": False,
        "required_next_action": required_next_action,
        "message": (
            "The same successful tool arguments were already observed. "
            "This call produced no new investigation progress."
        ),
    }, ensure_ascii=False)


def _duplicate_no_progress_prompt(
    tool_name: str,
    cached_observation_id: str,
    pending_observation_ids: list[str],
) -> str:
    ids = _dedupe_strings([cached_observation_id, *pending_observation_ids])
    return (
        "The investigation repeated already-observed discovery tool calls without "
        f"new progress: {tool_name or 'invalid'}. Do not call those discovery actions again. "
        "Use resolve_unknowns if the cached observation answers a blocking unknown, "
        "record_investigation_findings if it contains material findings, or "
        "finish_investigation if investigation is already sufficient. "
        f"Relevant observation ids: {', '.join(ids) if ids else 'none'}."
    )


def _tool_blocked_error_json(tool_name: str, *, allowed_tools: list[str]) -> str:
    required_action = allowed_tools[0] if len(allowed_tools) == 1 else "choose_allowed_tool"
    return json.dumps({
        "error": {
            "code": "tool_blocked_by_investigation_state",
            "tool": tool_name or "invalid",
            "retryable": False,
            "blocked_tool": tool_name or "invalid",
            "allowed_tools": allowed_tools,
            "required_action": required_action,
            "message": (
                "This tool is not available in the current investigation state. "
                "Use an allowed control tool or wait until discovery is available again."
            ),
        },
    }, ensure_ascii=False)


def _tool_repair_error_json(
    exc: Exception,
    tool_name: str,
    raw_arguments: str,
    partial_arguments: dict,
    attempt: int = 0,
    *,
    observations: list[dict] | None = None,
) -> str:
    try:
        payload = json.loads(tool_error_json(exc, tool_name))
    except json.JSONDecodeError:
        payload = {"error": {"message": str(exc), "tool": tool_name or "invalid"}}
    error = payload.setdefault("error", {})
    error["partial_arguments"] = partial_arguments
    error["attempt"] = attempt
    error["missing_fields"] = _missing_fields_from_error(
        str(exc),
        partial_arguments,
    )
    if "unknown evidence ids" in str(exc) and observations:
        error["valid_observation_refs"] = _observation_reference_payload(observations)
        error["repair_instruction"] = (
            "Reuse partial_arguments. Replace invalid evidence references with "
            "resolution.observation_ids using one of valid_observation_refs.ref; "
            "do not repeat discovery only to repair ids."
        )
    if tool_name == "finish_investigation" and "bugfix_readiness" in str(exc):
        error["required_argument_shape"] = {
            "bugfix_readiness": {
                "failure_reproduced_or_observed": True,
                "root_cause_or_failing_boundary_identified": True,
                "patch_target_identified": True,
                "expected_behavior_change_defined": True,
                "validation_scenario_defined": True,
                "reason": "Evidence-backed reason for each readiness field.",
            }
        }
    if tool_name == "clearify" and "requires product_decision or engineering_decision targets" in str(exc):
        error["retryable"] = False
        error["required_action"] = "resolve_or_investigate_contract_unknown"
        error["repair_instruction"] = (
            "Do not retry clearify for non-decision unknowns. "
            "Resolve the contract unknown from project evidence or continue discovery."
        )
    else:
        error["repair_instruction"] = (
            "Reuse partial_arguments. Return only the same tool call with missing/invalid fields corrected; "
            "do not restart discovery or repeat the identical arguments."
        )
    return json.dumps(payload, ensure_ascii=False)


def _missing_fields_from_error(
    message: str,
    partial_arguments: dict | None = None,
) -> list[str]:
    fields = []
    lowered = message.casefold()
    partial_arguments = partial_arguments or {}
    for field in (
        "reason",
        "target_unknown_ids",
        "summary",
        "recommended_next_step",
        "bugfix_readiness",
        *DISCOVERY_CONTRACT_FIELDS,
    ):
        if field in lowered and not partial_arguments.get(field):
            fields.append(field)
    return fields


def _partial_tool_arguments(raw: str | None) -> dict:
    text = (raw or "{}").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return _partial_json_object(text)


def _partial_json_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    result = {}
    index = _skip_ws(text, 0)
    if index >= len(text) or text[index] != "{":
        return result
    index += 1
    while True:
        index = _skip_ws(text, index)
        if index >= len(text) or text[index] == "}":
            return result
        try:
            key, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            return result
        if not isinstance(key, str):
            return result
        index = _skip_ws(text, index)
        if index >= len(text) or text[index] != ":":
            return result
        index = _skip_ws(text, index + 1)
        try:
            value, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            return result
        result[key] = value
        index = _skip_ws(text, index)
        if index >= len(text) or text[index] == "}":
            return result
        if text[index] != ",":
            return result
        index += 1


def _skip_ws(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _has_finding_fields(arguments: dict) -> bool:
    return any(isinstance(arguments.get(field), list) and arguments.get(field) for field in FINDING_FIELDS)


def _nothing_to_record_result(next_action: str = "finish_investigation") -> dict:
    return {
        "recorded": False,
        "code": "nothing_to_record",
        "next_action": next_action,
    }


def _record_consumes_observations(arguments: dict, observation_ids: list[str]) -> bool:
    pending = {str(item).strip() for item in observation_ids if str(item).strip()}
    if not pending:
        return False
    for field in ("beliefs", "resolutions"):
        for item in arguments.get(field, []):
            if isinstance(item, dict) and pending.intersection(_reference_list(item.get("evidence"))):
                return True
    return False


def _require_finding_fields(arguments: dict) -> None:
    if not _has_finding_fields(arguments):
        raise ValueError(
            "record_investigation_findings must include at least one non-empty findings array; canonical arrays are "
            "beliefs, resolutions, unknowns, new_unknowns, user_decisions_required, or task_updates"
        )


def _resolve_unknown_arguments(arguments: dict) -> dict:
    normalized = dict(arguments)
    normalized["resolutions"] = _resolutions(normalized.get("resolutions"))
    return normalized


def _resolution_kind(raw: dict, status: str) -> str:
    value = str(raw.get("kind") or "").strip()
    if value in RESOLUTION_KINDS:
        return value
    if status == "deferred":
        return "deferred"
    if status == "needs_clearify":
        return "user_decision"
    return "derived_inference"


def _record_arguments(arguments: dict) -> dict:
    normalized = dict(arguments)
    if not (isinstance(normalized.get("beliefs"), list) and normalized["beliefs"]):
        beliefs = _alias_beliefs(normalized.get("findings")) or _alias_beliefs(normalized.get("evidence_summaries"))
        if beliefs:
            normalized["beliefs"] = beliefs
    if isinstance(normalized.get("new_unknowns"), list):
        normalized["new_unknowns"] = [
            {
                **item,
                "question": item.get("question") or item.get("summary"),
                "resolution_strategy": item.get("resolution_strategy") or item.get("strategy"),
            }
            for item in normalized["new_unknowns"]
            if isinstance(item, dict)
        ]
    return normalized


def _alias_beliefs(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for raw in value:
        if isinstance(raw, str):
            statement = raw.strip()
            evidence = []
            status = "supported"
        elif isinstance(raw, dict):
            statement = _belief_text(raw) or _alias_statement(raw)
            evidence = _reference_list(raw.get("evidence") or raw.get("source") or raw.get("sources"))
            status = _belief_status(raw, default="supported")
        else:
            continue
        if statement:
            item = {"statement": statement, "status": status, "evidence": evidence}
            if isinstance(raw, dict) and str(raw.get("id") or "").strip():
                item["id"] = str(raw["id"]).strip()
            items.append(item)
    return items


def _alias_statement(raw: dict) -> str:
    label = str(raw.get("label") or raw.get("title") or "").strip()
    evidence = str(raw.get("evidence") or raw.get("source") or "").strip()
    if label and evidence:
        return f"{label}: {evidence}"
    return label or evidence


def _is_hypothesis_verifier_call(name: str, arguments: dict) -> bool:
    if name != "subagent":
        return False
    agent = str(arguments.get("agent") or arguments.get("name") or "")
    return agent.strip().removeprefix("@").casefold() == "hypothesis-verifier"


def _analysis_is_read_only(analysis: dict | None) -> bool:
    return str((analysis or {}).get("execution_mode") or "").strip().casefold() == "read_only"


def _recorded_resolves_initial_unknowns(
    recorded: dict,
    analysis: dict | None,
    *,
    repair_ids: set[str] | None = None,
) -> bool:
    """判断 recorded resolutions 是否已覆盖全部初始 unknown。

    repair_ids（语义门禁待修列表）中的 unknown 即使 resolution
    status 被提交为 resolved，也不计入"已解决"——因为语义门禁
    尚未接受它们。这是 d5eef05a 死锁的修复点：旧实现不知道
    repair 存在，repair 分支与 already_resolved 分支靠 if/elif
    顺序"碰巧"互斥；现在由调用方（_investigation_directive /
    工具拦截）显式注入 repair_ids，两个判定结构上互斥。
    """
    initial = [item for item in (analysis or {}).get("unknowns", []) if isinstance(item, dict) and item.get("id")]
    if not initial:
        return False
    required_ids = [str(item["id"]) for item in initial]
    if not _analysis_is_read_only(analysis):
        required_ids.extend(
            str(item["id"])
            for item in (
                _unknowns(recorded.get("unknowns"))
                + _unknowns(recorded.get("new_unknowns"))
            )
            if item.get("blocking")
        )
    if repair_ids:
        # 语义门禁还有待修 unknown 时，一律不算"全部已解决"——
        # 即使其余 unknown 的 resolution status 是 resolved。
        # 这样工具拦截处不会在 REPAIR 阶段误报 investigation_already_resolved，
        # 模型被引导补证据而不是错误地去 finish（d5eef05a 根因）。
        return False
    resolved = [
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") in {"resolved", "deferred"}
        and not _clearify_resolution_lacks_evidence(item)
    ]
    return all(any(_same_unknown_id(required_id, item) for item in resolved) for required_id in required_ids)


def _clearify_resolution_lacks_evidence(item: dict) -> bool:
    """clearify 的用户答案若没有文件证据，不算完全解决。

    用户决定（如"返回复数"）只是产品决策，模型还必须实际调查代码、
    把修改目标位置写进 evidence——否则后续 patch_planning 无从下手
    （也杜绝了 answers 里"模型自解析 vs 用户决定"并存导致的重复询问）。"""
    if str(item.get("reason") or "") != CLEARIFY_RESOLUTION_REASON:
        return False
    return not _reference_list(item.get("evidence"))


def _clearify_pending_evidence_unknowns(recorded: dict) -> list[dict]:
    """clearify 已答但还没有代码证据的 resolution。

    用户决定只是产品决策，模型必须 read/grep 定位实现位置并写进 evidence，
    才算真正完成该 unknown 的调查。"""
    return [
        item
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("reason") or "") == CLEARIFY_RESOLUTION_REASON
        and not _reference_list(item.get("evidence"))
    ]


def _pending_clearify_unknown(
    recorded: dict,
    analysis: dict | None,
    audit_questions: dict[str, str] | None = None,
) -> dict | None:
    completed = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict) and str(item.get("status") or "") in {"resolved", "deferred"}
    }
    delegated = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") == "partially_resolved"
        and item.get("reason") == CLEARIFY_UNRESOLVED_REASON
    }
    needs_clearify = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict) and str(item.get("status") or "") == "needs_clearify"
    }
    candidates = (
        _initial_unknowns(analysis)
        + _unknowns(recorded.get("new_unknowns"))
    )
    for item in candidates:
        if (
            item.get("blocking")
            and item.get("type") in ("product_decision", "engineering_decision")
            and not any(_same_unknown_id(item["id"], completed_id) for completed_id in completed)
            and not any(_same_unknown_id(item["id"], delegated_id) for delegated_id in delegated)
            and (
                item.get("resolution_strategy") == "clearify"
                or any(_same_unknown_id(item["id"], pending_id) for pending_id in needs_clearify)
                or item.get("type") == "engineering_decision"
            )
        ):
            result = dict(item)
            question = next(
                (
                    question
                    for unknown_id, question in (audit_questions or {}).items()
                    if _same_unknown_id(unknown_id, item["id"])
                ),
                "",
            )
            if question:
                result["question"] = question
            return result
    return None


def _unknown_blocks_finish(unknown_id: str | None, analysis: dict | None, recorded: dict) -> bool:
    candidates = (
        _initial_unknowns(analysis)
        + _unknowns(recorded.get("unknowns"))
        + _unknowns(recorded.get("new_unknowns"))
    )
    source = _find_by_unknown_id(candidates, unknown_id, id_field="id")
    return bool(source.get("blocking", True)) if source else True


def _analysis_with_recorded_unknowns(analysis: dict, recorded: dict) -> dict:
    merged = {
        **analysis,
        "unknowns": _merge_unknowns(
            _initial_unknowns(analysis)
            + _unknowns(recorded.get("unknowns"))
            + _unknowns(recorded.get("new_unknowns"))
        ),
    }
    # 已 resolve 的 unknown 会被 _open_analysis_unknowns 从 unknowns 移除，
    # 但它们仍是任务契约的一部分——保留 resolutions 让校验层能识别（模型
    # 可能继续为已解决的 unknown 补充证据，这不应当作"不在契约中"拒绝）。
    recorded_resolutions = recorded.get("resolutions") if isinstance(recorded, dict) else None
    if recorded_resolutions:
        merged["resolutions"] = _resolutions(analysis.get("resolutions")) + _resolutions(recorded_resolutions)
    return merged


def _clearify_required_prompt(unknown: dict) -> str:
    return "\n".join([
        "A blocking product decision requires the user's answer now.",
        f"Unknown id: {unknown['id']}",
        f"Question: {unknown['question']}",
        "Call clearify with this target_unknown_id and exactly three concrete candidate answers.",
        "Do not call discovery or finish tools before the user answers.",
    ])


def _unknowns_needing_resolution(recorded: dict, observations: list[dict], analysis: dict | None) -> list[str]:
    initial = [
        item for item in (analysis or {}).get("unknowns", [])
        if isinstance(item, dict)
        and item.get("blocking")
        and item.get("resolution_strategy") == "investigate_project"
        and str(item.get("id") or "").strip()
    ]
    if not initial:
        return []
    accounted = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict) and str(item.get("unknown_id") or "").strip()
    }
    supported = _supported_unknown_ids(recorded, observations)
    return [
        str(item["id"])
        for item in initial
        if not any(_same_unknown_id(item["id"], known_id) for known_id in accounted)
        and any(_same_unknown_id(item["id"], supported_id) for supported_id in supported)
    ]


def _unknowns_missing_project_evidence(
    recorded: dict,
    observations: list[dict],
    analysis: dict | None,
) -> list[str]:
    initial = [
        item for item in (analysis or {}).get("unknowns", [])
        if isinstance(item, dict)
        and item.get("blocking")
        and item.get("resolution_strategy") == "investigate_project"
        and str(item.get("id") or "").strip()
    ]
    if not initial:
        return []
    completed = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") in {"resolved", "deferred"}
        and str(item.get("unknown_id") or "").strip()
    }
    supported = _supported_unknown_ids(recorded, observations)
    return [
        str(item["id"])
        for item in initial
        if not any(_same_unknown_id(item["id"], done_id) for done_id in completed)
        and not any(_same_unknown_id(item["id"], supported_id) for supported_id in supported)
    ]


def _pending_observation_unknown_ids(
    observations: list[dict],
    pending_observation_ids: list[str],
    analysis: dict | None,
    recorded: dict,
) -> list[str]:
    pending = {str(item).strip() for item in pending_observation_ids if str(item).strip()}
    if not pending:
        return []
    candidates = [
        item for item in (analysis or {}).get("unknowns", [])
        if isinstance(item, dict)
        and item.get("blocking")
        and item.get("resolution_strategy") == "investigate_project"
        and str(item.get("id") or "").strip()
    ]
    completed = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") in {"resolved", "deferred"}
        and str(item.get("unknown_id") or "").strip()
    }
    target_ids = {
        _normalize_unknown_id(target)
        for observation in observations
        if isinstance(observation, dict)
        and str(observation.get("id") or "").strip() in pending
        and _positive_project_observation(observation)
        for target in observation.get("target_unknown_ids", [])
    }
    return [
        str(item["id"])
        for item in candidates
        if any(_same_unknown_id(item["id"], target_id) for target_id in target_ids)
        and not any(_same_unknown_id(item["id"], done_id) for done_id in completed)
    ]


def _supported_unknown_ids(recorded: dict, observations: list[dict]) -> set[str]:
    observations_by_id = {
        str(item.get("id") or "").strip(): item
        for item in observations
        if isinstance(item, dict) and _positive_project_observation(item)
    }
    by_unknown: dict[str, list[dict]] = {}
    for observation in observations_by_id.values():
        for unknown_id in observation.get("target_unknown_ids", []):
            by_unknown.setdefault(_normalize_unknown_id(unknown_id), []).append(observation)
    supported = set()
    for raw in recorded.get("beliefs", []):
        if not isinstance(raw, dict) or not _supporting_belief(raw):
            continue
        text = _belief_text(raw)
        evidence_refs = _reference_list(raw.get("evidence")) + _reference_list(raw.get("observation_ids"))
        evidence_ids = [item for item in evidence_refs if item in observations_by_id]
        for unknown_id, unknown_observations in by_unknown.items():
            unknown_evidence_ids = {
                str(item.get("id") or "").strip()
                for item in unknown_observations
            }
            if any(evidence_id in unknown_evidence_ids for evidence_id in evidence_ids):
                supported.add(unknown_id)
                continue
            if any(_observation_mentioned(text, observation) for observation in unknown_observations):
                supported.add(unknown_id)
    return supported


def _positive_project_observation(item: dict) -> bool:
    if str(item.get("tool") or "") == "lsp_tool":
        return False
    tool = registry.get(str(item.get("tool") or ""))
    if (
        item.get("tool") not in PROJECT_EVIDENCE_TOOLS
        and (tool is None or PROJECT_EVIDENCE_CAPABILITY not in tool.capabilities)
    ):
        return False
    if not item.get("target_unknown_ids"):
        return False
    return True


def _supporting_belief(item: dict) -> bool:
    if not _belief_text(item):
        return False
    status = str(item.get("status") or "").strip()
    return status in {"", "supported", "strongly_supported", "runtime_confirmed"}


def _belief_text(item: dict) -> str:
    return str(item.get("statement") or item.get("text") or item.get("summary") or item.get("content") or "").strip()


def _observation_mentioned(text: str, observation: dict) -> bool:
    if not text:
        return False
    haystack = text.casefold()
    path = str(observation.get("path") or "").replace("\\", "/")
    title = str(observation.get("title") or "")
    names = [path, Path(path).name if path else "", title]
    return any(name and name.casefold() in haystack for name in names)


def _resolution_required_prompt(
    unknown_ids: list[str],
    recorded: dict,
    observations: list[dict],
    analysis: dict | None,
) -> str:
    questions = {
        str(item.get("id") or ""): str(item.get("question") or "").strip()
        for item in (analysis or {}).get("unknowns", [])
        if isinstance(item, dict)
    }
    return "\n".join([
        "Existing project evidence is sufficient to write an explicit resolution.",
        "Do not call more discovery tools for these unknowns.",
        "Call resolve_unknowns with resolutions for: " + ", ".join(unknown_ids),
        "Each resolution must include unknown_id, status, answer, observation_ids or belief_ids, and reason.",
        "If an unknown is still not fully resolved, record a partially_resolved resolution naming the precise missing evidence.",
        "Questions:",
        *[f"- {unknown_id}: {questions.get(unknown_id, '')}" for unknown_id in unknown_ids],
        "Supported project evidence:",
        *_resolution_evidence_lines(unknown_ids, recorded, observations),
    ])


def _resolution_evidence_lines(unknown_ids: list[str], recorded: dict, observations: list[dict]) -> list[str]:
    wanted = set(unknown_ids)
    lines = []
    ref_by_id = _observation_ref_by_id(observations)
    for observation in observations:
        targets = {_normalize_unknown_id(item) for item in observation.get("target_unknown_ids", [])}
        if targets & wanted and _positive_project_observation(observation):
            observation_id = str(observation.get("id") or "").strip()
            ref = ref_by_id.get(observation_id, observation_id)
            lines.append(
                f"- observation ref {ref} (id {observation_id}): "
                f"{observation.get('title') or observation.get('summary')}"
            )
    for belief in recorded.get("beliefs", []):
        if isinstance(belief, dict) and _supporting_belief(belief):
            lines.append(f"- belief: {_belief_text(belief)}")
    return lines[-12:]


def _require_control_reason(arguments: dict, name: str) -> None:
    if not str(arguments.get("reason") or "").strip():
        raise ValueError(f"{name} requires reason")


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


def _previous_context(observations: list[dict] | None, knowledge: list[dict] | None) -> list[str]:
    lines = []
    fresh_knowledge = [item for item in knowledge or [] if item.get("fresh", True)]
    fresh_observations = [item for item in observations or [] if item.get("fresh")]
    if fresh_knowledge:
        lines.append("PREVIOUS SUPPORTED KNOWLEDGE:")
        lines.extend(f"- {item.get('id', '')}: {item.get('statement', '')}" for item in fresh_knowledge[:12])
    if fresh_observations:
        lines.append("PREVIOUS OBSERVATIONS:")
        lines.extend(f"- {item.get('id', '')}: {item.get('summary') or item.get('title') or item.get('tool', '')}" for item in fresh_observations[:20])
    return lines


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


def _record_findings_by_slots(
    *,
    provider: dict,
    model: str,
    messages: list[dict],
    pricing_rules: list[dict],
    usage_total: dict,
    run_id: str,
    reason: str,
    analysis: dict,
    observations: list[dict],
    recorded_findings: dict,
    pending_observation_ids: list[str],
    required_resolution_ids: list[str] | None = None,
) -> Iterator[dict]:
    required_resolution_ids = required_resolution_ids or []
    belief_observation_ids = _dedupe_strings([
        *pending_observation_ids,
        *_semantic_repair_observation_ids(recorded_findings, required_resolution_ids),
    ])
    resolution_slot_ids = _record_resolution_slot_ids(
        analysis,
        observations,
        recorded_findings,
        pending_observation_ids,
        required_resolution_ids,
    )
    slot_messages = [
        {"role": "system", "content": prompt.build_investigation_static(
            app_settings.get_output_language()
        )},
        {"role": "user", "content": _record_slot_context(
            reason,
            analysis,
            observations,
            recorded_findings,
            pending_observation_ids,
            required_resolution_ids,
            belief_observation_ids,
            resolution_slot_ids,
        )},
    ]
    usage_events: list[dict] = []

    def ask(path: str, prompt_text: str) -> JSONValue:
        required = _required_resolution_slot(
            path,
            resolution_slot_ids,
            required_resolution_ids,
        )
        slot_prompt = _record_slot_prompt(
            path,
            prompt_text,
            required=required,
            required_answer_literals=_required_state_write_literals(
                path,
                resolution_slot_ids,
                recorded_findings,
                observations,
            ),
        )
        raw = ""
        attempts = (
            REQUIRED_FINDING_SLOT_ATTEMPTS
            if path.startswith(("beliefs[", "resolutions["))
            else 1
        )
        expected = (
            "a JSON array or null"
            if path == "new_unknowns"
            else "one JSON object" + ("." if required else " or null.")
        )
        attempt_messages = [*slot_messages, {"role": "user", "content": slot_prompt}]
        for attempt in range(attempts):
            assistant = _call_model(
                provider,
                model,
                attempt_messages,
                tools=[],
                use_skills=False,
            )
            if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
                _add_usage(usage_total, usage)
                usage_events.append(start_event(
                    f"{run_id}-usage-record-slot-{len(usage_events)}",
                    "usage",
                    {"delta": usage, "total": usage_total},
                ))
            raw = _normalize_record_slot_answer(
                _content_text(assistant.get("content")),
                path,
            )
            if _valid_record_slot_value(raw, path, required=required):
                break
            if attempt + 1 < attempts:
                attempt_messages = [
                    *slot_messages,
                    {"role": "user", "content": slot_prompt},
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": (
                        f"The {path} slot has the wrong shape. Return {expected}"
                    )},
                ]
        if not _valid_record_slot_value(raw, path, required=required):
            if not required:
                return [] if path == "new_unknowns" else None
            raise ValueError(f"{path} must return {expected}")
        return raw

    filled = json2slots(_record_slot_template(
        belief_observation_ids,
        resolution_slot_ids,
    ), ask)
    yield from usage_events
    filled = filled if isinstance(filled, dict) else {}
    beliefs = _runtime_slot_beliefs(
        filled.get("beliefs"),
        belief_observation_ids,
        recorded_findings,
    )
    resolutions = _runtime_slot_resolutions(
        filled.get("resolutions"),
        resolution_slot_ids,
        observations,
        recorded_findings,
        beliefs,
        pending_observation_ids,
    )
    result: dict = {
        "reason": reason,
        **_empty_recorded_findings(),
        "beliefs": beliefs,
        "resolutions": resolutions,
        "new_unknowns": _runtime_new_unknowns(
            filled.get("new_unknowns"),
            analysis,
            recorded_findings,
            resolutions,
        ),
    }
    return result


def _semantic_repair_observation_ids(
    recorded_findings: dict,
    required_resolution_ids: list[str],
) -> list[str]:
    required = {_normalize_unknown_id(item) for item in required_resolution_ids}
    return _dedupe_strings([
        evidence_id
        for resolution in recorded_findings.get("resolutions", [])
        if isinstance(resolution, dict)
        and resolution.get("repair_mode") == "append_missing_only"
        and _normalize_unknown_id(str(resolution.get("unknown_id") or "")) in required
        for evidence_id in _reference_list(resolution.get("evidence"))
    ])


def _empty_discovery_recording(
    arguments: dict,
    pending_observation_ids: list[str],
    required_resolution_ids: list[str],
) -> bool:
    return bool(
        pending_observation_ids
        and not required_resolution_ids
        and not _has_finding_fields(arguments)
    )


def _audit_recorded_findings(
    *,
    provider: dict,
    model: str,
    messages: list[dict],
    pricing_rules: list[dict],
    usage_total: dict,
    run_id: str,
    analysis: dict,
    observations: list[dict],
    recorded_findings: dict,
    audit_cache: dict[str, dict] | None = None,
) -> Iterator[dict]:
    initial_unknowns = _initial_unknowns(analysis)
    target_resolutions = [
        item
        for item in recorded_findings.get("resolutions", [])
        if isinstance(item, dict)
        and _resolution_requires_semantic_audit(item, initial_unknowns)
    ]
    resolved_ids = [
        str(item.get("unknown_id") or "").strip()
        for item in target_resolutions
    ]
    if not resolved_ids:
        return {"verdicts": []}
    usage_events: list[dict] = []
    verdicts: list[dict] = []
    partial_ids = {
        str(item.get("unknown_id") or "").strip()
        for item in recorded_findings.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") == "partially_resolved"
    }
    independently_verified_ids = {
        str(resolution.get("unknown_id") or "").strip()
        for resolution in recorded_findings.get("resolutions", [])
        if isinstance(resolution, dict)
        and any(
            observation.get("verification")
            for observation in observations
            if isinstance(observation, dict)
            and observation.get("id") in resolution.get("evidence", [])
        )
    }
    contract = {
        "statements": analysis.get("statements", []),
        "acceptance_criteria": analysis.get("acceptance_criteria", []),
        "constraints": analysis.get("constraint_statements", []),
        "scope": analysis.get("scope_statements", {}),
        "reference_baselines": analysis.get("reference_baselines", []),
        "unknowns": analysis.get("unknowns", []),
        "execution_mode": analysis.get("execution_mode", ""),
    }
    all_beliefs = [
        item for item in recorded_findings.get("beliefs", [])
        if isinstance(item, dict)
    ]
    for resolution in target_resolutions:
        unknown_id = str(resolution.get("unknown_id") or "").strip()
        target_belief_ids = set(_reference_list(resolution.get("belief_ids")))
        target_beliefs = [
            item for item in all_beliefs
            if str(item.get("id") or "").strip() in target_belief_ids
        ]
        target_observation_ids = set(_reference_list(resolution.get("evidence"))) | {
            evidence_id
            for belief in target_beliefs
            for evidence_id in _reference_list(belief.get("evidence"))
        }
        target_literals = _grounding_code_literals(" ".join([
            str(resolution.get("answer") or ""),
            *[
                str(belief.get("statement") or "")
                for belief in target_beliefs
            ],
        ]))
        resolution_view = dict(resolution)
        spans = _resolution_grounding_evidence_spans(
            resolution_view,
            {"beliefs": target_beliefs},
            observations,
        )
        if spans:
            resolution_view["grounding_evidence_spans"] = spans
        context = json.dumps({
            "authoritative_task_contract": contract,
            "proposed_findings": {
                "beliefs": target_beliefs,
                "resolutions": [resolution_view],
            },
            "observation_index": [
                _observation_context_view(item, target_literals)
                for item in observations
                if isinstance(item, dict)
                and str(item.get("id") or "").strip() in target_observation_ids
            ],
            "authorized_user_decisions": [
                {
                    "unknown_id": str(resolution.get("unknown_id") or "").strip(),
                    "answer": str(resolution.get("answer") or ""),
                }
                for resolution in target_resolutions
                if str(resolution.get("reason") or "") == CLEARIFY_RESOLUTION_REASON
                and str(resolution.get("answer") or "").strip()
            ],
            "required_unknown_ids": [unknown_id],
        }, ensure_ascii=False)
        cache_key = "audit:v2:" + hashlib.sha256(context.encode("utf-8")).hexdigest()
        if audit_cache is not None and cache_key in audit_cache:
            cached_verdicts = audit_cache[cache_key].get("verdicts", [])
            verdicts.extend(cached_verdicts)
            yield from _quality_gate_events(run_id, unknown_id, cached_verdicts, 0)
            continue
        audit_messages = [{"role": "system", "content": prompt.build_investigation_auditor(
            app_settings.get_output_language()
        )}]
        audit = {"verdicts": []}
        for attempt in range(REQUIRED_AUDIT_ATTEMPTS):

            def ask(_path: str, slot_prompt: str) -> JSONValue:
                assistant = _call_model(provider, model, [
                    *audit_messages,
                    {"role": "user", "content": f"{slot_prompt}\ncontext: {context}"},
                ], tools=[])
                if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
                    _add_usage(usage_total, usage)
                    usage_events.append(start_event(
                        f"{run_id}-usage-investigation-audit-{len(usage_events)}",
                        "usage",
                        {"delta": usage, "total": usage_total},
                    ))
                return _content_text(assistant.get("content"))

            filled = json2slots({"verdicts": "____"}, ask)
            audit = _normalize_investigation_audit(filled, [unknown_id])
            covered_ids = {
                item["unknown_id"] for item in audit["verdicts"]
            }
            invalid_grounded_ids = {
                item["unknown_id"]
                for item in audit["verdicts"]
                if item["status"] == "grounded"
                and item["unknown_id"] in partial_ids - independently_verified_ids
            }
            if covered_ids == {unknown_id} and not invalid_grounded_ids:
                break
            problem = (
                "A partial resolution was incorrectly marked grounded without independent "
                "verification. Return verify with one atomic hypothesis, or investigate, for: "
                + ", ".join(sorted(invalid_grounded_ids))
                if invalid_grounded_ids
                else "The audit omitted conclusions. Return exactly one verdict for: "
                + unknown_id
            )
            audit_messages.append({"role": "user", "content": problem})
        invalid_grounded_ids = {
            item["unknown_id"]
            for item in audit["verdicts"]
            if item["status"] == "grounded"
            and item["unknown_id"] in partial_ids - independently_verified_ids
        }
        for item in audit["verdicts"]:
            if item["unknown_id"] in invalid_grounded_ids:
                item["status"] = "investigate"
                item["reason"] = (
                    "The partial conclusion was not independently verified and cannot be "
                    "promoted from the same evidence."
                )
        if audit_cache is not None:
            audit_cache[cache_key] = audit
        verdicts.extend(audit["verdicts"])
        yield from _quality_gate_events(run_id, unknown_id, audit["verdicts"], attempt)
    for event in usage_events:
        yield event
    return {"verdicts": verdicts}


def _quality_gate_events(
    run_id: str,
    unknown_id: str,
    verdicts: list[dict],
    index: int,
):
    """把语义质量门（audit）的判定结果作为事件发送给前端展示。"""
    for i, verdict in enumerate(verdicts):
        yield start_event(
            f"{run_id}-quality-gate-{unknown_id}-{index}-{i}",
            "quality_gate",
            {
                "unknown_id": str(verdict.get("unknown_id") or ""),
                "status": str(verdict.get("status") or ""),
                "reason": str(verdict.get("reason") or ""),
                "missing": verdict.get("missing") or [],
                "repair_mode": str(verdict.get("repair_mode") or ""),
                "hypothesis": str(verdict.get("hypothesis") or ""),
                "question": str(verdict.get("question") or ""),
            },
        )


def _normalize_investigation_audit(value, resolved_ids: list[str]) -> dict:
    raw = value.get("verdicts") if isinstance(value, dict) else None
    raw = raw if isinstance(raw, list) else []
    allowed = {"grounded", "verify", "clearify", "investigate"}
    by_id = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        unknown_id = str(item.get("unknown_id") or "").strip()
        status = str(item.get("status") or "").strip()
        matched_id = next(
            (known_id for known_id in resolved_ids if _same_unknown_id(unknown_id, known_id)),
            "",
        )
        if not matched_id or status not in allowed:
            continue
        by_id[matched_id] = {
            "unknown_id": matched_id,
            "status": status,
            "reason": str(item.get("reason") or "").strip(),
            "missing": _semantic_missing_items(item.get("missing")),
            "repair_mode": (
                "append_missing_only"
                if str(item.get("repair_mode") or "").strip() == "append_missing_only"
                else ""
            ),
            "hypothesis": str(item.get("hypothesis") or "").strip(),
            "question": str(item.get("question") or "").strip(),
        }
    return {"verdicts": [
        by_id.get(unknown_id, {
            "unknown_id": unknown_id,
            "status": "investigate",
            "reason": "The semantic audit did not return a usable verdict.",
            "hypothesis": "",
            "question": "",
        })
        for unknown_id in resolved_ids
    ]}


def _semantic_missing_items(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        requirement = str(raw.get("requirement") or raw.get("text") or "").strip()
        if not requirement:
            continue
        items.append({
            "acceptance_id": str(raw.get("acceptance_id") or "").strip(),
            "requirement": requirement,
        })
    return items


def _record_slot_template(
    belief_observation_ids: list[str] | None = None,
    resolution_ids: list[str] | None = None,
) -> dict[str, JSONValue]:
    return {
        "beliefs": ["____" for _ in belief_observation_ids or []],
        "resolutions": ["____" for _ in resolution_ids or []],
        "new_unknowns": "____",
    }


def _record_slot_context(
    reason: str,
    analysis: dict,
    observations: list[dict],
    recorded_findings: dict,
    pending_observation_ids: list[str],
    required_resolution_ids: list[str],
    belief_observation_ids: list[str] | None = None,
    resolution_slot_ids: list[str] | None = None,
) -> str:
    payload = {
        "mode": "record_investigation_findings_slots",
        "record_reason": reason,
        "cache_policy": "Fill one bound item per request. Runtime owns ids and evidence links.",
        "task": {
            "intent": analysis.get("intent", {}),
            "unknowns": analysis.get("unknowns", []),
            "acceptance_criteria": analysis.get("acceptance_criteria", []),
        },
        "pending_observation_ids": list(pending_observation_ids),
        "observation_refs": _observation_reference_payload(observations),
        "required_resolution_ids": list(required_resolution_ids),
        "observations": _record_slot_relevant_observations(
            observations,
            required_resolution_ids,
            belief_observation_ids or pending_observation_ids,
        ),
        "already_recorded": _record_slot_relevant_findings(
            recorded_findings,
            required_resolution_ids,
            belief_observation_ids or pending_observation_ids,
        ),
        "runtime_slot_bindings": {
            "beliefs": [
                {"index": index, "observation_id": observation_id}
                for index, observation_id in enumerate(belief_observation_ids or pending_observation_ids)
            ],
            "resolutions": [
                {"index": index, "unknown_id": unknown_id}
                for index, unknown_id in enumerate(resolution_slot_ids or required_resolution_ids)
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _record_slot_relevant_findings(
    recorded_findings: dict,
    required_resolution_ids: list[str],
    observation_ids: list[str],
) -> dict:
    required = {_normalize_unknown_id(item) for item in required_resolution_ids}
    evidence = {str(item).strip() for item in observation_ids if str(item).strip()}
    resolutions = [
        item for item in recorded_findings.get("resolutions", [])
        if isinstance(item, dict)
        and (
            not required
            or _normalize_unknown_id(str(item.get("unknown_id") or "")) in required
        )
    ]
    belief_ids = {
        belief_id
        for resolution in resolutions
        for belief_id in _reference_list(resolution.get("belief_ids"))
    }
    beliefs = [
        item for item in recorded_findings.get("beliefs", [])
        if isinstance(item, dict)
        and (
            str(item.get("id") or "").strip() in belief_ids
            or bool(evidence.intersection(_reference_list(item.get("evidence"))))
        )
    ]
    return {
        **_empty_recorded_findings(),
        "beliefs": beliefs,
        "resolutions": resolutions,
        "new_unknowns": [
            item for item in recorded_findings.get("new_unknowns", [])
            if isinstance(item, dict)
            and (
                not required
                or _normalize_unknown_id(str(item.get("id") or "")) in required
            )
        ],
    }


def _record_slot_relevant_observations(
    observations: list[dict],
    required_resolution_ids: list[str],
    observation_ids: list[str],
) -> list[dict]:
    required = {_normalize_unknown_id(item) for item in required_resolution_ids}
    selected_ids = {str(item).strip() for item in observation_ids if str(item).strip()}
    selected = [
        item for item in observations
        if isinstance(item, dict)
        and (
            str(item.get("id") or "").strip() in selected_ids
            or bool(required.intersection(
                _normalize_unknown_id(value)
                for value in item.get("target_unknown_ids", [])
            ))
        )
    ]
    return [_observation_context_view(item) for item in selected[-12:]]


def _record_slot_prompt(
    path: str,
    prompt_text: str,
    *,
    required: bool = False,
    required_answer_literals: list[str] | None = None,
) -> str:
    payload = {
        "slot": path,
        "instruction": prompt_text,
        "contract": _record_slot_contract(path),
        "required_non_empty": required,
        "required_exact_answer_literals": required_answer_literals or [],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _record_slot_contract(path: str) -> str:
    if path.startswith("beliefs["):
        return (
            "Return null when this observation has no material finding. Otherwise return one "
            "JSON object with statement and status. Runtime supplies id and evidence."
        )
    if path.startswith("resolutions["):
        return (
            "Return null when the bound unknown is not reduced by available evidence. Otherwise "
            "return one JSON object with status, answer, and reason. Runtime supplies unknown_id, "
            "observation_ids, and belief_ids. status is resolved, partially_resolved, needs_clearify, or deferred. "
            "For append-only semantic repairs, include repair_mode=append_missing_only."
        )
    contracts = {
        "beliefs": (
            "Return a JSON array of objects with statement, status, observation_ids. "
            "status is one of unverified, plausible, supported, strongly_supported, runtime_confirmed, contradicted, invalidated. "
            "observation_ids must use runtime refs such as obs_1 or exact observation ids."
        ),
        "resolutions": (
            "Return a JSON array of objects with unknown_id, status, answer, observation_ids, belief_ids, reason. "
            "status is resolved, partially_resolved, needs_clearify, or deferred."
        ),
        "new_unknowns": (
            "Return a JSON array of new unknown objects with id, question, blocking, resolution_strategy. "
            "resolution_strategy is investigate_project, clearify, or deferred. Add only material facts "
            "that must be resolved before design; do not add implementation-mechanism or design-choice questions."
        ),
        "user_decisions_required": "Return a JSON array of user decision question strings.",
    }
    return contracts.get(path, "Return the JSON value for this slot only.")


def _record_resolution_slot_ids(
    analysis: dict,
    observations: list[dict],
    recorded_findings: dict,
    pending_observation_ids: list[str],
    required_resolution_ids: list[str],
) -> list[str]:
    unknowns = _merge_unknowns(
        _initial_unknowns(analysis)
        + (
            []
            if _analysis_is_read_only(analysis)
            else _unknowns(recorded_findings.get("new_unknowns"))
        )
    )
    investigable = {
        item["id"]
        for item in unknowns
        if item.get("resolution_strategy") == "investigate_project"
    }
    pending = set(pending_observation_ids)
    candidates = list(required_resolution_ids)
    for observation in observations:
        if observation.get("id") not in pending:
            continue
        candidates.extend(
            _normalize_unknown_id(item)
            for item in observation.get("target_unknown_ids", [])
        )
    return [
        item
        for index, item in enumerate(candidates)
        if item in investigable and item not in candidates[:index]
    ]


def _required_resolution_slot(
    path: str,
    resolution_slot_ids: list[str],
    required_resolution_ids: list[str],
) -> bool:
    match = re.fullmatch(r"resolutions\[(\d+)\]", path)
    if not match:
        return False
    index = int(match.group(1))
    return (
        index < len(resolution_slot_ids)
        and resolution_slot_ids[index] in set(required_resolution_ids)
    )


def _required_state_write_literals(
    path: str,
    resolution_slot_ids: list[str],
    recorded_findings: dict,
    observations: list[dict],
) -> list[str]:
    match = re.fullmatch(r"resolutions\[(\d+)\]", path)
    if not match:
        return []
    index = int(match.group(1))
    if index >= len(resolution_slot_ids):
        return []
    unknown_id = resolution_slot_ids[index]
    resolution = next((
        item
        for item in recorded_findings.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("unknown_id") or "").strip() == unknown_id
        and str(item.get("reason") or "").startswith(STATE_WRITE_REASON_PREFIX)
    ), None)
    if resolution is None:
        return []
    return _missing_grounding_state_writes(
        resolution,
        recorded_findings,
        observations,
    )


def _valid_record_slot_value(value: str, path: str, *, required: bool) -> bool:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False
    if path == "new_unknowns":
        return parsed is None or parsed == {} or isinstance(parsed, list)
    if parsed is None:
        return not required
    if not isinstance(parsed, dict):
        return False
    if path.startswith("beliefs["):
        return bool(_belief_text(parsed))
    if path.startswith("resolutions["):
        status = str(parsed.get("status") or "").strip()
        return (
            status in {"resolved", "partially_resolved", "needs_clearify", "deferred"}
            and bool(str(parsed.get("answer") or parsed.get("reason") or "").strip())
        )
    return True


def _normalize_record_slot_answer(value: str, path: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    parsed: JSONValue = None
    for candidate in _json_candidates(text):
        try:
            parsed, _ = json.JSONDecoder().raw_decode(candidate)
            break
        except json.JSONDecodeError:
            continue
    else:
        return text
    field = path.split("[", 1)[0]
    if isinstance(parsed, dict) and field in parsed:
        parsed = parsed[field]
    if path.startswith(("beliefs[", "resolutions[")) and isinstance(parsed, list):
        if not parsed:
            parsed = None
        elif len(parsed) == 1:
            parsed = parsed[0]
    return json.dumps(parsed, ensure_ascii=False)


def _runtime_slot_beliefs(
    value,
    observation_ids: list[str],
    recorded_findings: dict,
) -> list[dict]:
    raw_items = value if isinstance(value, list) else []
    existing = _beliefs(recorded_findings.get("beliefs"))
    used_ids = {
        str(item.get("id") or "").strip()
        for item in existing
        if str(item.get("id") or "").strip()
    }
    next_id = max(
        (int(match.group(1)) for item in used_ids if (match := re.fullmatch(r"B(\d+)", item))),
        default=0,
    ) + 1
    beliefs = []
    for observation_id, raw in zip(observation_ids, raw_items):
        if not isinstance(raw, dict) or not _belief_text(raw):
            continue
        while f"B{next_id}" in used_ids:
            next_id += 1
        beliefs.append({
            **raw,
            "id": f"B{next_id}",
            "evidence": [observation_id],
        })
        used_ids.add(f"B{next_id}")
        next_id += 1
    return _beliefs(beliefs)


def _runtime_slot_resolutions(
    value,
    resolution_ids: list[str],
    observations: list[dict],
    recorded_findings: dict,
    new_beliefs: list[dict],
    pending_observation_ids: list[str] | None = None,
) -> list[dict]:
    raw_items = value if isinstance(value, list) else []
    beliefs = _beliefs(recorded_findings.get("beliefs")) + _beliefs(new_beliefs)
    pending = set(pending_observation_ids or [])
    resolutions = []
    for unknown_id, raw in zip(resolution_ids, raw_items):
        if not isinstance(raw, dict):
            continue
        matching_evidence_ids = [
            str(item.get("id") or "").strip()
            for item in observations
            if isinstance(item, dict)
            and str(item.get("id") or "").strip()
            and unknown_id in {
                _normalize_unknown_id(value)
                for value in item.get("target_unknown_ids", [])
            }
        ]
        pending_evidence_ids = [
            item for item in matching_evidence_ids
            if item in pending
        ]
        evidence_ids = pending_evidence_ids or matching_evidence_ids
        belief_ids = [
            item["id"]
            for item in beliefs
            if set(item.get("evidence", [])).intersection(evidence_ids)
        ]
        resolutions.append({
            **raw,
            "unknown_id": unknown_id,
            "evidence": _dedupe_strings(evidence_ids),
            "belief_ids": _dedupe_strings(belief_ids),
        })
    return resolutions


def _runtime_new_unknowns(
    value,
    analysis: dict,
    recorded_findings: dict,
    resolutions: list[dict] | None = None,
) -> list[dict]:
    if _analysis_is_read_only(analysis):
        return []
    if any(
        item.get("status") != "resolved"
        for item in resolutions or []
        if isinstance(item, dict)
    ):
        return []
    existing = _merge_unknowns(
        _initial_unknowns(analysis)
        + _unknowns(recorded_findings.get("unknowns"))
        + _unknowns(recorded_findings.get("new_unknowns"))
    )
    known_questions = {_question_key(item["question"]) for item in existing}
    used_ids = {str(item.get("id") or "").strip() for item in existing}
    next_id = max(
        (int(match.group(1)) for item in used_ids if (match := re.fullmatch(r"U(\d+)", item))),
        default=0,
    ) + 1
    result = []
    for item in _unknowns(value):
        question_key = _question_key(item.get("question", ""))
        if not question_key or question_key in known_questions:
            continue
        while f"U{next_id}" in used_ids:
            next_id += 1
        result.append({**item, "id": f"U{next_id}"})
        known_questions.add(question_key)
        used_ids.add(f"U{next_id}")
        next_id += 1
    return result


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


def _finalize_investigation(
    *,
    provider: dict,
    model: str,
    messages: list[dict],
    pricing_rules: list[dict],
    usage_total: dict,
    run_id: str,
    analysis: dict | None = None,
    observations: list[dict] | None = None,
    recorded_findings: dict | None = None,
    audit_cache: dict[str, dict] | None = None,
    reason: str = "Investigation needs a final structured summary.",
    workspace_dir: str = "",
) -> Iterator[dict]:
    messages.append({"role": "user", "content": prompt.build_investigation_finalize(reason)})
    last_error = ""
    last_content = ""
    last_arguments: dict | None = None

    attempts = app_settings.get_round_limit("investigation_finalization_attempts")
    repeated_tool_error_name = ""
    repeated_tool_error_count = 0
    already_resolved_error_count = 0
    repeated_finalization_error_key = ""
    repeated_finalization_error_count = 0
    stop_finalization = False
    quality_audit: dict = {}
    best_progress = _finalization_progress_score(recorded_findings, observations or [])
    no_progress_attempts = 0
    for attempt in _round_indexes(attempts, start=0):
        thinking_id = f"{run_id}-thinking-final-{attempt}"
        yield start_event(thinking_id, "thinking", {
            "text": reason,
            "done": False,
            "open": True,
        })
        assistant = _call_model(
            provider,
            model,
            messages,
            tools=[_resolve_unknowns_tool_schema(), _record_findings_tool_schema(), _finish_tool_schema()],
            tool_choice="required",
        )
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-final-{attempt}", "usage", {
                "delta": usage,
                "total": usage_total,
            })

        tool_calls = assistant.get("tool_calls") or []
        content = _assistant_visible_text(assistant) or _tool_call_summary(tool_calls)
        last_content = content or last_content
        record_calls = [
            call for call in tool_calls
            if ((call.get("function") or {}).get("name") in {"resolve_unknowns", "record_investigation_findings"})
        ]
        finish_call = next(
            (call for call in tool_calls if ((call.get("function") or {}).get("name") == "finish_investigation")),
            None,
        )
        replay = {"role": "assistant", "content": assistant.get("content") or ""}
        for key in ("reasoning_content", "reasoning"):
            if assistant.get(key):
                replay[key] = assistant[key]
        if record_calls or finish_call:
            replay["tool_calls"] = record_calls + ([finish_call] if finish_call else [])
        messages.append(replay)
        yield {"op": "update", "id": thinking_id, "patch": {
            "text": content,
            "done": True,
            "open": bool(finish_call),
        }}

        for record_call in record_calls:
            call_id = record_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = record_call.get("function") or {}
            record_name = str(function.get("name") or "")
            try:
                record_arguments = _tool_arguments(function.get("arguments"))
                if record_name == "resolve_unknowns":
                    record_arguments = _resolve_unknown_arguments(record_arguments)
                    _require_control_reason(record_arguments, "resolve_unknowns")
                    if not record_arguments.get("resolutions"):
                        raise ValueError("resolve_unknowns requires at least one valid resolution")
                    _validate_resolution_refs(
                        record_arguments["resolutions"],
                        _beliefs((recorded_findings or {}).get("beliefs")),
                        observations or [],
                    )
                else:
                    record_arguments = _record_arguments(record_arguments)
                    _require_control_reason(record_arguments, "record_investigation_findings")
                required_resolution_ids = _unknowns_needing_resolution(
                    recorded_findings or _empty_recorded_findings(),
                    observations or [],
                    analysis,
                )
                if (
                    record_name != "resolve_unknowns"
                    and not required_resolution_ids
                    and not _has_finding_fields(record_arguments)
                ):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(_nothing_to_record_result(), ensure_ascii=False),
                    })
                    continue
                if record_name != "resolve_unknowns" and not _has_finding_fields(record_arguments):
                    record_arguments = yield from _record_findings_by_slots(
                        provider=provider,
                        model=model,
                        messages=messages[:-1],
                        pricing_rules=pricing_rules,
                        usage_total=usage_total,
                        run_id=run_id,
                        reason=str(record_arguments.get("reason") or "").strip(),
                        analysis=analysis or {},
                        observations=observations or [],
                        recorded_findings=recorded_findings or _empty_recorded_findings(),
                        pending_observation_ids=[],
                        required_resolution_ids=required_resolution_ids,
                    )
                    if not _has_finding_fields(record_arguments):
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": json.dumps(_nothing_to_record_result(), ensure_ascii=False),
                        })
                        continue
                _require_finding_fields(record_arguments)
                _reject_empty_repair(record_arguments, recorded_findings or _empty_recorded_findings())
                recorded_findings = _merge_recorded_findings(
                    recorded_findings or _empty_recorded_findings(),
                    record_arguments,
                )
                recorded_findings = _bind_grounding_evidence(
                    recorded_findings,
                    observations or [],
                )
                recorded_findings = _apply_direct_resolution_gate(
                    recorded_findings,
                    observations or [],
                )
                if (analysis or {}).get("_canonicalized"):
                    quality_audit = yield from _audit_recorded_findings(
                        provider=provider,
                        model=model,
                        messages=[
                            item for item in messages[:-1]
                            if item.get("role") != "system"
                        ],
                        pricing_rules=pricing_rules,
                        usage_total=usage_total,
                        run_id=run_id,
                        analysis=analysis or {},
                        observations=observations or [],
                        recorded_findings=recorded_findings,
                        audit_cache=audit_cache,
                    )
                    recorded_findings, _, _ = _apply_investigation_audit(
                        recorded_findings,
                        quality_audit,
                        observations=observations or [],
                        analysis=analysis or {},
                    )
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps({"recorded": True, "tool": record_name}, ensure_ascii=False),
                })
            except Exception as exc:
                last_error = f"{record_name or 'record_investigation_findings'} arguments were invalid: {exc}"
                raw_arguments = function.get("arguments") or "{}"
                partial_arguments = _partial_tool_arguments(raw_arguments)
                if record_name != "resolve_unknowns":
                    partial_arguments = _record_arguments(partial_arguments)
                if _has_finding_fields(partial_arguments):
                    recorded_findings = _merge_recorded_findings(
                        recorded_findings or _empty_recorded_findings(),
                        partial_arguments,
                    )
                if repeated_tool_error_name == record_name:
                    repeated_tool_error_count += 1
                else:
                    repeated_tool_error_name = record_name
                    repeated_tool_error_count = 1
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _tool_repair_error_json(
                        exc,
                        record_name or "record_investigation_findings",
                        raw_arguments,
                        partial_arguments,
                        observations=observations or [],
                    ),
                })
                stop_finalization = repeated_tool_error_count >= MAX_REPEATED_TOOL_ERRORS

        if finish_call:
            call_id = finish_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = finish_call.get("function") or {}
            try:
                last_arguments = _tool_arguments(function.get("arguments"))
                _require_control_reason(last_arguments, "finish_investigation")
                final = _finish_payload(
                    _finish_arguments(
                        recorded_findings or _empty_recorded_findings(),
                        last_arguments,
                        prefer_finish_summary=not _analysis_requests_implementation(analysis),
                    ),
                    analysis=analysis,
                    observations=observations or [],
                    repair_conflicts=True,
                    workspace_dir=workspace_dir,
                )
            except Exception as exc:
                last_error = f"finish_investigation arguments were invalid: {exc}"
                raw_arguments = function.get("arguments") or "{}"
                partial_arguments = _partial_tool_arguments(raw_arguments)
                if partial_arguments:
                    last_arguments = partial_arguments
                if repeated_tool_error_name == "finish_investigation":
                    repeated_tool_error_count += 1
                else:
                    repeated_tool_error_name = "finish_investigation"
                    repeated_tool_error_count = 1
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _tool_repair_error_json(
                        ValueError(last_error),
                        "finish_investigation",
                        raw_arguments,
                        partial_arguments,
                        observations=observations or [],
                    ),
                })
                stop_finalization = repeated_tool_error_count >= MAX_REPEATED_TOOL_ERRORS
            else:
                if quality_audit:
                    final["quality_audit"] = quality_audit
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(final, ensure_ascii=False),
                })
                if final.get("resolution_repair") and (attempts <= 0 or attempt < attempts - 1):
                    last_error = "Investigation findings need explicit resolutions before finalizing."
                    messages.append({
                        "role": "user",
                        "content": _resolution_repair_prompt(final["resolution_repair"]),
                    })
                else:
                    return final
        elif content:
            last_error = "Investigation finalization must use resolve_unknowns or record_investigation_findings, then finish_investigation."
        else:
            last_error = "Investigation finalization started before finish_investigation was called."

        failure_key = json.dumps({
            "error": last_error,
            "content": content,
            "tool_calls": bool(tool_calls),
        }, ensure_ascii=False, sort_keys=True)
        if failure_key == repeated_finalization_error_key:
            repeated_finalization_error_count += 1
        else:
            repeated_finalization_error_key = failure_key
            repeated_finalization_error_count = 1
        progress = _finalization_progress_score(recorded_findings, observations or [])
        if progress > best_progress:
            best_progress = progress
            no_progress_attempts = 0
        else:
            no_progress_attempts += 1

        if stop_finalization:
            return _runtime_recovered_investigation(
                last_error or "Investigation finalization repeated the same invalid tool call.",
                analysis,
                observations or [],
                recorded_findings or _empty_recorded_findings(),
            )
        if repeated_finalization_error_count >= MAX_REPEATED_TOOL_ERRORS:
            return _runtime_recovered_investigation(
                last_error or "Investigation finalization repeated the same invalid response.",
                analysis,
                observations or [],
                recorded_findings or _empty_recorded_findings(),
            )
        if no_progress_attempts >= MAX_REPEATED_TOOL_ERRORS:
            return _runtime_recovered_investigation(
                last_error or "Investigation finalization made no contract progress.",
                analysis,
                observations or [],
                recorded_findings or _empty_recorded_findings(),
            )

        if attempts <= 0 or attempt < attempts - 1:
            messages.append({
                "role": "user",
                "content": (
                    f"Previous finalization failed: {last_error}\n"
                    "Do not call discovery tools or repeat investigation. "
                    "Fix only the finalization arguments: create/cite belief ids like B1 in "
                    "record_investigation_findings, use those ids in resolution.belief_ids, "
                    "then call finish_investigation with summary and recommended_next_step."
                ),
            })

    if last_arguments:
        try:
            return _finish_payload(
                _finish_arguments(
                    recorded_findings or _empty_recorded_findings(),
                    last_arguments,
                    prefer_finish_summary=not _analysis_requests_implementation(analysis),
                ),
                analysis=analysis,
                observations=observations or [],
                repair_conflicts=True,
                workspace_dir=workspace_dir,
            )
        except Exception:
            pass

    return _runtime_recovered_investigation(
        last_error or "finish_investigation did not produce a usable result.",
        analysis,
        observations or [],
        recorded_findings or _empty_recorded_findings(),
    )


def _runtime_recovered_investigation(
    reason: str,
    analysis: dict | None,
    observations: list[dict],
    recorded_findings: dict,
) -> dict:
    facts = _runtime_patch_facts(observations, recorded_findings)
    initial_unknowns = _initial_unknowns(analysis)
    recorded_unknowns = _unknowns(recorded_findings.get("unknowns"))
    if not _analysis_is_read_only(analysis):
        recorded_unknowns += _unknowns(recorded_findings.get("new_unknowns"))
    known_unknowns = _merge_unknowns(initial_unknowns + recorded_unknowns)
    resolutions = _complete_resolutions(
        _resolutions(recorded_findings.get("resolutions")),
        known_unknowns,
        recorded_unknowns,
    )
    unknowns = _unresolved_from_resolutions(resolutions, known_unknowns)
    read_only_complete = _analysis_is_read_only(analysis) and not unknowns
    read_only_summary = "\n\n".join(
        str(item.get("answer") or "").strip()
        for item in resolutions
        if item.get("status") == "resolved" and str(item.get("answer") or "").strip()
    )
    return {
        "summary": (
            read_only_summary
            if read_only_complete and read_only_summary
            else "Investigation auto-recovered from tool errors; see task panel for details."
        ),
        "ready_for_patch_planning": False,
        "runtime_recovered": True,
        "runtime_failure": not read_only_complete,
        "recovery_reason": reason,
        "beliefs": recorded_findings.get("beliefs", []),
        "resolutions": resolutions,
        "unknowns": unknowns,
        "open_questions": [] if read_only_complete else [reason],
        "patch_planning_facts": facts,
        "patch_planning_context": facts,
    }


def _finalization_progress_score(
    recorded_findings: dict | None,
    observations: list[dict],
) -> tuple[int, int, int]:
    recorded = recorded_findings or {}
    observation_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    beliefs = _beliefs(recorded.get("beliefs"))
    resolutions = _resolutions(recorded.get("resolutions"))
    grounded_beliefs = sum(
        1 for item in beliefs
        if item.get("evidence")
        and set(item["evidence"]).issubset(observation_ids)
    )
    resolved = sum(1 for item in resolutions if item.get("status") == "resolved")
    evidence_links = sum(
        len(item.get("evidence", [])) + len(item.get("belief_ids", []))
        for item in resolutions
    )
    return grounded_beliefs, resolved, evidence_links


def _runtime_patch_facts(observations: list[dict], recorded_findings: dict) -> list[str]:
    facts = []
    for belief in recorded_findings.get("beliefs", []):
        if (
            isinstance(belief, dict)
            and belief.get("status") in {"supported", "strongly_supported", "runtime_confirmed"}
            and belief.get("evidence")
            and _belief_text(belief)
        ):
            facts.append(_belief_text(belief))
    for resolution in recorded_findings.get("resolutions", []):
        if (
            isinstance(resolution, dict)
            and resolution.get("status") == "resolved"
            and resolution.get("answer")
            and (
                resolution.get("evidence")
                or resolution.get("belief_ids")
                or resolution.get("reason") == CLEARIFY_RESOLUTION_REASON
            )
        ):
            facts.append(str(resolution["answer"]))
    for item in observations:
        if isinstance(item, dict) and item.get("summary"):
            facts.append(f"{item.get('tool') or 'tool'}: {item['summary']}")
    return _dedupe_strings(facts)[:20]


def _dedupe_strings(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = " ".join(str(value or "").split())
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _clearify_question(
    arguments: dict,
    *,
    question_id: str,
    analysis: dict | None,
) -> dict:
    target_ids = _target_unknown_ids(arguments)
    question = str(arguments.get("question") or "").strip()
    if not question:
        raise ValueError("clearify requires question")
    options = _clearify_options(arguments.get("options"))
    return {
        "id": question_id,
        "question_id": question_id,
        "analysis_id": (analysis or {}).get("id", ""),
        "unknown_id": target_ids[0] if target_ids else "",
        "question": question,
        "options": options[:3],
        "custom_allowed": True,
        "origin_message": (analysis or {}).get("origin_message", ""),
        "clearify_tool": True,
        "tool_name": "clearify",
    }


def _clearify_options(raw_options) -> list[dict]:
    if not isinstance(raw_options, list) or len(raw_options) != 3:
        raise ValueError("clearify requires exactly three options")
    options = []
    for index, raw in enumerate(raw_options, start=1):
        if not isinstance(raw, dict):
            raise ValueError("clearify options must be objects")
        label = str(raw.get("label") or raw.get("value") or "").strip()
        value = str(raw.get("value") or label).strip()
        if not label or not value:
            raise ValueError("clearify option label and value are required")
        option = dict(raw)
        option["id"] = str(option.get("id") or f"option_{index}").strip()
        option["label"] = label
        option["value"] = value
        options.append(option)
    return options


def _clearify_tool_result(answer: dict | None) -> str:
    answer = answer or {}
    response = str(answer.get("response") or answer.get("text") or "").strip()
    return json.dumps({
        "question": answer.get("question") or "",
        "selected_option_id": answer.get("selected_option_id") or "",
        "selected_option_label": answer.get("selected_option_label") or "",
        "answer": response,
    }, ensure_ascii=False)


def _clearify_resolution(arguments: dict, answer: dict | None) -> dict | None:
    resolutions = _clearify_resolutions(arguments, answer)
    return resolutions[0] if resolutions else None


def _clearify_resolutions(arguments: dict, answer: dict | None) -> list[dict]:
    return [
        item for item in _clearify_resolution_records(arguments, answer)
        if item.get("status") == "resolved"
    ]


def _clearify_resolution_records(arguments: dict, answer: dict | None) -> list[dict]:
    answer = answer or {}
    text = _clearify_answer_text(answer)
    if not text:
        raise ValueError("clearify answer is empty")
    target_ids = _target_unknown_ids(arguments)
    if not target_ids:
        if arguments.get("orientation"):
            return []
        raise ValueError("clearify answer has no target unknown")
    status = "partially_resolved" if _clearify_answer_is_non_answer(text) else "resolved"
    reason = CLEARIFY_UNRESOLVED_REASON if status == "partially_resolved" else CLEARIFY_RESOLUTION_REASON
    return [
        {
            "unknown_id": unknown_id,
            "status": status,
            "answer": text,
            "evidence": [],
            "belief_ids": [],
            "reason": reason,
        }
        for unknown_id in target_ids
    ]


def _clearify_answer_text(answer: dict) -> str:
    return str(
        answer.get("response")
        or answer.get("text")
        or answer.get("selected_option_label")
        or ""
    ).strip()


def _clearify_answer_is_non_answer(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    if not normalized:
        return True
    if any(marker in normalized for marker in ("but", "但是", "但")) and any(
        marker in normalized
        for marker in ("choose", "select", "use ", "option", "方案", "选择", "用")
    ):
        return False
    return any(
        marker in normalized
        for marker in (
            "don't know",
            "do not know",
            "not sure",
            "no idea",
            "cannot answer",
            "can't answer",
            "you check",
            "you investigate",
            "please investigate",
            "不知道",
            "不清楚",
            "不确定",
            "无法确定",
            "无法回答",
            "没法回答",
            "你再查",
            "你再检查",
            "你查一下",
            "你继续查",
        )
    )


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
            from . import subagents

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


def _known_unknowns_by_canonical_id(analysis: dict | None) -> dict[str, dict]:
    known: dict[str, dict] = {
        _normalize_unknown_id(item.get("id")): item
        for item in (analysis or {}).get("unknowns", [])
        if isinstance(item, dict) and _normalize_unknown_id(item.get("id"))
    }
    # 已 resolved/deferred 的 unknown 可能被从 analysis["unknowns"] 移除
    # （见 investigation_context._open_analysis_unknowns），但模型仍可能合法地
    # 引用它们补充证据——从 resolutions 补回 known 表，避免误报"不在契约中"。
    for item in (analysis or {}).get("resolutions", []):
        if not isinstance(item, dict):
            continue
        known_id = _normalize_unknown_id(item.get("unknown_id"))
        if known_id and known_id not in known:
            known[known_id] = {"id": known_id, "resolved": True}
    return known


def _canonicalize_resolution_unknown_ids(
    resolutions: list[dict],
    analysis: dict | None,
) -> list[dict]:
    known = _known_unknowns_by_canonical_id(analysis)
    if not known:
        return resolutions
    result = []
    invalid = []
    for resolution in resolutions:
        unknown_id = str(resolution.get("unknown_id") or "").strip()
        canonical_id = _normalize_unknown_id(unknown_id)
        if canonical_id not in known:
            invalid.append(unknown_id)
            continue
        if (
            resolution.get("status") == "needs_clearify"
            and known[canonical_id].get("type") != "product_decision"
        ):
            raise ValueError(
                "needs_clearify resolutions require product_decision unknowns: "
                + unknown_id
            )
        item = dict(resolution)
        item["unknown_id"] = canonical_id
        result.append(item)
    if invalid:
        raise ValueError(
            "resolve_unknowns unknown_id not in task contract: "
            + ", ".join(invalid)
        )
    return result


def _target_unknown_ids(arguments: dict) -> list[str]:
    raw = arguments.get("target_unknown_ids")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [item for value in raw if (item := _normalize_unknown_id(value))]


def _normalize_unknown_id(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if ":" in text:
        text = text.rsplit(":", 1)[-1].strip()
    return text


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


def _observation_evidence_excerpt(value) -> str:
    text = str(value or "")
    if len(text) <= OBSERVATION_EVIDENCE_CHARS:
        return text
    half = OBSERVATION_EVIDENCE_CHARS // 2
    return f"{text[:half]}\n...\n{text[-half:]}"


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


def _investigation_tool_arguments(
    name: str,
    raw: str | None,
    *,
    pending_observation_ids: list[str],
    resolution_required_ids: list[str],
) -> dict:
    try:
        return _tool_arguments(raw)
    except ValueError:
        if (
            name == "record_investigation_findings"
            and (pending_observation_ids or resolution_required_ids)
        ):
            return {"reason": RECORD_RECOVERY_REASON}
        raise


def _resolution_repair_request(
    arguments: dict,
    initial_unknowns: list[dict],
    explicit_resolutions: list[dict],
    beliefs: list[dict],
    patch_context: list[str],
) -> dict | None:
    initial_ids = [item["id"] for item in initial_unknowns if item.get("blocking")]
    if not initial_ids:
        return None
    resolution_ids = [item["unknown_id"] for item in explicit_resolutions]
    missing = [
        unknown_id
        for unknown_id in initial_ids
        if not any(_same_unknown_id(unknown_id, resolved_id) for resolved_id in resolution_ids)
    ]
    if not missing:
        return None
    misplaced = _misplaced_resolution_ids(arguments.get("unknowns"), initial_ids)
    if not _has_resolution_repair_signal(arguments, explicit_resolutions, beliefs, patch_context, misplaced):
        return None
    return {
        "next_step": "repair_findings",
        "missing_resolution_ids": missing,
        "misplaced_resolution_ids": misplaced,
        "instruction": (
            "Reuse existing evidence. Do not call discovery tools. "
            "Call resolve_unknowns with explicit resolutions for these ids."
        ),
    }


def _has_resolution_repair_signal(
    arguments: dict,
    explicit_resolutions: list[dict],
    beliefs: list[dict],
    patch_context: list[str],
    misplaced_resolution_ids: list[str],
) -> bool:
    if explicit_resolutions or beliefs or patch_context or misplaced_resolution_ids:
        return True
    if arguments.get("ready_for_patch_planning"):
        return True
    if str(arguments.get("recommended_next_step") or "").strip() == "patch_planning":
        return True
    if _string_list(arguments.get("user_decisions_required")):
        return True
    return any(
        isinstance(item, dict)
        and (
            str(item.get("status") or "").strip() in {"known", "deferred", "blocked", "resolved"}
            or bool(str(item.get("text") or "").strip())
        )
        for item in arguments.get("task_updates") or []
    )


def _misplaced_resolution_ids(value, initial_ids: list[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    misplaced = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        unknown_id = str(raw.get("unknown_id") or raw.get("id") or "").strip()
        status = str(raw.get("status") or "").strip()
        if (
            any(_same_unknown_id(unknown_id, initial_id) for initial_id in initial_ids)
            and status in {"resolved", "known", "done", "complete", "completed"}
        ):
            misplaced.add(unknown_id)
    return [
        unknown_id
        for unknown_id in initial_ids
        if any(_same_unknown_id(unknown_id, item) for item in misplaced)
    ]


def _resolution_repair_prompt(repair: dict) -> str:
    missing = ", ".join(str(item) for item in repair.get("missing_resolution_ids", []) if str(item).strip())
    misplaced = ", ".join(str(item) for item in repair.get("misplaced_resolution_ids", []) if str(item).strip())
    lines = [
        "Previous finalization needs findings repair, not more investigation.",
        f"Missing explicit resolution ids: {missing or 'none'}",
    ]
    if misplaced:
        lines.append(f"These ids look like resolved answers were written into unknowns instead: {misplaced}")
    lines.extend([
        "Do not call discovery tools or read more files.",
        "Call resolve_unknowns with explicit resolutions for every missing id.",
        "Each resolution must include unknown_id, status, answer, observation_ids or belief_ids, and reason.",
        "Then call finish_investigation again with the same final summary and next step.",
    ])
    return "\n".join(lines)


def _finish_payload(
    arguments: dict,
    *,
    analysis: dict | None = None,
    observations: list[dict] | None = None,
    expected_step: dict | None = None,
    required_items: list[dict] | None = None,
    repair_conflicts: bool = False,
    strict_readiness: bool = False,
    workspace_dir: str = "",
) -> dict:
    repairs: list[str] = []
    explicit_unknowns = _unknowns(arguments.get("unknowns"))
    unknowns = list(explicit_unknowns)
    new_unknowns = [] if _analysis_is_read_only(analysis) else _unknowns(arguments.get("new_unknowns"))
    initial_unknowns = _initial_unknowns(analysis)
    user_decisions = _string_list(arguments.get("user_decisions_required"))
    decision_questions = [_decision_question(item) for item in user_decisions]
    known_unknowns = initial_unknowns + unknowns + new_unknowns
    decision_unknowns = [
        {
            "id": _unknown_id_for_question(question, known_unknowns) or f"D{index}",
            "question": question,
            "blocking": True,
            "resolution_strategy": "clearify",
        }
        for index, question in enumerate(decision_questions, start=1)
    ]
    open_questions = arguments.get("open_questions") if isinstance(arguments.get("open_questions"), list) else []
    open_questions = [
        _decision_question(question) for question in _clean_questions(open_questions)
        if _decision_question(question) not in set(decision_questions)
    ]
    if open_questions and not unknowns and not decision_unknowns:
        unknowns = [
            {
                "id": f"Q{index}",
                "question": question,
                "blocking": True,
                "resolution_strategy": "clearify",
            }
            for index, question in enumerate(_clean_questions(open_questions), start=1)
        ]
    beliefs = _beliefs(arguments.get("beliefs"))
    if repair_conflicts:
        beliefs = _drop_invalid_belief_refs(beliefs, observations or [], repairs)
    else:
        _validate_belief_refs(beliefs, observations or [])
    explicit_resolutions = _resolutions(arguments.get("resolutions"))
    resolutions = list(explicit_resolutions)
    resolutions = _complete_resolutions(resolutions, initial_unknowns, unknowns)
    if repair_conflicts:
        resolutions = _drop_invalid_resolution_refs(resolutions, beliefs, observations or [], repairs, workspace_dir=workspace_dir)
    else:
        _validate_resolution_refs(resolutions, beliefs, observations or [])
    resolutions = _enforce_resolution_evidence(resolutions, initial_unknowns, strict=not repair_conflicts)
    resolutions = _strip_closed_resolution_repair_diagnostics(resolutions)
    unknowns = _drop_resolved_unknowns(unknowns, resolutions, repairs)
    unresolved = _unresolved_from_resolutions(resolutions, initial_unknowns)
    unknowns = _merge_unknowns(unresolved + unknowns + decision_unknowns + new_unknowns)
    patch_context = _patch_context(arguments.get("patch_planning_facts"), repairs, repair_conflicts)
    if not patch_context:
        patch_context = _patch_context(arguments.get("patch_planning_context"), repairs, repair_conflicts)
    model_ready = bool(arguments.get("ready_for_patch_planning"))
    ready = model_ready
    if expected_step and expected_step.get("next_step") == "write_code" and not ready:
        if not repair_conflicts:
            raise ValueError("finish_investigation conflicts with accepted write_code checkpoint")
        ready = True
        for item in unknowns:
            item["blocking"] = False
            item["resolution_strategy"] = "deferred"
        repairs.append("Deferred blockers that conflicted with accepted write_code checkpoint")
    if any(item["blocking"] for item in unknowns):
        if model_ready and any(item["blocking"] for item in explicit_unknowns) and not repair_conflicts:
            raise ValueError("ready_for_patch_planning conflicts with blocking unknowns")
        ready = False
    _require_items_accounted(required_items, arguments.get("task_updates"), resolutions, repair_conflicts)
    unknowns = _resolve_task_update_conflicts(unknowns, arguments.get("task_updates"), repairs, repair_conflicts)
    if not unknowns and repair_conflicts:
        ready = True
    readiness = _runtime_readiness(
        model_ready=ready,
        analysis=analysis,
        initial_unknowns=initial_unknowns,
        resolutions=resolutions,
        unknowns=unknowns,
        patch_context=patch_context,
        finish_arguments=arguments,
    )
    if strict_readiness and model_ready:
        strict_reasons = [
            str(reason)
            for reason in readiness.get("reasons", [])
            if str(reason).startswith("bugfix_readiness:")
        ]
        if strict_reasons:
            raise ValueError(
                "bugfix_readiness is required when ready_for_patch_planning is true: "
                + ", ".join(strict_reasons)
            )
    bugfix_reasons = [
        str(reason)
        for reason in readiness.get("reasons", [])
        if str(reason).startswith("bugfix_readiness:")
    ]
    bugfix_readiness_state = None
    if bugfix_reasons:
        checks = {}
        for reason in bugfix_reasons:
            field = str(reason).replace("bugfix_readiness:", "").strip()
            checks[field] = False
        bugfix_readiness_state = {
            "gate": "bugfix_readiness",
            "status": "not_ready",
            "checks": checks,
            "reasons": bugfix_reasons,
        }
    ready = readiness["ready"]
    hard_readiness_reasons = [
        reason for reason in readiness.get("reasons", [])
        if (
            reason.endswith(":not_resolved")
            or reason.endswith(":missing_evidence")
        )
    ]
    if not ready and model_ready and patch_context and not any(
        item.get("blocking") and item.get("resolution_strategy") == "clearify"
        for item in unknowns
    ) and not hard_readiness_reasons:
        ready = True
        readiness = {**readiness, "ready": True, "runtime_override": "patch_facts_present"}
        for item in unknowns:
            if item.get("blocking"):
                item["blocking"] = False
                item["resolution_strategy"] = "deferred"
        repairs.append("Allowed patch planning from grounded patch facts and deferred remaining investigate_project unknowns")
    repair_request = _resolution_repair_request(
        arguments,
        initial_unknowns,
        explicit_resolutions,
        beliefs,
        patch_context,
    )
    final = {
        "summary": str(arguments.get("summary") or "").strip(),
        "ready_for_patch_planning": ready,
        "beliefs": beliefs,
        "open_questions": open_questions,
        "resolutions": resolutions,
        "new_unknowns": new_unknowns,
        "user_decisions_required": user_decisions,
        "unknowns": unknowns,
        "bugfix_readiness_state": bugfix_readiness_state,
        "task_updates": _investigation_task_updates(
            arguments.get("task_updates"),
            initial_unknowns + unknowns,
            resolutions,
        ),
        "patch_planning_context": patch_context,
        "patch_planning_facts": patch_context,
        "recommended_next_step": str(arguments.get("recommended_next_step") or "").strip(),
        "readiness": readiness,
        "protocol_repairs": repairs,
    }
    final["project_facts"] = _investigation_project_facts(
        beliefs,
        resolutions,
        analysis,
    )
    if repair_request:
        final["resolution_repair"] = repair_request
    return final


def _strip_submitted_repair_diagnostics(arguments: dict) -> dict:
    """record 时剥离模型提交的 repair 诊断字段（repair_mode/semantic_missing）。

    这些字段是 audit 质量门的输出标记，模型在 REPAIR 阶段会从上下文把上一轮
    的 missing 原样抄进自己提交的 resolution。若不清理，merge 后
    _semantic_repair_resolution_ids 看到 repair_mode 就永远判该 unknown 待修，
    即使模型已补齐证据，也会无限 REPAIR（U4 类死循环根因）。
    """
    cleaned = dict(arguments)
    resolutions = cleaned.get("resolutions")
    if isinstance(resolutions, list):
        cleaned["resolutions"] = [
            (
                {key: value for key, value in item.items() if key not in {"repair_mode", "semantic_missing"}}
                if isinstance(item, dict)
                else item
            )
            for item in resolutions
        ]
    return cleaned


def _strip_closed_resolution_repair_diagnostics(resolutions: list[dict]) -> list[dict]:
    cleaned = []
    for resolution in resolutions:
        if not isinstance(resolution, dict):
            continue
        item = dict(resolution)
        if item.get("status") in {"resolved", "deferred"}:
            item.pop("repair_mode", None)
            item.pop("semantic_missing", None)
        cleaned.append(item)
    return cleaned


def _investigation_project_facts(
    beliefs: list[dict],
    resolutions: list[dict],
    analysis: dict | None,
) -> list[dict]:
    unknowns = {
        str(item.get("id") or "").strip(): item
        for item in (analysis or {}).get("unknowns", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    belief_by_id = {
        str(item.get("id") or "").strip(): item
        for item in beliefs
        if str(item.get("id") or "").strip()
    }
    facts = []
    seen = set()
    for resolution in resolutions:
        if resolution.get("status") != "resolved" or not resolution.get("answer"):
            continue
        unknown_id = str(resolution.get("unknown_id") or "").strip()
        evidence_ids = list(resolution.get("evidence", []))
        for belief_id in resolution.get("belief_ids", []):
            evidence_ids.extend(belief_by_id.get(belief_id, {}).get("evidence", []))
        text = str(resolution["answer"]).strip()
        user_decision = _is_user_product_decision(resolution, list(unknowns.values()))
        facts.append({
            "id": f"PF{len(facts) + 1}",
            "text": text,
            "authority": (
                "user_explicit"
                if user_decision
                else "verified_fact"
            ),
            "unknown_ids": [unknown_id] if unknown_id else [],
            "acceptance_criteria_ids": list(
                unknowns.get(unknown_id, {}).get("acceptance_criteria_ids", [])
            ),
            "evidence_ids": _dedupe_strings(evidence_ids),
            "belief_ids": list(resolution.get("belief_ids", [])),
        })
        seen.add(" ".join(text.split()).casefold())
    for belief in beliefs:
        text = str(belief.get("statement") or "").strip()
        normalized = " ".join(text.split()).casefold()
        if (
            not text
            or normalized in seen
            or belief.get("status") not in {"supported", "strongly_supported", "runtime_confirmed"}
            or not belief.get("evidence")
        ):
            continue
        facts.append({
            "id": f"PF{len(facts) + 1}",
            "text": text,
            "authority": "verified_fact",
            "unknown_ids": [],
            "acceptance_criteria_ids": [],
            "evidence_ids": list(belief.get("evidence", [])),
            "belief_ids": [belief.get("id", "")],
        })
        seen.add(normalized)
    return facts


def _empty_recorded_findings() -> dict:
    return {field: [] for field in FINDING_FIELDS}


def _continued_recorded_findings(previous: dict | None, observations: list[dict]) -> dict:
    recorded = _merge_recorded_findings(_empty_recorded_findings(), previous or {})
    observation_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    by_tail = {
        item.rsplit(":", 1)[-1]: item
        for item in observation_ids
    }
    for field in ("beliefs", "resolutions"):
        recorded[field] = [
            dict(item) if isinstance(item, dict) else item
            for item in recorded[field]
        ]
        for item in recorded[field]:
            if not isinstance(item, dict):
                continue
            evidence = item.get("evidence")
            if isinstance(evidence, list):
                item["evidence"] = [
                    value if value in observation_ids else by_tail.get(value, value)
                    for value in (str(raw).strip() for raw in evidence)
                    if value
                ]
    return recorded


def _apply_investigation_audit(
    recorded: dict,
    audit: dict,
    *,
    observations: list[dict] | None = None,
    strict_grounding: bool = True,
    allow_verification: bool = True,
    analysis: dict | None = None,
) -> tuple[dict, list[dict], dict[str, str]]:
    result = {field: list(recorded.get(field, [])) for field in FINDING_FIELDS}
    beliefs = [dict(item) for item in result["beliefs"] if isinstance(item, dict)]
    result["beliefs"] = beliefs
    for belief in beliefs:
        if belief.get("status") not in {"supported", "strongly_supported", "runtime_confirmed"}:
            continue
        unsupported = (
            _unsupported_grounding_literals(
                {
                    "answer": belief.get("statement", ""),
                    "evidence": belief.get("evidence", []),
                },
                _empty_recorded_findings(),
                observations or [],
            )
            if strict_grounding
            else []
        )
        supporting_ids = _supporting_observation_ids(unsupported, observations or [])
        if supporting_ids:
            belief["evidence"] = _dedupe_strings([
                *_reference_list(belief.get("evidence")),
                *supporting_ids,
            ])
            unsupported = _unsupported_grounding_literals(
                {
                    "answer": belief.get("statement", ""),
                    "evidence": belief.get("evidence", []),
                },
                _empty_recorded_findings(),
                observations or [],
            )
        if unsupported:
            belief["status"] = "unverified"
    resolutions = [dict(item) for item in result["resolutions"] if isinstance(item, dict)]
    verification_requests = []
    clearify_questions: dict[str, str] = {}
    for verdict in audit.get("verdicts", []):
        if not isinstance(verdict, dict):
            continue
        unknown_id = str(verdict.get("unknown_id") or "").strip()
        resolution = _find_by_unknown_id(resolutions, unknown_id)
        if (
            resolution is None
            or resolution.get("reason") == CLEARIFY_RESOLUTION_REASON
        ):
            continue
        status = str(verdict.get("status") or "").strip()
        reason = str(verdict.get("reason") or "").strip()
        if status == "grounded":
            unsupported = (
                _grounding_unsupported_for_resolution(
                    resolution,
                    result,
                    observations or [],
                )
                if strict_grounding
                else []
            )
            # Derived inferences may reference runtime paths and identifiers
            # that cannot appear as literals in source code (e.g. .venv/Scripts/stratumcode,
            # python.exe). Skip unsupported-literal check for derived inferences — they are
            # by definition the model's synthesis, not direct source quotes.
            # _grounding_unsupported_for_resolution 内部已同时豁免 derived_inference
            # 与 absence（否定性结论）两类。
            supporting_ids = _supporting_observation_ids(unsupported, observations or [])
            if supporting_ids:
                resolution["evidence"] = _dedupe_strings([
                    *_reference_list(resolution.get("evidence")),
                    *supporting_ids,
                ])
                unsupported = _grounding_unsupported_for_resolution(
                    resolution,
                    result,
                    observations or [],
                )
            missing_state_writes = (
                _missing_grounding_state_writes(
                    resolution,
                    result,
                    observations or [],
                )
                if strict_grounding
                else []
            )
            if not unsupported and not missing_state_writes:
                spans = _resolution_grounding_evidence_spans(
                    resolution,
                    result,
                    observations or [],
                )
                if spans:
                    resolution["grounding_evidence_spans"] = spans
                resolution["status"] = "resolved"
                resolution.pop("repair_mode", None)
                resolution.pop("semantic_missing", None)
                if reason:
                    resolution["reason"] = reason
                continue
            status = "investigate"
            if unsupported:
                reason = GROUNDING_LITERAL_REASON_PREFIX + " " + ", ".join(unsupported)
            else:
                reason = STATE_WRITE_REASON_PREFIX + " " + ", ".join(missing_state_writes)
        if status == "verify":
            hypothesis = str(verdict.get("hypothesis") or "").strip()
            if hypothesis and allow_verification:
                resolution["status"] = "partially_resolved"
                resolution["reason"] = reason
                resolution.pop("repair_mode", None)
                resolution.pop("semantic_missing", None)
                verification_requests.append({
                    "unknown_id": unknown_id,
                    "hypothesis": hypothesis,
                    "reason": reason,
                })
                continue
            status = "investigate"
        if status == "clearify":
            question = str(verdict.get("question") or "").strip()
            if question:
                resolution["status"] = "needs_clearify"
                resolution["reason"] = reason
                resolution.pop("repair_mode", None)
                resolution.pop("semantic_missing", None)
                clearify_questions[unknown_id] = question
                continue
            status = "investigate"
        missing = _semantic_missing_items(verdict.get("missing"))
        original_missing = missing
        # read_only 模式下，"运行时证据"（测试/日志/复现/实际运行表现）按设计
        # 不可得。审计模型若要求这类证据，直接移除——代码路径推断 + 明确声明
        # 无运行时证据，就是该 unknown 在只读调查中的最终答案，不应进入 REPAIR。
        if missing and _analysis_is_read_only(analysis):
            missing = [
                item
                for item in missing
                if not _RUNTIME_EVIDENCE_RE.search(str(item.get("requirement") or ""))
            ]
        # 语义门禁打回时无条件进入 append_missing_only 修复：
        # 不依赖 verdict 是否带 repair_mode 字段（audit 模型常缺失该字段，
        # 缺失时旧实现 pop 掉 repair_mode，导致下一轮 repair_ids 为空、
        # 主循环误入 FINISH 分支、模型 record 被 already_resolved 拦截
        # 的三面夹击死锁——d5eef05a）。
        if missing:
            resolution["semantic_missing"] = missing
            resolution["repair_mode"] = "append_missing_only"
        else:
            resolution.pop("repair_mode", None)
            resolution.pop("semantic_missing", None)
        if missing:
            resolution["status"] = "partially_resolved"
        elif original_missing and _analysis_is_read_only(analysis):
            # 缺失要求全部是"运行时证据"类且被只读约束过滤：代码路径推断即最终答案
            resolution["status"] = "resolved"
        else:
            resolution["status"] = "partially_resolved"
        resolution["reason"] = (
            reason
            or _semantic_missing_reason(missing)
            or "The semantic audit found insufficient evidence."
        )
    result["resolutions"] = resolutions
    return result, verification_requests, clearify_questions


def _semantic_missing_reason(missing: list[dict]) -> str:
    requirements = [
        str(item.get("requirement") or "").strip()
        for item in missing
        if isinstance(item, dict) and str(item.get("requirement") or "").strip()
    ]
    return "Missing semantic requirements: " + "; ".join(requirements) if requirements else ""


def _resolution_requires_semantic_audit(resolution: dict, initial_unknowns: list[dict]) -> bool:
    if _is_user_product_decision(resolution, initial_unknowns):
        return False
    if _is_engineering_decision_resolution(resolution, initial_unknowns):
        return False
    if str(resolution.get("kind") or "derived_inference") not in SEMANTIC_AUDIT_KINDS:
        return False
    return (
        str(resolution.get("status") or "") in {"resolved", "partially_resolved"}
        and str(resolution.get("answer") or "").strip()
        and str(resolution.get("unknown_id") or "").strip()
    )


def _apply_direct_resolution_gate(
    recorded: dict,
    observations: list[dict],
    *,
    strict_grounding: bool = True,
) -> dict:
    direct_ids = [
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("kind") or "") == "direct_fact"
        and str(item.get("status") or "") == "resolved"
        and str(item.get("unknown_id") or "").strip()
    ]
    if not direct_ids:
        return recorded
    gated, _, _ = _apply_investigation_audit(
        recorded,
        {"verdicts": [
            {
                "unknown_id": unknown_id,
                "status": "grounded",
                "reason": "Direct fact passed deterministic grounding checks.",
            }
            for unknown_id in direct_ids
        ]},
        observations=observations,
        strict_grounding=strict_grounding,
        allow_verification=False,
    )
    return gated


def _resolution_is_absence_claim(resolution: dict, observations: list[dict]) -> bool:
    """否定性结论（absence）判定：答案声称某物"不存在/未找到/未定义"，
    且引用了至少一条观察（grep/glob 无命中、读取文件确认缺失等）。

    absence 无法作为代码字面量被观察引用，质量门若仍要求字面量逐字命中，
    这类结论会永远判缺、触发 REPAIR 死循环（见 U2 类"预期未定义"问题）。
    """
    answer = str(resolution.get("answer") or "")
    if not _NEGATIVE_CLAIM_RE.search(answer):
        return False
    evidence_ids = set(_reference_list(resolution.get("evidence")))
    return any(
        isinstance(item, dict)
        and str(item.get("id") or "") in evidence_ids
        for item in observations or []
    )


def _grounding_unsupported_for_resolution(
    resolution: dict,
    recorded: dict,
    observations: list[dict],
) -> list[str]:
    """计算 resolution 未获观察支撑的代码字面量，带两类豁免：

    - derived_inference：推断本来就是模型的综合，不要求逐字引源码；
    - absence（否定性结论）：声称"不存在"的字面量无法在源码里被观察到。
    """
    unsupported = _unsupported_grounding_literals(resolution, recorded, observations or [])
    if unsupported and (
        str(resolution.get("kind") or "") == "derived_inference"
        or _resolution_is_absence_claim(resolution, observations or [])
    ):
        return []
    return unsupported


_PYTHON_STDLIB_MODULES = frozenset({
    "abc", "argparse", "array", "ast", "asyncio", "base64", "binascii", "bisect",
    "builtins", "bz2", "cmath", "codecs", "collections", "concurrent", "contextlib",
    "copy", "copyreg", "csv", "ctypes", "dataclasses", "datetime", "decimal",
    "difflib", "email", "enum", "errno", "functools", "glob", "gzip", "hashlib",
    "heapq", "hmac", "html", "http", "importlib", "inspect", "io", "itertools",
    "json", "logging", "lzma", "math", "mmap", "multiprocessing", "operator", "os",
    "pathlib", "pickle", "platform", "pprint", "queue", "random", "re", "sched",
    "secrets", "select", "selectors", "shutil", "signal", "socket", "sqlite3",
    "ssl", "stat", "statistics", "string", "struct", "subprocess", "sys",
    "tempfile", "textwrap", "threading", "time", "traceback", "types", "typing",
    "unicodedata", "unittest", "urllib", "uuid", "warnings", "weakref", "xml",
    "zipfile", "zlib", "zoneinfo",
})


def _is_python_stdlib_module(name: str) -> bool:
    """标准库判断：优先用运行时权威列表（sys.stdlib_module_names，
    Python 3.10+ 内置、自动跟随版本），手写列表只作兜底。"""
    return name in _PYTHON_STDLIB_MODULES or name in getattr(sys, "stdlib_module_names", ())


# 其他语言的框架/语言级根命名空间：与 Python 标准库同理，它们是语言环境
# 的一部分，项目里没有对应源文件，grep/read 永远产生不了这些引用
# （System.Math.Sqrt / UnityEngine.Debug.Log / java.lang.Math.sqrt /
# console.log / std::vector / fmt.Println 等）。模型在答案里引用它们
# 是"计划使用框架 API"，不是"声称项目里有这段代码"，无需观察证据。
_FRAMEWORK_ROOTS = frozenset({
    # .NET / C# / F# / VB
    "System", "Microsoft", "Windows", "System.Runtime",
    # Unity（C# 变种：引擎级命名空间）
    "UnityEngine", "UnityEditor",
    # Java / Kotlin / JVM
    "java", "javax", "jakarta", "jdk", "kotlin",
    # Rust
    "std", "core", "alloc",
    # C / C++（std:: 已被提取规则跳过，这里兜底 boost 等）
    "boost", "glib",
    # Go 标准库包
    "bufio", "bytes", "container", "crypto", "database", "encoding", "errors",
    "flag", "fmt", "hash", "html", "image", "index", "io", "log", "math", "mime",
    "net", "os", "path", "reflect", "regexp", "runtime", "sort", "strconv",
    "strings", "sync", "syscall", "testing", "text", "time", "unicode",
    "unsafe",
    # JavaScript / TypeScript 内置全局
    "Array", "BigInt", "Date", "Intl", "JSON", "Map", "Math", "Number",
    "Object", "Promise", "Proxy", "Reflect", "RegExp", "Set", "String",
    "Symbol", "WeakMap", "WeakSet", "console", "fetch", "globalThis",
    "navigator", "window", "document", "WebSocket",
    # PHP
    "PHP", "Spl",
})


def _is_framework_module(name: str) -> bool:
    """非 Python 语言/框架级根命名空间判断。"""
    return name in _FRAMEWORK_ROOTS


def _unsupported_grounding_literals(
    resolution: dict,
    recorded: dict,
    observations: list[dict],
) -> list[str]:
    evidence_ids = set(_reference_list(resolution.get("evidence")))
    belief_ids = set(_reference_list(resolution.get("belief_ids")))
    for belief in recorded.get("beliefs", []):
        if isinstance(belief, dict) and str(belief.get("id") or "") in belief_ids:
            evidence_ids.update(_reference_list(belief.get("evidence")))
    evidence_obs = [
        item
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "") in evidence_ids
    ]
    evidence = "\n".join(_grounding_observation_text(item) for item in evidence_obs)
    normalized_evidence = re.sub(r"\s+", "", evidence)
    literals = _grounding_code_literals(str(resolution.get("answer") or ""))
    unsupported = []
    for literal in literals:
        if not _is_grounding_code_literal(literal):
            continue
        normalized_literal = re.sub(r"\s+", "", literal)
        if normalized_literal in normalized_evidence:
            continue
        # 模块引用写法（useSessions.open / model_settings.resolve）豁免：
        # 源码里是 `export async function open` / `def resolve`，点链字符串
        # 本身不会出现在观察文本里。只要观察覆盖了对应源文件（path 命中
        # 模块名），就认为该引用已 grounded——否则模型永远补不出这个字面量，
        # REPAIR 死循环（useSessions.open 类根因）。
        if "." in literal:
            module = literal.split(".")[0]
            # 先查项目内同名源文件：项目真有这个模块（如项目自己的 utils.py
            # 或 System.cs）→ 模块文件豁免（类5），不进入框架判断。
            if _observation_covers_module(evidence_obs, module):
                continue
            # 语言/框架级根引用（cmath.sqrt / System.Math.Sqrt /
            # UnityEngine.Debug.Log / java.lang.Math.sqrt / console.log）：
            # 语言环境的一部分，项目里没有对应源文件，grep/read 永远
            # 产生不了这个字面量。要求观察证据毫无意义，直接放行。
            if _is_python_stdlib_module(module) or _is_framework_module(module):
                continue
            # LSP 兜底：框架列表没覆盖的（冷门语言/新框架），问 LSP 符号
            # 在项目里有没有定义。只有 server 正常返回"查不到"（NOT_FOUND）
            # 才按框架/外部引用豁免——这是可信信号。UNAVAILABLE（自动安装+
            # 退避重试后仍不可用）不豁免：宁可要求证据等环境修复，也不能把
            # 未观察的项目代码引用误放行（同名文件层只兜底被观察过的文件）。
            lsp_anchor = next(
                (item for item in evidence_obs if item.get("path")),
                None,
            )
            if lsp_anchor is not None:
                workspace = _infer_workspace_root(str(lsp_anchor.get("path") or ""))
                if workspace:
                    status = _lsp_definition_file_typed(
                        str(lsp_anchor.get("path")),
                        module,
                        workspace,
                    )
                    if status == LSP_DEFINITION_NOT_FOUND:
                        continue
        unsupported.append(literal)
    return _dedupe_strings(unsupported)


def _infer_workspace_root(path: str) -> str:
    """从文件路径向上找项目根（含 .git 或常见项目标记文件的目录）。"""
    current = os.path.dirname(os.path.abspath(path))
    markers = (".git", "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
               "*.csproj", "*.sln", "pom.xml", "build.gradle", "composer.json",
               "Gemfile", "mix.exs")
    while True:
        if any(
            os.path.exists(os.path.join(current, marker))
            or (marker.startswith("*.") and any(
                os.path.exists(os.path.join(current, name))
                for name in os.listdir(current)
                if name.endswith(marker[1:])
            ))
            for marker in markers
        ):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return ""
        current = parent


def _observation_covers_module(observations: list[dict], module: str) -> bool:
    """观察的 path 是否覆盖了给定模块（useSessions -> useSessions.js）。"""
    if not module:
        return False
    for item in observations:
        path = str(item.get("path") or "")
        if not path:
            continue
        base = os.path.basename(path)
        stem = os.path.splitext(base)[0]
        if module in (stem, base):
            return True
    return False


def _semantic_repair_resolution_ids(recorded: dict) -> set[str]:
    return {
        str(item.get("unknown_id") or "")
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and (
            item.get("repair_mode") == "append_missing_only"
            or (
                item.get("status") == "partially_resolved"
                and str(item.get("reason") or "").startswith((
                    GROUNDING_LITERAL_REASON_PREFIX,
                    STATE_WRITE_REASON_PREFIX,
                ))
            )
        )
        and str(item.get("unknown_id") or "")
    }


def _semantic_repair_payload(recorded: dict, unknown_ids: set[str]) -> dict:
    target_ids = {
        _normalize_unknown_id(item)
        for item in unknown_ids
        if str(item).strip()
    }
    resolutions = [
        item for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and _normalize_unknown_id(str(item.get("unknown_id") or "")) in target_ids
    ]
    recorded_ids = _dedupe_strings([
        belief_id
        for resolution in resolutions
        for belief_id in _reference_list(resolution.get("belief_ids"))
    ])
    missing = [
        missing_item
        for resolution in resolutions
        for missing_item in _semantic_missing_items(resolution.get("semantic_missing"))
    ]
    if not missing:
        missing = []
        for resolution in resolutions:
            _reason = str(resolution.get("reason") or "").strip()
            if _reason.startswith(GROUNDING_LITERAL_REASON_PREFIX):
                _literal = _reason[len(GROUNDING_LITERAL_REASON_PREFIX):].strip()
                _reason = f"Find and cite the exact code observation that contains: {_literal}" if _literal else "Cite the exact code observation that contains the claimed code literal."
            elif _reason.startswith(STATE_WRITE_REASON_PREFIX):
                _writes = _reason[len(STATE_WRITE_REASON_PREFIX):].strip()
                _reason = f"Account for the following observed state writes in the resolution: {_writes}" if _writes else "Account for observed state writes in the resolution."
            if not _reason:
                _reason = "Add the missing semantic support."
            missing.append({
                "acceptance_id": "",
                "requirement": _reason,
            })
    return {
        "accepted": False,
        "recorded_ids": recorded_ids,
        "missing": missing,
        "repair_mode": "append_missing_only",
    }


def _grounding_code_literals(value: str) -> list[str]:
    quoted = [
        literal
        for literal in re.findall(r"`([^`\r\n]+)`", value)
        if len(literal) <= 80
        and "..." not in literal
        and not literal.lstrip().startswith("<")
    ]
    return [
        literal
        for literal in _dedupe_strings([
            *quoted,
            *re.findall(
                r"""\bv-(?:if|show|for|else-if|else|bind|on|model|html|text|slot|pre|cloak|once|memo)"""
                r"""(?:\s*=\s*(?:"[^"\r\n]*"|'[^'\r\n]*'))?""",
                value,
            ),
            *re.findall(
                r"(?<![-A-Za-z0-9_$])[a-z_$][A-Za-z0-9_$]*"
                r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+"
                r"(?![-A-Za-z0-9_$])",
                value,
            ),
        ])
        if not _code_literal_is_negated(value, literal)
    ]


def _code_literal_is_negated(value: str, literal: str) -> bool:
    positions = [match.start() for match in re.finditer(re.escape(literal), value)]
    return bool(positions) and all(
        re.search(
            r"(?:非|无|没有|不存在|\bnot|\bno)\s*[\(（]?\s*$",
            value[max(0, index - 12):index].casefold(),
        )
        for index in positions
    )


def _bind_grounding_evidence(recorded: dict, observations: list[dict]) -> dict:
    for field, text_field in (("beliefs", "statement"), ("resolutions", "answer")):
        for item in recorded.get(field, []):
            if not isinstance(item, dict):
                continue
            supporting_ids = _supporting_observation_ids(
                _grounding_code_literals(str(item.get(text_field) or "")),
                observations,
            )
            if supporting_ids:
                item["evidence"] = _dedupe_strings([
                    *_reference_list(item.get("evidence")),
                    *supporting_ids,
                ])
    return recorded


def _supporting_observation_ids(
    literals: list[str],
    observations: list[dict],
) -> list[str]:
    if not literals:
        return []
    result = []
    for literal in literals:
        normalized = re.sub(r"\s+", "", literal)
        matches = [
            str(item.get("id") or "")
            for item in observations
            if isinstance(item, dict)
            and str(item.get("id") or "")
            and normalized in re.sub(
                r"\s+",
                "",
                _grounding_observation_text(item),
            )
        ]
        if not matches:
            return []
        result.extend(matches)
    return _dedupe_strings(result)


def _grounding_observation_text(observation: dict) -> str:
    return "\n".join(
        text for field in ("path", "_grounding_evidence", "evidence_excerpt", "summary")
        if (text := str(observation.get(field) or "").strip())
    )


def _observation_context_view(
    observation: dict,
    literals: list[str] | None = None,
) -> dict:
    view = {
        "id": observation.get("id", ""),
        "tool": observation.get("tool", ""),
        "title": observation.get("title", ""),
        "summary": observation.get("summary", ""),
        "evidence_excerpt": observation.get("evidence_excerpt", ""),
        "verification": observation.get("verification", {}),
        "target_unknown_ids": observation.get("target_unknown_ids", []),
        "reason": observation.get("reason", ""),
        "path": observation.get("path", ""),
    }
    spans = _observation_grounding_literal_spans(observation, literals or [])
    if spans:
        view["literal_evidence_spans"] = spans
    return view


def _resolution_grounding_evidence_spans(
    resolution: dict,
    recorded: dict,
    observations: list[dict],
) -> list[dict]:
    literals = [
        literal
        for literal in _grounding_code_literals(str(resolution.get("answer") or ""))
        if _is_grounding_code_literal(literal)
    ]
    if not literals:
        return []
    evidence_ids = set(_reference_list(resolution.get("evidence")))
    belief_ids = set(_reference_list(resolution.get("belief_ids")))
    for belief in recorded.get("beliefs", []):
        if isinstance(belief, dict) and str(belief.get("id") or "") in belief_ids:
            evidence_ids.update(_reference_list(belief.get("evidence")))
    spans = [
        span
        for observation in observations
        if isinstance(observation, dict)
        and str(observation.get("id") or "") in evidence_ids
        for span in _observation_grounding_literal_spans(observation, literals)
    ]
    return spans[:GROUNDING_LITERAL_SPAN_MAX_ITEMS]


def _observation_grounding_literal_spans(
    observation: dict,
    literals: list[str],
) -> list[dict]:
    evidence = str(
        observation.get("_grounding_evidence")
        or observation.get("evidence_excerpt")
        or observation.get("summary")
        or ""
    )
    if not evidence or not literals:
        return []
    observation_id = str(observation.get("id") or "")
    path = str(observation.get("path") or "")
    spans = []
    for literal, start, end in _literal_line_ranges(evidence, literals):
        excerpt = _format_line_span_excerpt(evidence, start, end, [literal])
        if not excerpt:
            continue
        span = {
            "observation_id": observation_id,
            "literal": literal,
            "line_start": start,
            "line_end": end,
            "excerpt": excerpt,
        }
        if path:
            span["path"] = path
        spans.append(span)
        if len(spans) >= GROUNDING_LITERAL_SPAN_MAX_ITEMS:
            break
    return spans


def _literal_line_ranges(evidence: str, literals: list[str]) -> list[tuple[str, int, int]]:
    lines = evidence.splitlines() or [evidence]
    ranges: list[tuple[str, int, int]] = []
    seen: set[tuple[str, int, int]] = set()
    normalized_lines = [re.sub(r"\s+", "", line) for line in lines]
    for literal in _dedupe_strings(literals):
        normalized = re.sub(r"\s+", "", literal)
        if not normalized:
            continue
        for index, line in enumerate(normalized_lines):
            if normalized in line:
                start, end = _context_line_range(index, index, len(lines))
                key = (literal, start, end)
                if key not in seen:
                    ranges.append(key)
                    seen.add(key)
                break
        else:
            for index in range(len(lines)):
                window_end = min(len(lines), index + GROUNDING_LITERAL_SPAN_MAX_LINES)
                window = "".join(normalized_lines[index:window_end])
                if normalized not in window:
                    continue
                start, end = _context_line_range(index, window_end - 1, len(lines))
                key = (literal, start, end)
                if key not in seen:
                    ranges.append(key)
                    seen.add(key)
                break
    return ranges


def _context_line_range(hit_start: int, hit_end: int, total_lines: int) -> tuple[int, int]:
    return (
        max(1, hit_start + 1 - GROUNDING_LITERAL_SPAN_CONTEXT_LINES),
        min(total_lines, hit_end + 1 + GROUNDING_LITERAL_SPAN_CONTEXT_LINES),
    )


def _format_line_span_excerpt(
    evidence: str,
    line_start: int,
    line_end: int,
    literals: list[str],
) -> str:
    lines = evidence.splitlines() or [evidence]
    selected = lines[line_start - 1:line_end]
    return "\n".join(
        f"L{line_start + offset}: {_trim_grounding_span_line(line, literals)}"
        for offset, line in enumerate(selected)
    )


def _trim_grounding_span_line(line: str, literals: list[str]) -> str:
    text = str(line)
    if len(text) <= GROUNDING_LITERAL_SPAN_MAX_LINE_CHARS:
        return text
    for literal in literals:
        if not literal:
            continue
        index = text.find(literal)
        if index < 0:
            continue
        half = max(1, (GROUNDING_LITERAL_SPAN_MAX_LINE_CHARS - len(literal)) // 2)
        start = max(0, index - half)
        end = min(len(text), index + len(literal) + half)
        prefix = "..." if start else ""
        suffix = "..." if end < len(text) else ""
        return f"{prefix}{text[start:end]}{suffix}"
    half = GROUNDING_LITERAL_SPAN_MAX_LINE_CHARS // 2
    return f"{text[:half]}...{text[-half:]}"


def _missing_grounding_state_writes(
    resolution: dict,
    recorded: dict,
    observations: list[dict],
) -> list[str]:
    answer = str(resolution.get("answer") or "")
    state_ids = _assigned_state_ids(answer)
    if not state_ids:
        return []
    evidence_ids = set(_reference_list(resolution.get("evidence")))
    belief_ids = set(_reference_list(resolution.get("belief_ids")))
    for belief in recorded.get("beliefs", []):
        if isinstance(belief, dict) and str(belief.get("id") or "") in belief_ids:
            evidence_ids.update(_reference_list(belief.get("evidence")))
    evidence = "\n".join(
        _grounding_observation_text(item)
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "") in evidence_ids
    )
    normalized_answer = re.sub(r"\s+", "", answer)
    writes = []
    for state_id in state_ids:
        writes.extend(re.findall(
            re.escape(state_id)
            + r"\s*(?:(?:\?\?=|&&=|\|\|=|\+=|-=|\*=|/=|%=|=)"
            + r"\s*[^;\r\n}]+|\+\+|--)",
            evidence,
        ))
    return _dedupe_strings([
        write.strip()
        for write in writes
        if re.sub(r"\s+", "", write) not in normalized_answer
    ])


def _assigned_state_ids(value: str) -> list[str]:
    return _dedupe_strings([
        match.group(1)
        for match in re.finditer(
            r"(?<![@:A-Za-z0-9_$-])"
            r"([a-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+)"
            r"\s*(?=\?\?=|&&=|\|\|=|\+=|-=|\*=|/=|%=|=(?!=)|\+\+|--)",
            value,
        )
    ])


def _recorded_findings_signature(recorded: dict) -> str:
    beliefs = [
        {
            "key": _belief_identity_key(item),
            "evidence": sorted(_reference_list(item.get("evidence"))),
            "status": str(item.get("status") or "").strip(),
        }
        for item in _beliefs(recorded.get("beliefs"))
    ]
    resolutions = [
        {
            "unknown_id": _normalize_unknown_id(item.get("unknown_id")),
            "status": str(item.get("status") or "").strip(),
            "evidence": sorted(_reference_list(item.get("evidence"))),
            "belief_ids": sorted(_reference_list(item.get("belief_ids"))),
        }
        for item in _resolutions(recorded.get("resolutions"))
    ]
    unknowns = [
        {
            "id": _normalize_unknown_id(item.get("id")),
            "status": str(item.get("status") or "").strip(),
            "strategy": str(item.get("resolution_strategy") or "").strip(),
        }
        for item in _unknowns(recorded.get("unknowns")) + _unknowns(recorded.get("new_unknowns"))
    ]
    return json.dumps(
        {
            "beliefs": sorted(beliefs, key=lambda item: item["key"]),
            "resolutions": sorted(resolutions, key=lambda item: item["unknown_id"]),
            "unknowns": sorted(unknowns, key=lambda item: item["id"]),
            "decisions": sorted(_string_list(recorded.get("user_decisions_required"))),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _final_observations(
    observations: list[dict],
    *,
    preserve_grounding_evidence: bool,
) -> list[dict]:
    if preserve_grounding_evidence:
        return [dict(item) for item in observations]
    return [
        {key: value for key, value in item.items() if key != "_grounding_evidence"}
        for item in observations
    ]


def _is_grounding_code_literal(value: str) -> bool:
    if value.casefold().endswith((
        ".cfg", ".css", ".html", ".ini", ".js", ".json", ".jsx", ".lock",
        ".md", ".py", ".toml", ".ts", ".tsx", ".txt", ".vue", ".yaml", ".yml",
    )):
        return False
    return value.startswith(("v-if", "v-show", "@")) or any(
        marker in value
        for marker in ("=", "<", ">", "(", ")", "{", "}", "[", "]", "/", "\\")
    ) or bool(
        re.fullmatch(
            r"[a-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+",
            value,
        )
    )


def _audit_covers_resolutions(
    audit: dict,
    recorded: dict,
    analysis: dict | None = None,
) -> bool:
    initial_unknowns = _initial_unknowns(analysis)
    resolved_ids = [
        str(item.get("unknown_id") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and _resolution_requires_semantic_audit(item, initial_unknowns)
    ]
    audited_ids = [
        str(item.get("unknown_id") or "").strip()
        for item in audit.get("verdicts", [])
        if isinstance(item, dict) and str(item.get("unknown_id") or "").strip()
    ]
    return all(any(_same_unknown_id(resolved_id, audited_id) for audited_id in audited_ids) for resolved_id in resolved_ids)


def _merge_recorded_findings(current: dict, update: dict) -> dict:
    merged = {field: list(current.get(field, [])) for field in FINDING_FIELDS}
    belief_aliases: dict[str, str] = {}
    if isinstance(update.get("beliefs"), list):
        merged["beliefs"], belief_aliases = _merge_beliefs_by_identity(
            merged["beliefs"],
            update["beliefs"],
        )
    for field in FINDING_FIELDS:
        if field == "beliefs":
            continue
        value = update.get(field)
        if isinstance(value, list):
            if field == "resolutions" and belief_aliases:
                value = [_remap_resolution_belief_ids(item, belief_aliases) for item in value]
            merged[field] = _merge_list_by_identity(merged[field], value)
            if field == "resolutions":
                # clearify 用户答案是权威决定：覆盖同 unknown 的先前模型自解析，
                # 避免 answers 里两条矛盾答案并存（重复 clearify 的根源）。
                merged[field] = _supersede_resolutions_with_clearify(merged[field])
    return merged


def _supersede_resolutions_with_clearify(resolutions: list[dict]) -> list[dict]:
    clearify_ids = {
        str(item.get("unknown_id") or "").strip()
        for item in resolutions
        if isinstance(item, dict) and str(item.get("reason") or "") == CLEARIFY_RESOLUTION_REASON
    }
    if not clearify_ids:
        return resolutions
    return [
        item
        for item in resolutions
        if not (
            isinstance(item, dict)
            and str(item.get("unknown_id") or "").strip() in clearify_ids
            and str(item.get("reason") or "") != CLEARIFY_RESOLUTION_REASON
        )
    ]


def _merge_beliefs_by_identity(left: list, right: list) -> tuple[list, dict[str, str]]:
    result = list(left)
    aliases: dict[str, str] = {}
    positions: dict[str, int] = {}
    for index, item in enumerate(result):
        key = _belief_identity_key(item)
        if key:
            positions[key] = index
    for item in right:
        key = _belief_identity_key(item)
        if key and key in positions:
            existing = result[positions[key]]
            if isinstance(existing, dict) and isinstance(item, dict):
                result[positions[key]] = _merge_belief(existing, item)
                old_id = str(existing.get("id") or "").strip()
                new_id = str(item.get("id") or "").strip()
                if old_id and new_id and old_id != new_id:
                    aliases[new_id] = old_id
            else:
                result[positions[key]] = item
        else:
            if key:
                positions[key] = len(result)
            result.append(item)
    return result, aliases


def _merge_belief(existing: dict, item: dict) -> dict:
    merged = {**existing, **item}
    if existing.get("id"):
        merged["id"] = existing["id"]
    merged["evidence"] = _dedupe_strings([
        *_reference_list(existing.get("evidence")),
        *_reference_list(item.get("evidence")),
    ])
    return merged


def _remap_resolution_belief_ids(item: object, aliases: dict[str, str]) -> object:
    if not isinstance(item, dict) or not aliases:
        return item
    return {
        **item,
        "belief_ids": [
            aliases.get(str(raw).strip(), str(raw).strip())
            for raw in item.get("belief_ids", [])
            if str(raw).strip()
        ],
    }


def _merge_list_by_identity(left: list, right: list) -> list:
    result = list(left)
    positions: dict[str, int] = {}
    for index, item in enumerate(result):
        key = _identity_key(item)
        if key:
            positions[key] = index
    for item in right:
        key = _identity_key(item)
        if key and key in positions:
            existing = result[positions[key]]
            if isinstance(existing, dict) and isinstance(item, dict):
                if item.get("repair_mode") == "append_missing_only" and item.get("unknown_id"):
                    result[positions[key]] = _append_resolution_repair(existing, item)
                elif existing.get("reason") == CLEARIFY_RESOLUTION_REASON:
                    result[positions[key]] = {**item, **existing}
                else:
                    result[positions[key]] = {**existing, **item}
            else:
                result[positions[key]] = item
        else:
            if key:
                positions[key] = len(result)
            result.append(item)
    return result


def _append_resolution_repair(existing: dict, repair: dict) -> dict:
    merged = dict(existing)
    new_refs = False
    for field in ("evidence", "belief_ids"):
        old_values = _reference_list(existing.get(field))
        new_values = _reference_list(repair.get(field))
        if any(value not in old_values for value in new_values):
            new_refs = True
        merged[field] = _dedupe_strings([
            *old_values,
            *new_values,
        ])
    for field in ("status", "reason"):
        if str(repair.get(field) or "").strip():
            merged[field] = repair[field]
    # 保留 repair_mode/semantic_missing：append-only 修复是否真正通过
    # 只能由 audit（finish 时的语义门禁）裁决，模型提交 repair 时不能
    # 自我宣布 resolved。旧实现在这里 pop，导致下一轮 repair_ids 为空、
    # 主循环误入 FINISH 分支、模型 read/record 被 already_resolved 拦截
    # 的三面夹击死锁（d5eef05a 第二形态）。
    return merged


def _reject_empty_repair(arguments: dict, recorded: dict) -> None:
    """Reject append_missing_only repair resolutions that add no new evidence.

    Without this guard a model caught in the semantic repair loop can resubmit
    the same partially_resolved resolution forever (empty belief_ids/evidence),
    keeping the unknown permanently in the repair set with zero progress.
    """
    if not isinstance(arguments.get("resolutions"), list):
        return
    for item in arguments["resolutions"]:
        if not isinstance(item, dict):
            continue
        if str(item.get("repair_mode") or "").strip() != "append_missing_only":
            continue
        unknown_id = str(item.get("unknown_id") or "").strip()
        if not unknown_id:
            continue
        existing = _find_by_unknown_id(
            [res for res in recorded.get("resolutions", []) if isinstance(res, dict)],
            unknown_id,
        )
        old_evidence = set(_reference_list((existing or {}).get("evidence")))
        old_beliefs = set(_reference_list((existing or {}).get("belief_ids")))
        new_evidence = set(_reference_list(item.get("evidence")))
        new_beliefs = set(_reference_list(item.get("belief_ids")))
        if str(item.get("status") or "") == "resolved":
            continue
        if new_evidence - old_evidence or new_beliefs - old_beliefs:
            continue
        raise ValueError(
            f"append_missing_only repair for {unknown_id} adds no new evidence or "
            "belief_ids; gather the missing observations first (read/grep/code_nav), "
            "then resubmit the repair with the new references."
        )


def _require_repair_resolutions(arguments: dict, repair_ids: set[str]) -> None:
    """Require a record call during active semantic repair to cover repair targets.

    When the semantic quality gate is waiting on specific unknowns, a bare
    beliefs-only record (or a record patching unrelated unknowns) makes zero
    progress on the repair list and lets the model spin forever. Force every
    record call to include a resolution for at least one pending repair id.
    """
    if not repair_ids:
        return
    provided = {
        str(item.get("unknown_id") or "").strip()
        for item in arguments.get("resolutions", [])
        if isinstance(item, dict) and str(item.get("unknown_id") or "").strip()
    }
    covered = [rid for rid in repair_ids if any(_same_unknown_id(rid, pid) for pid in provided)]
    if covered:
        return
    missing = sorted(repair_ids)
    raise ValueError(
        "semantic repair is active for unknowns: " + ", ".join(missing) +
        "; record_investigation_findings must include a repair resolution "
        "(repair_mode=append_missing_only with new belief_ids/evidence) for at "
        "least one of them before finishing."
    )


def _identity_key(item) -> str:
    if not isinstance(item, dict):
        return str(item)
    for field in ("unknown_id", "id", "text", "statement", "question"):
        value = str(item.get(field) or "").strip()
        if field == "unknown_id":
            value = _normalize_unknown_id(value)
        if value:
            return f"{field}:{value}"
    return ""


def _belief_identity_key(item) -> str:
    if not isinstance(item, dict):
        return str(item)
    key = str(item.get("key") or item.get("fact_key") or "").strip()
    if key:
        return f"fact:{key.casefold()}"
    statement = _normalize_statement(_belief_text(item))
    if statement:
        return f"statement:{statement}"
    item_id = str(item.get("id") or "").strip()
    return f"id:{item_id}" if item_id else ""


def _normalize_statement(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _record_task_updates(arguments: dict) -> list[dict]:
    return _investigation_task_updates(
        arguments.get("task_updates"),
        _unknowns(arguments.get("unknowns")) + _unknowns(arguments.get("new_unknowns")),
        _resolutions(arguments.get("resolutions")),
    )


def _finish_arguments(
    recorded: dict,
    finish: dict,
    *,
    prefer_finish_summary: bool = False,
) -> dict:
    facts = _runtime_patch_facts([], recorded)
    resolution_summary = "\n\n".join(
        str(item.get("answer") or "").strip()
        for item in recorded.get("resolutions", [])
        if isinstance(item, dict)
        and item.get("status") == "resolved"
        and str(item.get("answer") or "").strip()
    )
    finish_summary = str(finish.get("summary") or "").strip()
    if (
        prefer_finish_summary
        and finish_summary
        and resolution_summary
        and len(finish_summary)
        < len(resolution_summary) * READ_ONLY_SUMMARY_MIN_RESOLUTION_RATIO
    ):
        raise ValueError(
            "read_only finish summary drops too much audited resolution detail; "
            "rewrite the final deliverable to satisfy every acceptance criterion"
        )
    summary = (
        finish_summary
        if prefer_finish_summary and finish_summary
        else resolution_summary or finish_summary
    )
    if not summary:
        summary = "Investigation complete."
    combined: dict = {
        field: list(recorded.get(field, []))
        for field in FINDING_FIELDS
    }
    combined.update({
        "summary": summary,
        "ready_for_patch_planning": _recommended_next_step(finish) == "patch_planning",
        "recommended_next_step": _recommended_next_step(finish),
        "patch_planning_facts": facts,
        "patch_planning_context": facts,
    })
    return combined


def _recommended_next_step(finish: dict) -> str:
    value = str(finish.get("recommended_next_step") or "").strip()
    if value in {LEGACY_ASK_USER_STRATEGY, "clearify"}:
        return "continue_investigation"
    return value if value in {"patch_planning", "continue_investigation", "done"} else "done"


def _clean_questions(value: list) -> list[str]:
    return [text for item in value if (text := str(item).strip())]


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for raw in value if (item := str(raw).strip())]


def _reference_list(value) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return _string_list(value)


def _observation_reference_map(observations: list[dict]) -> dict[str, str]:
    refs: dict[str, str] = {}
    index = 1
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        observation_id = str(observation.get("id") or "").strip()
        if not observation_id:
            continue
        refs[f"obs_{index}"] = observation_id
        index += 1
    return refs


def _observation_ref_by_id(observations: list[dict]) -> dict[str, str]:
    return {
        observation_id: ref
        for ref, observation_id in _observation_reference_map(observations).items()
    }


def _observation_reference_payload(observations: list[dict], *, limit: int = 12) -> list[dict]:
    refs = _observation_reference_map(observations)
    payload = []
    by_id = {
        str(item.get("id") or "").strip(): item
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    for ref, observation_id in list(refs.items())[-limit:]:
        observation = by_id.get(observation_id, {})
        payload.append({
            "ref": ref,
            "id": observation_id,
            "tool": str(observation.get("tool") or ""),
            "title": str(observation.get("title") or observation.get("summary") or ""),
        })
    return payload


def _observation_refs(value: dict) -> list[str]:
    return _dedupe_strings([
        *_reference_list(value.get("observation_ids")),
        *_reference_list(value.get("evidence")),
    ])


def _belief_status(raw: dict, default: str = "unverified") -> str:
    allowed = {
        "unverified",
        "plausible",
        "supported",
        "strongly_supported",
        "runtime_confirmed",
        "contradicted",
        "invalidated",
    }
    status = str(raw.get("status") or "").strip()
    if status in allowed:
        return status
    confidence = str(raw.get("confidence") or raw.get("certainty") or "").strip().casefold()
    mapped = {
        "certain": "strongly_supported",
        "high": "strongly_supported",
        "likely": "supported",
        "medium": "supported",
        "uncertain": "plausible",
        "low": "plausible",
    }.get(confidence)
    if mapped:
        return mapped
    return default if default in allowed else "unverified"


def _beliefs(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            continue
        statement = _belief_text(raw)
        evidence = _observation_refs(raw)
        status = _belief_status(raw, default="supported" if evidence else "unverified")
        if not statement:
            continue
        items.append({
            "id": str(raw.get("id") or f"B{index}").strip(),
            "statement": statement,
            "status": status,
            "evidence": evidence,
        })
    return items


def _resolutions(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        unknown_id = str(raw.get("unknown_id") or raw.get("id") or "").strip()
        status = str(raw.get("status") or "").strip()
        if status == LEGACY_NEEDS_USER_STATUS:
            status = "needs_clearify"
        if status not in {"resolved", "partially_resolved", "needs_clearify", "deferred"}:
            continue
        if not unknown_id:
            continue
        item = {
            "unknown_id": unknown_id,
            "status": status,
            "kind": _resolution_kind(raw, status),
            "answer": str(raw.get("answer") or "").strip(),
            "evidence": _observation_refs(raw),
            "belief_ids": _string_list(raw.get("belief_ids")),
            "reason": str(raw.get("reason") or "").strip(),
        }
        if str(raw.get("repair_mode") or "").strip() == "append_missing_only":
            item["repair_mode"] = "append_missing_only"
        if isinstance(raw.get("semantic_missing"), list):
            item["semantic_missing"] = _semantic_missing_items(raw.get("semantic_missing"))
        items.append(item)
    return items


def _canonical_evidence_id(
    evidence_id: str,
    known_ids: set[str],
    observation_refs: dict[str, str] | None = None,
) -> str:
    if observation_refs and evidence_id in observation_refs:
        return observation_refs[evidence_id]
    if evidence_id in known_ids:
        return evidence_id
    # Cross-task prefix mapping: a resolution from an earlier analysis pass
    # (task-2f536378:call_...) may reference an observation that this pass
    # knows under the current task prefix (task-e092c71c:call_...). Match by
    # the call-id tail when the mapping is unambiguous.
    tail = evidence_id.rsplit(":", 1)[-1]
    if tail and tail != evidence_id:
        matches = [item for item in known_ids if item.endswith(f":{tail}")]
        if len(matches) == 1:
            return matches[0]
    matches = [item for item in known_ids if item.endswith(f":{evidence_id}")]
    return matches[0] if len(matches) == 1 else ""


def _normalize_evidence_refs(
    item: dict,
    known_ids: set[str],
    observation_refs: dict[str, str] | None = None,
) -> list[str]:
    normalized = []
    missing = []
    for evidence_id in _observation_refs(item):
        canonical = _canonical_evidence_id(evidence_id, known_ids, observation_refs)
        if canonical:
            normalized.append(canonical)
        else:
            missing.append(evidence_id)
    item["evidence"] = _dedupe_strings(normalized)
    return missing


def _validate_resolution_refs(resolutions: list[dict], beliefs: list[dict], observations: list[dict]) -> None:
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    observation_refs = _observation_reference_map(observations)
    belief_by_id = {
        str(item.get("id") or "").strip(): item
        for item in beliefs
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    usable_belief_status = {"plausible", "supported", "strongly_supported", "runtime_confirmed"}
    for resolution in resolutions:
        missing_evidence = _normalize_evidence_refs(resolution, evidence_ids, observation_refs)
        if missing_evidence:
            sample_ids = sorted(evidence_ids)[:8]
            raise ValueError(
                f"resolution {resolution['unknown_id']} references unknown evidence ids: "
                + ", ".join(missing_evidence)
                + ". Evidence ids must be observation ids returned by read/glob/grep "
                "(not tool call ids)"
                + (f"; current observations: {', '.join(sample_ids)}" + ("..." if len(evidence_ids) > 8 else "") if sample_ids else "")
            )
        missing_beliefs = [item for item in resolution.get("belief_ids", []) if item not in belief_by_id]
        if missing_beliefs:
            raise ValueError(
                f"resolution {resolution['unknown_id']} references unknown belief ids: "
                + ", ".join(missing_beliefs)
            )
        weak_beliefs = [
            item for item in resolution.get("belief_ids", [])
            if belief_by_id[item].get("status") not in usable_belief_status
        ]
        if weak_beliefs:
            raise ValueError(
                f"resolution {resolution['unknown_id']} references unsupported belief ids: "
                + ", ".join(weak_beliefs)
            )


def _validate_belief_refs(beliefs: list[dict], observations: list[dict]) -> None:
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    observation_refs = _observation_reference_map(observations)
    for belief in beliefs:
        missing = _normalize_evidence_refs(belief, evidence_ids, observation_refs)
        if missing:
            raise ValueError(
                f"belief {belief['id']} references unknown evidence ids: "
                + ", ".join(missing)
            )


def _salvage_resolution_candidates(arguments: dict, observations: list[dict]) -> list[dict]:
    resolutions = _resolutions(arguments.get("resolutions"))
    if not resolutions:
        return []
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    observation_refs = _observation_reference_map(observations)
    salvaged = []
    for resolution in resolutions:
        if not (
            str(resolution.get("unknown_id") or "").strip()
            and (
                str(resolution.get("answer") or "").strip()
                or str(resolution.get("reason") or "").strip()
            )
        ):
            continue
        missing = _normalize_evidence_refs(resolution, evidence_ids, observation_refs)
        if missing:
            resolution["invalid_evidence_ids"] = missing
            resolution["evidence_binding"] = "unbound"
        if not resolution.get("evidence") and not resolution.get("belief_ids"):
            bound = _bind_grounding_evidence({"resolutions": [dict(resolution)]}, observations)
            resolution = bound["resolutions"][0]
        if not resolution.get("evidence") and not resolution.get("belief_ids"):
            resolution["evidence_binding"] = "unbound"
            if resolution.get("status") == "resolved":
                resolution["status"] = "partially_resolved"
                resolution["reason"] = (
                    resolution.get("reason")
                    or "Resolution answer was retained, but valid observation evidence is still unbound."
                )
        elif missing:
            resolution["evidence_binding"] = "rebound"
        salvaged.append(resolution)
    return salvaged


def _drop_invalid_belief_refs(
    beliefs: list[dict],
    observations: list[dict],
    repairs: list[str],
) -> list[dict]:
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    observation_refs = _observation_reference_map(observations)
    changed = False
    for belief in beliefs:
        original = belief.get("evidence", [])
        _normalize_evidence_refs(belief, evidence_ids, observation_refs)
        evidence = belief.get("evidence", [])
        if evidence != original:
            changed = True
            if not evidence:
                belief["status"] = "unverified"
    if changed:
        repairs.append("Dropped invalid belief evidence references during finalization repair")
    return beliefs


_FILE_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_$])"
    r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9][A-Za-z0-9_+-]{0,15})"
    r"(?![A-Za-z0-9_$])"
)


def _normalize_path(p: str) -> str:
    p = str(p).replace("\\", "/").strip()
    p = re.sub(r"^[A-Za-z]:/", "", p)
    return p.lstrip("./").lower()


@lru_cache(maxsize=8)
def _workspace_file_catalog(workspace_dir: str) -> tuple[frozenset[str], frozenset[str]]:
    if not workspace_dir:
        return frozenset(), frozenset()
    try:
        from .tools.builtin.common import _ignored

        root = Path(workspace_dir).resolve()
        if not root.is_dir():
            return frozenset(), frozenset()
        files: set[str] = set()
        stack = [root]
        while stack and len(files) < PROJECT_FILE_SCAN_LIMIT:
            current = stack.pop()
            try:
                children = list(current.iterdir())
            except OSError:
                continue
            for child in children:
                if _ignored(child, root):
                    continue
                if child.is_dir():
                    stack.append(child)
                    continue
                if not child.is_file():
                    continue
                rel = _normalize_path(child.relative_to(root).as_posix())
                if rel:
                    files.add(rel)
                if len(files) >= PROJECT_FILE_SCAN_LIMIT:
                    break
        basenames = {item.rsplit("/", 1)[-1] for item in files}
        return frozenset(files), frozenset(basenames)
    except Exception:
        return frozenset(), frozenset()


def _observed_file_paths(observations: list[dict]) -> set[str]:
    files = {
        _normalize_path(item.get("path") or "")
        for item in observations
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    }
    return {item for item in files if item}


def _file_ref_matches(path: str, ref: str) -> bool:
    basename = ref.rsplit("/", 1)[-1]
    return path == ref or path.endswith("/" + ref) or path.endswith("/" + basename)


def _project_file_ref(
    ref_raw: str,
    observations: list[dict],
    workspace_dir: str = "",
) -> str:
    if "://" in str(ref_raw):
        return ""
    ref = _normalize_path(ref_raw)
    if not ref or ref.endswith(".pyc"):
        return ""
    observed = _observed_file_paths(observations) | _hit_files_from_observations(observations)
    for path in sorted(observed, key=len):
        if _file_ref_matches(path, ref):
            return path
    files, _ = _workspace_file_catalog(str(workspace_dir or ""))
    for path in sorted(files, key=len):
        if _file_ref_matches(path, ref):
            return path
    return ""


def _hit_files_from_observations(observations: list[dict]) -> set[str]:
    """Files confirmed to exist / contain a match via grep/glob observations.

    A grep hit line has the form ``path:line:content`` and a glob result is a
    bare path. A file listed there is *known to exist* (and to contain the
    searched symbol), which is enough for existential mentions ("App.vue is
    the root component") even though its full behavior was never read.
    """
    files: set[str] = set()
    for item in observations:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "")
        if tool not in {"grep", "glob"}:
            continue
        evidence = item.get("_grounding_evidence") or item.get("output") or ""
        if not isinstance(evidence, str):
            continue
        for line in evidence.splitlines():
            line = line.strip()
            if not line:
                continue
            if tool == "glob":
                files.add(_normalize_path(line))
                continue
            # grep: "path:line:content" — path may itself contain colons (Windows)
            head = line.split(":", 2)[0] if line.count(":") >= 2 else line
            if head:
                files.add(_normalize_path(head))
    return files


def _require_file_reads(
    resolutions: list[dict],
    observations: list[dict],
    workspace_dir: str = "",
) -> list[str]:
    """Return issues when a resolution claims a project file's behavior without
    ever reading it.

    The answer text is scanned for references that resolve to real project
    files (sessions.py, frontend/.../HomePage.vue, config schemas, etc.).
    References that carry a symbol or a
    behavioral claim ("sessions.py's generate_title writes the name field")
    require a ``read`` observation of that file. A bare existential mention
    ("App.vue is the root component") only requires the file to be known --
    grep/glob hits are sufficient evidence the file exists and contains the
    searched symbol. Candidate paths are filtered through observed paths and
    the workspace file catalog, so dotted identifiers such as ``props.sessions``
    are not treated as files.
    """
    read_files: set[str] = set()
    for item in observations:
        if not isinstance(item, dict) or str(item.get("tool") or "") != "read":
            continue
        path = _normalize_path(item.get("path") or "")
        if path:
            read_files.add(path)
    if not read_files:
        return []
    hit_files = read_files | _hit_files_from_observations(observations)
    issues: list[str] = []
    for resolution in resolutions:
        if not isinstance(resolution, dict):
            continue
        unknown_id = str(resolution.get("unknown_id") or "?")
        answer = " ".join(str(resolution.get(field) or "") for field in ("answer", "reason"))
        for match in _FILE_REF_RE.finditer(answer):
            ref_raw = match.group(1)
            ref = _project_file_ref(ref_raw, observations, workspace_dir)
            if not ref:
                continue
            read_matched = any(
                _file_ref_matches(rf, ref)
                for rf in read_files
            )
            if read_matched:
                continue
            if _ref_is_existential(answer, match) and any(
                _file_ref_matches(rf, ref)
                for rf in hit_files
            ):
                continue
            issues.append(
                f"resolution {unknown_id} references file {ref_raw} "
                "but no read observation covers it"
            )
    return issues


def _ref_is_existential(answer: str, match) -> bool:
    """True when a file mention is existential, not a behavioral claim.

    A mention is existential when the file name is not followed by a symbol
    or a behavior verb ("App.vue 是根组件", "the store lives in
    useSessions.js"). A behavioral claim attaches a symbol ("sessions.py 的
    generate_title") or a verb of effect ("HomePage.vue 调用
    generateSessionTitle"), which needs real reading.
    """
    tail = answer[match.end():match.end() + 80]
    if re.match(r"^\s*(?:的|中|里|内|文件|:)?\s*[A-Za-z_][A-Za-z0-9_]*\s*(?:\(\))?", tail):
        return False
    if re.search(
        r"调用|写入|返回|更新|修改|执行|处理|定义|实现|触发|发送|接收|渲染|"
        r"绑定|监听|创建|删除|设置|声明|初始化|导入|导出|请求|响应|加载|刷新|"
        r"显示|切换|注册|订阅|update|set|get|call|invoke|emit|handle|apply|"
        r"resolve|finish|render|write|return|send|receive|create|delete",
        tail,
        re.IGNORECASE,
    ):
        return False
    return True


_FILE_SYMBOL_RE = re.compile(
    r"(?<![A-Za-z0-9_$])"
    r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9][A-Za-z0-9_+-]{0,15})"
    r"(?:\s|[^A-Za-z0-9_$./\\-]){0,8}([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\))?"
)

# Generic language/stdlib/argument words that must never be treated as
# project symbols by the LSP definition check. Real project function names
# (create, rename, generate_title, ...) must NOT be listed here.
_DEF_READ_NOISE_SYMBOLS = frozenset({
    "def", "if", "elif", "else", "for", "while", "return", "not", "and", "or",
    "in", "is", "with", "as", "try", "except", "finally", "raise", "yield",
    "lambda", "pass", "break", "continue", "import", "from", "class", "assert",
    "del", "global", "nonlocal",
    "print", "len", "str", "int", "float", "bool", "list", "dict", "set",
    "tuple", "type", "range", "sum", "min", "max", "sorted", "enumerate",
    "zip", "map", "filter", "any", "all", "isinstance", "issubclass", "getattr",
    "setattr", "hasattr", "repr", "format", "open", "id", "hash", "iter", "next",
    "object", "property", "staticmethod", "classmethod", "super", "vars", "dir",
    "abs", "round", "divmod", "pow", "ord", "chr", "hex", "oct", "bin", "bytes",
    "bytearray", "memoryview", "slice", "frozenset", "complex", "input", "eval",
    "exec", "compile", "globals", "locals", "callable", "ascii", "help", "exit",
    "json", "re", "os", "sys", "time", "datetime", "pathlib", "Path", "shutil",
    "subprocess", "uuid", "collections", "defaultdict", "Counter", "deque",
    "functools", "itertools", "typing", "Optional", "Any", "List", "Dict", "Set",
    "Tuple", "Union", "Callable", "Iterator", "Generator", "Iterable", "Mapping",
    "startswith", "endswith", "strip", "split", "join", "replace", "lower",
    "upper", "capitalize", "title", "find", "index", "count", "append", "extend",
    "insert", "remove", "pop", "clear", "sort", "reverse", "copy", "setdefault",
    "update", "keys", "values", "items", "add", "discard", "union", "intersection",
    "difference", "issubset", "issuperset", "encode", "decode", "zfill", "ljust",
    "rjust", "partition", "rpartition", "splitlines", "expandtabs", "maketrans",
    "translate", "self", "data", "item", "value", "key", "text", "content",
    "message", "event", "run", "id", "name", "title", "state", "status", "reason",
    "answer", "summary", "output", "input", "result", "error", "exc", "request",
    "response", "path", "file", "line", "index", "kind", "source", "target",
    "session", "workspace", "model", "provider", "analysis", "investigation",
    "resolution", "belief", "observation", "evidence", "unknown", "task", "goal",
    "acceptance", "requirement", "tool", "call", "function", "fn", "args",
    "kwargs", "prev", "current", "total", "count", "size", "length", "before",
    "after", "start", "end", "done", "fail", "success", "ok", "true", "false",
    "none", "null", "python", "node", "js", "ts", "vue", "css", "html", "md",
    "txt", "log", "default", "generate", "select", "seed", "final", "finish",
    "handle", "process", "apply", "resolve", "check", "validate", "parse",
    "convert", "merge", "normalize", "search", "find", "read", "write", "grep",
    "get", "set", "known", "items", "values", "keys", "status", "action",
    "kind", "field", "fields", "record", "records", "store", "stores",
    "explain", "describe", "show", "mention", "note", "include", "cover",
})


def _require_lsp_definition_reads(
    resolutions: list[dict],
    observations: list[dict],
    workspace_dir: str,
    max_queries: int = 6,
) -> list[str]:
    """Use LSP to resolve the true definition file of symbols the answer
    claims, then require a read observation covering that file.

    A plain file-level check (``_require_file_reads``) only catches answers
    that name a file which was never read. It misses the more common failure:
    the model names the file it *did* read (e.g. investigating.py) while the
    symbol's actual definition lives elsewhere (e.g. task_updates.py). LSP
    definition lookup follows the import and points at the real definition
    file, so the check enforces that the model read the file where the symbol
    is actually defined, not just where it was mentioned.

    The check is best-effort: LSP is a subprocess, so every failure (server
    not installed, timeout, unknown symbol) silently skips that symbol.
    """
    read_files: set[str] = set()
    for item in observations:
        if not isinstance(item, dict) or str(item.get("tool") or "") != "read":
            continue
        path = _normalize_path(item.get("path") or "")
        if path:
            read_files.add(path)
    if not read_files:
        return []
    issues: list[str] = []
    queried = 0
    for resolution in resolutions:
        if not isinstance(resolution, dict):
            continue
        unknown_id = str(resolution.get("unknown_id") or "?")
        answer = " ".join(str(resolution.get(field) or "") for field in ("answer", "reason"))
        for match in _FILE_SYMBOL_RE.finditer(answer):
            if queried >= max_queries:
                return issues
            ref_raw, symbol = match.group(1), match.group(2)
            ref = _project_file_ref(ref_raw, observations, workspace_dir)
            if not ref or ref.endswith(".pyc") or symbol in _DEF_READ_NOISE_SYMBOLS:
                continue
            definition_file = _lsp_definition_file(ref, symbol, workspace_dir)
            queried += 1
            if not definition_file:
                continue
            norm_def = _normalize_path(definition_file)
            matched = any(
                _file_ref_matches(rf, norm_def)
                for rf in read_files
            )
            if not matched:
                issues.append(
                    f"resolution {unknown_id} claims behavior of {symbol}() "
                    f"(defined in {definition_file}) but that file was never read"
                )
    return issues


def _lsp_definition_file(path: str, symbol: str, workspace_dir: str) -> str | None:
    """Resolve the file where ``symbol`` is defined via the code_nav LSP tool.

    Returns the absolute definition file path, or None when LSP is unavailable
    or the symbol cannot be resolved (best-effort). 兼容旧调用方：
    路径 与 三态中的 NOT_FOUND/UNAVAILABLE 统一折叠为 None。
    """
    status = _lsp_definition_file_typed(path, symbol, workspace_dir)
    if status in (LSP_DEFINITION_NOT_FOUND, LSP_DEFINITION_UNAVAILABLE):
        return None
    return status if isinstance(status, str) else None


LSP_DEFINITION_NOT_FOUND = "___lsp_definition_not_found___"
LSP_DEFINITION_UNAVAILABLE = "___lsp_definition_unavailable___"


@lru_cache(maxsize=256)
def _lsp_definition_file_typed(path: str, symbol: str, workspace_dir: str) -> str:
    """三态 LSP 定义查询：定义文件路径 / NOT_FOUND / UNAVAILABLE。

    - 查到定义文件：符号是项目代码（不豁免，且定义文件可作为读证据要求）。
    - NOT_FOUND：server 正常但符号无定义 —— 框架/外部引用的强信号（豁免）。
    - UNAVAILABLE：server 没装/无法启动 —— 自动安装（lsp_tool install by
      language）并指数退避重试（1s/2s/4s）；仍不可用返回 UNAVAILABLE。
      调用方按"框架引用"豁免（同名文件层已兜底项目代码，不会误放行）。
    """
    for attempt in range(3):
        result = _lsp_definition_file_once(path, symbol, workspace_dir)
        if result is not LSP_DEFINITION_UNAVAILABLE:
            return result
        if attempt < 2:
            _try_install_lsp_for_path(path, workspace_dir)
            time.sleep(1.0 * (2 ** attempt))  # 1s, 2s
    return LSP_DEFINITION_UNAVAILABLE


def _try_install_lsp_for_path(path: str, workspace_dir: str) -> bool:
    """按文件扩展名推断语言，自动安装最合适的 LSP server（mason）。

    失败静默（返回 False）——LSP 安装是 best-effort，装不上就走
    UNAVAILABLE 分支，由调用方按框架引用豁免兜底。
    """
    try:
        from . import lsp

        language = _extension_language(path)
        if not language:
            return False
        candidates = lsp.list_all(language=language)
        if not candidates:
            return False
        picked = next(
            (item for item in candidates if item.get("available")),
            candidates[0],
        )
        name = str(picked.get("name") or "").strip()
        if not name:
            return False
        existing = lsp.get(name) if hasattr(lsp, "get") else None
        if isinstance(existing, dict) and existing.get("status") == "ready":
            return True
        result = lsp.install(name)
        return bool(isinstance(result, dict) and result.get("ok"))
    except Exception:
        return False


_EXTENSION_LANGUAGE = {
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescriptreact", ".jsx": "javascriptreact",
    ".vue": "vue", ".svelte": "svelte",
    ".cs": "csharp", ".fs": "fsharp", ".vb": "vb",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala",
    ".go": "go", ".rs": "rust",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hxx": "cpp",
    ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".lua": "lua", ".r": "r", ".m": "objective-c", ".mm": "objective-cpp",
    ".dart": "dart", ".ex": "elixir", ".exs": "elixir",
    ".hs": "haskell", ".erl": "erlang", ".ml": "ocaml",
    ".sql": "sql", ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".json": "json",
    ".html": "html", ".css": "css", ".scss": "scss",
}


def _extension_language(path: str) -> str:
    ext = os.path.splitext(str(path).lower())[1]
    return _EXTENSION_LANGUAGE.get(ext, "")


def _lsp_definition_file_once(path: str, symbol: str, workspace_dir: str) -> str:
    """单次 LSP 定义查询，返回三态（不重试不安装）。"""
    try:
        import asyncio

        from .tools.builtin import code_nav
        from .tools.spec import ToolResult

        result: ToolResult = asyncio.run(
            code_nav.code_nav_tool.execute(
                {"operation": "definition", "path": path, "symbol": symbol},
                {"directory": workspace_dir},
            )
        )
    except Exception:
        return LSP_DEFINITION_UNAVAILABLE
    if not isinstance(result, ToolResult) or result.title.startswith("[error]"):
        return LSP_DEFINITION_UNAVAILABLE
    try:
        payload = json.loads(result.output)
    except (json.JSONDecodeError, TypeError):
        return LSP_DEFINITION_UNAVAILABLE
    if not isinstance(payload, dict):
        return LSP_DEFINITION_UNAVAILABLE
    if not payload.get("ok"):
        message = str(payload.get("message") or "").casefold()
        unavailable = any(
            token in message
            for token in (
                "lsp server not found",
                "no lsp server",
                "no enabled available lsp server",
                "server not available",
                "executable is unavailable",
                "not installed",
                "no server",
                "could not start",
                "unable to connect",
                "not found",
                "is not installed",
            )
        )
        if unavailable or payload.get("kind") == "error":
            return LSP_DEFINITION_UNAVAILABLE
        return LSP_DEFINITION_NOT_FOUND
    result_val = payload.get("result")
    items = None
    if isinstance(result_val, dict):
        items = result_val.get("items")
    elif isinstance(result_val, list):
        items = result_val
    if isinstance(items, list) and items:
        loc = items[0]
        if isinstance(loc, dict):
            raw_path = loc.get("path") or loc.get("uri")
            if raw_path:
                raw = str(raw_path)
                if raw.startswith("file://"):
                    from urllib.parse import unquote, urlparse

                    parsed = urlparse(raw)
                    raw = unquote(parsed.path)
                    if re.match(r"^/[A-Za-z]:", raw):
                        raw = raw[1:]
                return raw.replace("/", "\\") if "\\" in str(__import__("pathlib").Path.cwd()) else raw
    return None


def _drop_invalid_resolution_refs(
    resolutions: list[dict],
    beliefs: list[dict],
    observations: list[dict],
    repairs: list[str],
    workspace_dir: str = "",
) -> list[dict]:
    file_issues = _require_file_reads(resolutions, observations, workspace_dir)
    if workspace_dir:
        lsp_issues = _require_lsp_definition_reads(resolutions, observations, workspace_dir)
        file_issues = file_issues + lsp_issues
    if file_issues:
        raise ValueError("; ".join(file_issues))
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    observation_refs = _observation_reference_map(observations)
    usable_beliefs = {
        str(item.get("id") or "").strip()
        for item in beliefs
        if isinstance(item, dict)
        and str(item.get("id") or "").strip()
        and item.get("status") in {"plausible", "supported", "strongly_supported", "runtime_confirmed"}
    }
    changed = False
    for resolution in resolutions:
        original_evidence = resolution.get("evidence", [])
        _normalize_evidence_refs(resolution, evidence_ids, observation_refs)
        evidence = resolution.get("evidence", [])
        beliefs = [item for item in resolution.get("belief_ids", []) if item in usable_beliefs]
        changed = changed or evidence != original_evidence or beliefs != resolution.get("belief_ids", [])
        resolution["evidence"] = evidence
        resolution["belief_ids"] = beliefs
    if changed:
        repairs.append("Dropped invalid resolution references during finalization repair")
    return resolutions


def _runtime_readiness(
    *,
    model_ready: bool,
    analysis: dict | None,
    initial_unknowns: list[dict],
    resolutions: list[dict],
    unknowns: list[dict],
    patch_context: list[str],
    finish_arguments: dict | None = None,
) -> dict:
    blockers = [item for item in unknowns if item.get("blocking")]
    reasons = []
    if blockers:
        reasons.append("blocking_unknowns_remain")
    for item in initial_unknowns:
        if not item.get("blocking"):
            continue
        resolution = _find_by_unknown_id(resolutions, item["id"])
        if not resolution or resolution.get("status") != "resolved":
            if (
                resolution
                and item.get("resolution_strategy") == "investigate_project"
                and resolution.get("answer")
                and not (resolution.get("evidence") or resolution.get("belief_ids"))
            ):
                reasons.append(f"{item['id']}:missing_evidence")
            else:
                reasons.append(f"{item['id']}:not_resolved")
            continue
        if item.get("resolution_strategy") == "investigate_project" and not (
            resolution.get("evidence") or resolution.get("belief_ids")
        ) and not _is_user_product_decision(resolution, initial_unknowns):
            reasons.append(f"{item['id']}:missing_evidence")
    if isinstance(analysis, dict) and analysis.get("acceptance_criteria") and not patch_context:
        reasons.append("missing_patch_planning_facts")
    if _requires_bugfix_readiness(analysis, model_ready):
        reasons.extend(_bugfix_readiness_reasons(finish_arguments or {}))
    ready = not reasons
    return {
        "ready": ready,
        "model_ready": model_ready,
        "reasons": reasons,
    }


def _requires_bugfix_readiness(analysis: dict | None, model_ready: bool) -> bool:
    if not model_ready or not isinstance(analysis, dict):
        return False
    intent = analysis.get("intent") if isinstance(analysis.get("intent"), dict) else {}
    return (
        str(intent.get("type") or "").strip() == "bugfix"
        and analysis.get("execution_mode") == "implement"
    )


def _bugfix_readiness_reasons(arguments: dict) -> list[str]:
    readiness = arguments.get("bugfix_readiness")
    if not isinstance(readiness, dict):
        return ["bugfix_readiness:missing"]
    fields = [
        "failure_reproduced_or_observed",
        "root_cause_or_failing_boundary_identified",
        "patch_target_identified",
        "expected_behavior_change_defined",
        "validation_scenario_defined",
    ]
    return [
        f"bugfix_readiness:{field}"
        for field in fields
        if readiness.get(field) is not True
    ]


def _initial_unknowns(analysis: dict | None) -> list[dict]:
    if not isinstance(analysis, dict):
        return []
    value = analysis.get("unknowns")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        return []
    return _unknowns(value)


def _complete_resolutions(resolutions: list[dict], initial_unknowns: list[dict], unknowns: list[dict]) -> list[dict]:
    for resolution in resolutions:
        source = _find_by_unknown_id(initial_unknowns, resolution["unknown_id"], id_field="id")
        if (
            resolution.get("status") == "needs_clearify"
            and source
            and source.get("type") != "product_decision"
        ):
            grounded = bool(
                resolution.get("answer")
                and (resolution.get("evidence") or resolution.get("belief_ids"))
            )
            resolution["status"] = "resolved" if grounded else "partially_resolved"
            resolution["reason"] = (
                "Only product decisions may require user clarification; "
                "this project fact is grounded." if grounded else
                "Only product decisions may require user clarification."
            )
        if (
            resolution.get("status") == "deferred"
            and source
            and source.get("blocking")
            and source.get("resolution_strategy") != "deferred"
        ):
            resolution["status"] = "partially_resolved"
            resolution["reason"] = (
                "A blocking task-contract unknown cannot be deferred without resolving it."
            )
    unresolved_ids = [item["id"] for item in unknowns]
    for item in initial_unknowns:
        if _find_by_unknown_id(resolutions, item["id"]):
            continue
        status = "partially_resolved"
        if item.get("resolution_strategy") == "clearify":
            status = "needs_clearify"
        elif item.get("resolution_strategy") == "deferred" or not item.get("blocking"):
            status = "deferred"
        if any(_same_unknown_id(item["id"], unknown_id) for unknown_id in unresolved_ids) or item.get("blocking"):
            resolutions.append({
                "unknown_id": item["id"],
                "status": status,
                "answer": "",
                "evidence": [],
                "belief_ids": [],
                "reason": "No explicit resolution was supplied for this task-contract unknown.",
            })
    return resolutions


def _enforce_resolution_evidence(resolutions: list[dict], initial_unknowns: list[dict], *, strict: bool = True) -> list[dict]:
    if not strict:
        return resolutions
    for resolution in resolutions:
        source = _find_by_unknown_id(initial_unknowns, resolution["unknown_id"], id_field="id")
        if not source:
            continue
        if (
            source.get("blocking")
            and source.get("resolution_strategy") == "investigate_project"
            and resolution.get("status") == "resolved"
            and not (resolution.get("evidence") or resolution.get("belief_ids"))
            and not _is_user_product_decision(resolution, initial_unknowns)
        ):
            resolution["status"] = "partially_resolved"
            resolution["reason"] = resolution.get("reason") or "Resolved codebase facts require evidence or belief references."
    return resolutions


def _is_user_product_decision(
    resolution: dict,
    unknowns: list[dict],
) -> bool:
    if resolution.get("reason") != CLEARIFY_RESOLUTION_REASON:
        return False
    unknown_id = str(resolution.get("unknown_id") or "").strip()
    return any(
        _same_unknown_id(item.get("id"), unknown_id)
        and item.get("type") in ("product_decision", "engineering_decision")
        for item in unknowns
        if isinstance(item, dict)
    )


def _is_engineering_decision_resolution(
    resolution: dict,
    unknowns: list[dict],
) -> bool:
    unknown_id = str(resolution.get("unknown_id") or "").strip()
    return any(
        _same_unknown_id(item.get("id"), unknown_id)
        and item.get("type") == "engineering_decision"
        for item in unknowns
        if isinstance(item, dict)
    )


def _unresolved_from_resolutions(resolutions: list[dict], initial_unknowns: list[dict]) -> list[dict]:
    unresolved = []
    for resolution in resolutions:
        if resolution["status"] == "resolved":
            continue
        source = _find_by_unknown_id(initial_unknowns, resolution["unknown_id"], id_field="id") or {}
        strategy = "investigate_project"
        if resolution["status"] == "needs_clearify":
            strategy = "clearify"
        elif resolution["status"] == "deferred":
            strategy = "deferred"
        unknown_type = source.get("type") or "code_fact"
        if resolution.get("reason") == CLEARIFY_UNRESOLVED_REASON:
            unknown_type = "code_fact"
        question = source.get("question") or _question_from_resolution(resolution)
        unresolved.append({
            "id": resolution["unknown_id"],
            "question": question,
            "blocking": resolution["status"] in {"partially_resolved", "needs_clearify"} and bool(source.get("blocking", True)),
            "type": unknown_type,
            "resolution_strategy": strategy,
        })
    return unresolved


def _drop_resolved_unknowns(unknowns: list[dict], resolutions: list[dict], repairs: list[str]) -> list[dict]:
    resolved_ids = [
        str(item.get("unknown_id") or "").strip()
        for item in resolutions
        if isinstance(item, dict) and item.get("status") == "resolved"
    ]
    if not resolved_ids:
        return unknowns
    filtered = [
        item for item in unknowns
        if not any(_same_unknown_id(item.get("id"), resolved_id) for resolved_id in resolved_ids)
    ]
    if len(filtered) != len(unknowns):
        repairs.append("Removed unknowns already resolved by resolutions")
    return filtered


def _question_from_resolution(resolution: dict) -> str:
    text = str(resolution.get("answer") or resolution.get("reason") or "").strip()
    if _looks_like_question(text):
        return text
    return "请明确这个实现决策？"


def _decision_question(value: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if _looks_like_question(text):
        return text
    return f"请明确：{text}？"


def _looks_like_question(value: str) -> bool:
    text = str(value or "")
    return "?" in text or "？" in text

def _merge_unknowns(items: list[dict]) -> list[dict]:
    merged = {}
    by_question = {}
    for item in items:
        if not item.get("id") or not item.get("question"):
            continue
        key = _question_key(item["question"])
        existing_id = by_question.get(key)
        if existing_id:
            current = merged[existing_id]
            merged[existing_id] = {
                **current,
                **item,
                "id": existing_id,
                "blocking": bool(current.get("blocking") or item.get("blocking")),
            }
            continue
        merged[item["id"]] = item
        by_question[key] = item["id"]
    return list(merged.values())


def _unknown_id_for_question(question: str, unknowns: list[dict]) -> str:
    key = _question_key(question)
    for item in unknowns:
        if item.get("id") and _question_key(item.get("question", "")) == key:
            return item["id"]
    return ""


def _question_key(value: str) -> str:
    return re.sub(r"\W+", "", str(value or "")).casefold()


def _unknown_id_tail(value: str | None) -> str:
    text = str(value or "").strip()
    return text.rsplit(":", 1)[-1] if ":" in text else text


def _same_unknown_id(left: str | None, right: str | None) -> bool:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text or not right_text:
        return False
    return left_text == right_text or _unknown_id_tail(left_text) == _unknown_id_tail(right_text)


def _find_by_unknown_id(items: list[dict], unknown_id: str | None, *, id_field: str = "unknown_id") -> dict | None:
    return next(
        (
            item for item in items
            if isinstance(item, dict) and _same_unknown_id(item.get(id_field), unknown_id)
        ),
        None,
    )


def _is_placeholder_question(value: str | None, unknown_id: str | None = "") -> bool:
    text = " ".join(str(value or "").split())
    if not text:
        return True
    if unknown_id and _same_unknown_id(text, unknown_id):
        return True
    placeholder = "\u8bf7\u660e\u786e\u8fd9\u4e2a\u5b9e\u73b0\u51b3\u7b56"
    return text.startswith(placeholder)


def _task_update_question(final: dict, unknown_id: str | None) -> str:
    if not unknown_id:
        return ""
    for item in final.get("task_updates") or []:
        if not isinstance(item, dict) or not _same_unknown_id(item.get("id"), unknown_id):
            continue
        text = str(item.get("text") or "").strip()
        if not _is_placeholder_question(text, unknown_id):
            return text
    return ""


def _display_question_for_unknown(item: dict | None, final: dict) -> str:
    if not item:
        return ""
    unknown_id = str(item.get("id") or "").strip()
    question = str(item.get("question") or "").strip()
    if not _is_placeholder_question(question, unknown_id):
        return question
    task_question = _task_update_question(final, unknown_id)
    return task_question or question


def _best_clearify_unknown(final: dict) -> dict | None:
    candidates = [
        item for item in final.get("unknowns", [])
        if isinstance(item, dict)
        and item.get("blocking")
        and item.get("resolution_strategy") == "clearify"
    ]
    if not candidates:
        return None
    specific = [
        item for item in candidates
        if not _is_placeholder_question(_display_question_for_unknown(item, final), item.get("id"))
    ]
    return specific[0] if specific else candidates[0]


def _patch_context(value, repairs: list[str], repair_conflicts: bool) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        if repair_conflicts:
            repairs.append("Dropped non-array patch planning facts")
            return []
        raise ValueError("patch_planning_context must be an array of strings")
    items = []
    normalized_object = False
    for raw in value:
        if isinstance(raw, dict):
            if not repair_conflicts:
                raise ValueError("patch_planning_context must be an array of strings")
            fact = str(raw.get("fact") or raw.get("text") or raw.get("statement") or "").strip()
            source = str(raw.get("source") or raw.get("evidence") or "").strip()
            if fact:
                items.append(f"{fact} ({source})" if source else fact)
                normalized_object = True
        elif text := str(raw).strip():
            items.append(text)
    if normalized_object:
        repairs.append("Normalized patch_planning_context objects to strings")
    return items


def _require_items_accounted(required_items, task_updates, resolutions, repair_conflicts: bool) -> None:
    if not required_items:
        return
    update_ids = {
        str(item.get("id") or "").strip()
        for item in task_updates or []
        if isinstance(item, dict) and str(item.get("status") or "") in {"known", "deferred", "blocked"}
    }
    resolution_ids = [item["unknown_id"] for item in resolutions]
    missing = [
        str(item.get("id") or "").strip()
        for item in required_items
        if isinstance(item, dict)
        and not any(_same_unknown_id(item.get("id"), known_id) for known_id in [*update_ids, *resolution_ids])
    ]
    if missing and not repair_conflicts:
        raise ValueError("finish_investigation must account for every initial hypothesis/unknown")


def _resolve_task_update_conflicts(
    unknowns: list[dict],
    task_updates,
    repairs: list[str],
    repair_conflicts: bool,
) -> list[dict]:
    known_ids = {
        str(item.get("id") or "").strip()
        for item in task_updates or []
        if isinstance(item, dict) and str(item.get("status") or "").strip() == "known"
    }
    conflicts = [
        item for item in unknowns
        if any(_same_unknown_id(item.get("id"), known_id) for known_id in known_ids)
    ]
    if not conflicts:
        return unknowns
    if not repair_conflicts:
        raise ValueError("unknowns should contain only unresolved items")
    repairs.append("Removed unknowns already marked known by task_updates")
    return [
        item for item in unknowns
        if not any(_same_unknown_id(item.get("id"), known_id) for known_id in known_ids)
    ]


def _investigation_task_updates(value, unknowns: list[dict], resolutions: list[dict] | None = None) -> list[dict]:
    updates = []
    resolved_ids = [
        str(item.get("unknown_id") or "").strip()
        for item in resolutions or []
        if isinstance(item, dict) and item.get("status") == "resolved"
    ]
    if isinstance(value, list):
        for raw in value:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or "").strip()
            status = str(raw.get("status") or "").strip()
            if not text or status not in {"unknown", "known", "deferred", "blocked", "added", "updated"}:
                continue
            item_id = str(raw.get("id") or "").strip()
            if status == "known" and not any(
                _same_unknown_id(item_id, resolved_id)
                for resolved_id in resolved_ids
            ):
                status = "unknown"
            trace = raw.get("trace") if isinstance(raw.get("trace"), list) else []
            updates.append({
                "id": item_id,
                "kind": str(raw.get("kind") or "unknown").strip() or "unknown",
                "text": text,
                "status": status,
                "reason": str(raw.get("reason") or "").strip(),
                "trace": [str(item).strip() for item in trace if str(item).strip()][:6],
            })
    known_ids = {item["id"] for item in updates if item["status"] == "known" and item.get("id")}
    for resolution in resolutions or []:
        unknown_id = resolution.get("unknown_id", "")
        if not unknown_id or any(_same_unknown_id(unknown_id, known_id) for known_id in known_ids):
            continue
        source = _find_by_unknown_id(unknowns, unknown_id, id_field="id")
        text = (source or {}).get("question") or resolution.get("answer") or unknown_id
        resolution_status = resolution.get("status")
        status = {
            "resolved": "known",
            "partially_resolved": "unknown",
            "needs_clearify": "blocked",
            "deferred": "deferred",
        }.get(resolution_status, "unknown")
        evidence = resolution.get("evidence") if isinstance(resolution.get("evidence"), list) else []
        trace = evidence or resolution.get("belief_ids", [])
        updates.append({
            "id": unknown_id,
            "target_id": unknown_id,
            "kind": "unknown",
            "text": text,
            "status": status,
            "reason": resolution.get("reason", ""),
            "trace": trace[:6],
            "answers": [{
                "source": "investigation",
                "text": resolution.get("answer") or unknown_id,
                "reason": resolution.get("reason", ""),
                "trace": trace[:6],
            }] if resolution.get("answer") else [],
        })
        if status == "known":
            known_ids.add(unknown_id)
    for item in unknowns:
        existing_ids = {update.get("id") for update in updates if update.get("id")}
        if any(_same_unknown_id(item.get("id"), existing_id) for existing_id in existing_ids):
            continue
        updates.append({
            "id": item.get("id", ""),
            "kind": "unknown",
            "text": item["question"],
            "status": _unknown_task_status(item),
            "reason": item.get("resolution_strategy", ""),
            "trace": [],
        })
    return updates[:8]


def _step_result(final: dict, *, implementation_intent: bool = True) -> dict:
    blockers = [item for item in final.get("unknowns", []) if item.get("blocking")]
    # 已通过 clearify/调查得到答案的（resolutions 有记录）不再要求继续调查或重复弹窗
    resolved_ids = {
        str(item.get("unknown_id") or "").strip()
        for item in final.get("resolutions", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") in ("resolved", "partially_resolved", "deferred")
        and str(item.get("unknown_id") or "").strip()
    }
    investigate = [
        item for item in blockers
        if item.get("resolution_strategy") == "investigate_project"
        and not any(_same_unknown_id(item.get("id"), rid) for rid in resolved_ids)
    ]
    clearify = [item for item in blockers if item.get("resolution_strategy") == "clearify"]
    unresolved_ids = [item["id"] for item in blockers if item.get("id")]
    if final.get("runtime_failure") and (not blockers or _runtime_failure_blocks_continue(final)):
        failure_reason = (
            final.get("recovery_reason")
            or final.get("summary")
            or "Investigation failed before producing a valid final result."
        )
        return {
            "next_step": "failed",
            "continue_reason": failure_reason,
            "target_unknown_ids": unresolved_ids,
            "unresolved_unknown_ids": unresolved_ids,
            "summary": "",
            "beliefs": [],
            "ready_for_patch_planning": False,
            "patch_planning_context": [],
            "resolutions": [],
            "unknowns": final.get("unknowns", []),
        }
    if investigate:
        return {
            "next_step": "continue_investigation",
            "continue_reason": "; ".join(item["question"] for item in investigate[:3]),
            "target_unknown_ids": [item["id"] for item in investigate],
            "unresolved_unknown_ids": unresolved_ids,
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": False,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": final.get("unknowns", []),
        }
    # 阻塞但不需要项目调查的 unknown（engineering/product/clearify 等决策类）：
    # 继续调查，由 _pending_clearify_unknown 在 investigation_stream 内部强制
    # 进入 CLEARIFY 阶段（模型调 clearify 工具）——clearify 只有内部这一条路径。
    non_investigate = [
        item for item in blockers
        if item.get("resolution_strategy") != "investigate_project"
        and not any(_same_unknown_id(item.get("id"), rid) for rid in resolved_ids)
    ]
    if non_investigate:
        question = "; ".join(str(item.get("question") or "").strip() for item in non_investigate[:3])
        return {
            "next_step": "continue_investigation",
            "continue_reason": question or "Blocking decision unknowns require user input.",
            "target_unknown_ids": [item["id"] for item in non_investigate],
            "unresolved_unknown_ids": unresolved_ids,
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": False,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": final.get("unknowns", []),
        }
    if final.get("ready_for_patch_planning") and implementation_intent:
        return {
            "next_step": "write_code",
            "continue_reason": final.get("summary") or app_settings.text("ready_patch"),
            "target_unknown_ids": [],
            "unresolved_unknown_ids": [],
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": True,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": [],
        }
    if final.get("ready_for_patch_planning") and not implementation_intent:
        return {
            "next_step": "done",
            "continue_reason": final.get("summary") or "Investigation complete.",
            "target_unknown_ids": [],
            "unresolved_unknown_ids": [],
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": False,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": [],
        }
    readiness = final.get("readiness")
    readiness_reasons = [
        str(reason)
        for reason in (readiness.get("reasons", []) if isinstance(readiness, dict) else [])
        if str(reason).strip()
    ]
    if implementation_intent and (
        readiness_reasons
        or str(final.get("recommended_next_step") or "").strip() == "patch_planning"
    ):
        unresolved = readiness_reasons or ["patch_planning_not_ready"]
        return {
            "next_step": "continue_investigation",
            "continue_reason": "Patch planning readiness is incomplete: " + "; ".join(unresolved[:3]),
            "target_unknown_ids": unresolved,
            "unresolved_unknown_ids": unresolved,
            "summary": final.get("summary", ""),
            "beliefs": final.get("beliefs", []),
            "ready_for_patch_planning": False,
            "patch_planning_context": final.get("patch_planning_context", []),
            "resolutions": final.get("resolutions", []),
            "unknowns": final.get("unknowns", []),
        }
    open_questions = final.get("open_questions") or []
    question = str(open_questions[0]) if open_questions else ""
    return {
        "next_step": "continue_investigation" if open_questions else "done",
        "continue_reason": question
        or final.get("summary")
        or "Investigation complete.",
        "target_unknown_ids": [],
        "unresolved_unknown_ids": unresolved_ids,
        "summary": final.get("summary", "") if open_questions else "",
        "beliefs": final.get("beliefs", []),
        "ready_for_patch_planning": False,
        "patch_planning_context": final.get("patch_planning_context", []),
        "resolutions": final.get("resolutions", []),
        "unknowns": final.get("unknowns", []),
    }


def _runtime_failure_blocks_continue(final: dict) -> bool:
    reason = str(final.get("recovery_reason") or final.get("summary") or "")
    return (
        "repeated tool argument errors" in reason
        or "identical failed tool call loop" in reason
    )


def _unknowns(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    try:
        return _contract_unknowns(value)
    except ValueError:
        return []


def _summary(final: dict) -> str:
    lines = [final.get("summary") or "Investigation complete."]
    if final.get("open_questions"):
        lines.append(f"\n{app_settings.text('summary_open_questions')}")
        lines.extend(f"- {item}" for item in final["open_questions"][:5])
    return "\n".join(lines)
