from __future__ import annotations

from .. import chat
from .investigation_contracts import InvestigationStreamResult, InvestigationTransitionDecision
from .task_analysis import _analysis_requests_implementation
from .task_updates import _investigation_continuation_findings, _merge_findings

MAX_INVESTIGATION_PASSES = 3


class InvestigationTransitionPolicy:
    def decide(self, run, result: InvestigationStreamResult) -> InvestigationTransitionDecision:
        investigation = result.investigation or {}
        next_step = (investigation.get("step_result") or {}).get("next_step")
        blocking_unknown_ids = _blocking_unknown_ids(investigation)
        has_blocked_task = _has_task_status(investigation, "blocked")
        has_unknown_task = _has_task_status(investigation, "unknown")

        if (
            investigation
            and _investigation_allows_patch(investigation)
            and _analysis_requests_implementation(run.analysis)
        ):
            return InvestigationTransitionDecision(
                chat.ChatState.DESIGNING,
                "Investigation is ready for implementation planning.",
            )

        if next_step == "done":
            if _analysis_requests_implementation(run.analysis):
                return InvestigationTransitionDecision(
                    chat.ChatState.FAILED,
                    "Investigation ended without an implementation path.",
                )
            return InvestigationTransitionDecision(
                chat._chat_finish_state(run),
                "Investigation ended without an implementation path.",
            )

        if next_step == "failed" and blocking_unknown_ids:
            step = investigation.setdefault("step_result", {})
            step["next_step"] = "continue_investigation"
            step["target_unknown_ids"] = blocking_unknown_ids
            step["unresolved_unknown_ids"] = blocking_unknown_ids
            run.findings = _merge_findings(
                run.findings,
                _investigation_continuation_findings(investigation),
            )
            run.investigation_passes += 1
            if run.investigation_passes >= MAX_INVESTIGATION_PASSES:
                return _pass_limit_decision(run)
            return InvestigationTransitionDecision(
                chat.ChatState.INVESTIGATING,
                "Investigation still has unresolved blocking unknowns.",
            )

        if next_step == "failed":
            if investigation and _recorded_covers_unknowns(investigation, run.analysis):
                run.investigation_passes += 1
                if run.investigation_passes >= MAX_INVESTIGATION_PASSES:
                    return _pass_limit_decision(run)
                return InvestigationTransitionDecision(
                    chat.ChatState.INVESTIGATING,
                    "Investigation facts are complete; retrying finish.",
                )
            return InvestigationTransitionDecision(chat.ChatState.FAILED, "Investigation failed.")

        if next_step == "continue_investigation" or has_blocked_task or has_unknown_task:
            run.investigation_passes += 1
            run.findings = _merge_findings(
                run.findings,
                _investigation_continuation_findings(investigation),
            )
            if run.investigation_passes >= MAX_INVESTIGATION_PASSES:
                return _pass_limit_decision(run)
            return InvestigationTransitionDecision(
                chat.ChatState.INVESTIGATING,
                "Investigation requested another pass.",
            )

        if _analysis_requests_implementation(run.analysis):
            return InvestigationTransitionDecision(
                chat.ChatState.FAILED,
                "Investigation ended without an implementation path.",
            )
        return InvestigationTransitionDecision(
            chat._chat_finish_state(run),
            "Investigation ended without an implementation path.",
        )


def _pass_limit_decision(run) -> InvestigationTransitionDecision:
    if _analysis_requests_implementation(run.analysis):
        return InvestigationTransitionDecision(
            chat.ChatState.FAILED,
            "Investigation exceeded the maximum pass limit without resolving blockers.",
        )
    return InvestigationTransitionDecision(
        chat._chat_finish_state(run),
        "Investigation exceeded the maximum pass limit without resolving blockers.",
    )


def _investigation_allows_patch(investigation: dict) -> bool:
    if _has_task_status(investigation, "blocked"):
        return False
    if _has_task_status(investigation, "unknown"):
        return False
    if _has_blocking_unknown(investigation):
        return False
    raw_step = investigation.get("step_result")
    step: dict = raw_step if isinstance(raw_step, dict) else {}
    return bool(investigation.get("ready_for_patch_planning") or step.get("next_step") == "write_code")


def _has_open_tasks(investigation: dict) -> bool:
    return _has_task_status(investigation, "blocked") or _has_task_status(investigation, "unknown")


def _has_task_status(investigation: dict | None, status: str) -> bool:
    if not investigation:
        return False
    for item in investigation.get("task_updates", []) if isinstance(investigation.get("task_updates"), list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "hypothesis":
            continue
        if item.get("status") == status:
            return True
    return False


def _has_blocking_unknown(investigation: dict | None) -> bool:
    if not investigation:
        return False
    tasks = investigation.get("task_updates", [])
    if not isinstance(tasks, list):
        return False
    for item in tasks:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "hypothesis":
            continue
        if item.get("status") == "unknown" and item.get("blocking"):
            return True
    return False


def _recorded_covers_unknowns(investigation: dict | None, analysis: dict | None) -> bool:
    if not investigation or not analysis:
        return False
    recorded_unknown_ids = {
        str(r.get("unknown_id") or "")
        for r in investigation.get("resolutions", [])
        if isinstance(r, dict)
        and str(r.get("status") or "") in ("resolved", "partially_resolved", "deferred")
    }
    unknown_ids = {
        str(u.get("id") or "")
        for u in analysis.get("unknowns", [])
        if isinstance(u, dict)
        and u.get("blocking")
    }
    return bool(unknown_ids and unknown_ids <= recorded_unknown_ids)


def _blocking_unknown_ids(investigation: dict | None) -> list[str]:
    if not investigation or not isinstance(investigation.get("unknowns"), list):
        return []
    return [
        str(item.get("id") or "").strip()
        for item in investigation["unknowns"]
        if isinstance(item, dict)
        and item.get("blocking")
        and str(item.get("id") or "").strip()
    ]
