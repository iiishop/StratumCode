from __future__ import annotations

import json

from .constants import CLEARIFY_RESOLUTION_REASON
from .domain import (
    _recorded_resolves_initial_unknowns,
    _reference_list,
    _semantic_repair_payload,
)
from .evidence import _resolution_evidence_lines
from .state import InvestigationPhase
from .tools import _phase_tool_choice, _phase_tools


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


def _clearify_required_prompt(unknown: dict) -> str:
    return "\n".join([
        "A blocking product decision requires the user's answer now.",
        f"Unknown id: {unknown['id']}",
        f"Question: {unknown['question']}",
        "Call clearify with this target_unknown_id and exactly three concrete candidate answers.",
        "Do not call discovery or finish tools before the user answers.",
    ])


def _discover_lsp_first_prompt(analysis: dict) -> str | None:
    if not any(
        isinstance(item, dict)
        and item.get("resolution_strategy") == "investigate_project"
        for item in analysis.get("unknowns", [])
    ):
        return None
    return (
        "Discovery routing: for source-code questions, you MUST start with LSP "
        "navigation before whole-file reads. Use code_nav(operation='symbols', "
        "path=...) to list a file's symbols; code_nav(operation='definition' or "
        "'references' or 'inspect', symbol=..., path=...) to resolve a symbol; "
        "then read only the relevant line ranges as grounding evidence. "
        "Looking up a function/class/variable's definition, references, or call "
        "sites is a semantic query — you MUST use code_nav for it, even if the "
        "symbol name looks like a plain string; grep for such lookups is "
        "forbidden. grep is only for literal text patterns that are NOT symbol "
        "lookups (error strings, log text, comments, UI labels). Do not read an "
        "entire file just to locate a symbol's usages: code_nav references "
        "returns the exact sites; read only those cited line ranges. If code_nav "
        "reports an unavailable language server, use lsp_tool status/install "
        "once for that language; if LSP remains unavailable, fall back to "
        "grep/read and record that fallback. For cross-file, "
        "parent/caller, consumer, or state-transition claims, gather semantic "
        "references or the corresponding caller/consumer observations before "
        "resolving."
    )


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
    """集中计算当前调查阶段与对应工具集（唯一决策入口）。"""
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
