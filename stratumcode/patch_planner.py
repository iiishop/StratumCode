from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from itertools import count
from pathlib import Path
from uuid import uuid4

from . import app_settings, model_settings, patch_authorization, prompt, providers
from .planning_facts import normalize_project_facts as _project_facts
from .tools.builtin.common import IGNORED_DIRS
from .agent_runtime import (
    add_usage as _add_usage,
    call_model as _call_model,
    content_text as _content_text,
    empty_usage as _empty_usage,
    stage_progress,
    start_event,
    usage_delta as _usage_delta,
)

DEFAULT_PATCH_JSON_ATTEMPTS = 3
NO_CHANGE_ACTION_MARKERS = (
    "manual review",
    "manual inspection",
    "verification only",
    "no code change",
    "no additional code",
    "already implemented",
    "already satisfied",
    "手动检查",
    "人工检查",
    "仅检查",
    "无需代码",
    "无需额外代码",
    "不需要代码修改",
    "已经实现",
    "已实现",
    "已满足",
)


def patch_planning_stream(
    *,
    message: str,
    analysis: dict,
    investigation: dict,
    design_plan: dict,
    workspace_dir: str,
    revision_context: list[str] | None = None,
) -> Iterator[dict]:
    prerequisite = _patch_planning_prerequisite(analysis, investigation)
    if prerequisite:
        next_state, reason = prerequisite
        yield start_event(f"patch-prerequisite-{uuid4().hex[:8]}", "output", {
            "content": reason,
            "streaming": False,
        })
        yield {"op": "done", "next_state": next_state, "reason": reason}
        return

    setting = model_settings.resolve(model_settings.DEFAULT_STAGE)
    if setting is None:
        raise ValueError("No model configured for patch planning. Configure a default model in Providers.")

    provider = setting["provider"]
    model = setting["model_id"]
    pricing_rules = providers.get_model_pricing(provider["id"], model)
    usage_total = _empty_usage(pricing_rules)
    run_id = uuid4().hex[:10]
    stage_id = f"{run_id}-stage"
    progress = []

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
    feedback = "\n".join(f"- {line}" for line in (revision_context or []) if str(line or "").strip())
    if feedback:
        system = {
            "role": "system",
            "content": (
                system["content"]
                + "\n\nPLAN REVISION FEEDBACK\n"
                + "The previous patch plan conflicted during implementation. Fix the plan accordingly "
                + "(correct wrong completion conditions, resolve contradictions, and keep the rest stable).\n"
                + feedback
            ),
        }
    step_content = []
    tests_or_checks = []
    risks = []
    out_of_scope = _strings(design_plan.get("out_of_scope"))
    acceptance_verification = {}
    skipped_decision_slots = []
    facts = _project_facts(investigation)
    acceptance_count = len(analysis.get("acceptance_criteria", []))
    decisions = [
        item for item in design_plan.get("design_decisions", [])
        if isinstance(item, dict)
    ]
    revision_ids = (
        set(_strings(design_plan.get("runtime_revision_decision_ids")))
        if "runtime_revision_decision_ids" in design_plan
        else None
    )
    for index, decision in enumerate(decisions, start=1):
        if revision_ids is not None and str(decision.get("id") or "") not in revision_ids:
            continue
        progress_id = f"decision-{index}"
        progress_label = f"Plan {decision.get('id') or index}"
        progress_description = str(decision.get("decision") or "")
        messages = [
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
        ]
        slot = None
        slot_issues = []
        attempts = app_settings.get_round_limit("patch_json_attempts") or DEFAULT_PATCH_JSON_ATTEMPTS
        for semantic_attempt in _attempt_indexes(attempts):
            yield stage_progress(
                stage_id,
                progress,
                progress_id,
                progress_label,
                description=progress_description,
                detail=f"Attempt {semantic_attempt}",
            )
            slot = yield from _content_json_stream(
                provider,
                model,
                messages,
                pricing_rules,
                usage_total,
                run_id,
                f"decision-{index}-{semantic_attempt}",
            )
            _normalize_no_change_slot(slot)
            slot_issues = _slot_step_issues(
                slot,
                workspace_dir,
                acceptance_count=acceptance_count,
                fact_count=len(facts),
            )
            if not slot or not slot_issues:
                break
            messages.extend([
                {"role": "assistant", "content": json.dumps(slot, ensure_ascii=False)[:4000]},
                {"role": "user", "content": (
                    "The previous slot content was semantically unusable:\n- "
                    + "\n- ".join(slot_issues)
                    + "\nUse exact workspace-relative paths from project facts. "
                    "Return the corrected slot JSON only. If this design decision genuinely "
                    "needs no code change, set needed=false and include skip_reason."
                )},
            ])
        if slot and slot_issues:
            reason = "Patch step remains ungrounded after semantic repair: " + "; ".join(slot_issues)
            yield stage_progress(
                stage_id,
                progress,
                progress_id,
                progress_label,
                description=progress_description,
                detail="Project grounding failed",
                state="error",
            )
            yield start_event(f"{run_id}-ungrounded", "output", {
                "content": reason,
                "streaming": False,
            })
            if _slot_issues_need_investigation(slot_issues):
                yield {"op": "done", "next_state": "investigating", "reason": reason}
            else:
                yield {"op": "update", "id": stage_id, "patch": {
                    "state": "error",
                    "phase": "patch_planning_failed",
                }}
            return
        if not slot:
            yield stage_progress(
                stage_id,
                progress,
                progress_id,
                progress_label,
                description=progress_description,
                detail="No usable step returned",
                state="done",
            )
            continue
        tests_or_checks.extend(_strings(slot.get("tests_or_checks")))
        risks.extend(_strings(slot.get("risks")))
        _merge_acceptance_verification(acceptance_verification, slot.get("acceptance_verification"))
        if slot.get("needed") is False:
            yield stage_progress(
                stage_id,
                progress,
                progress_id,
                progress_label,
                description=progress_description,
                detail="No code change required",
                state="done",
            )
            skipped_decision_slots.append({
                "decision_slot": index,
                "reason": str(slot.get("skip_reason") or "Model marked this design decision as requiring no code change.").strip(),
                "project_fact_slots": _numbered_slots(
                    slot.get("skip_project_fact_slots"),
                    len(facts),
                ),
            })
            continue
        _append_slot_steps(
            step_content,
            slot.get("step_content"),
            decision_slot=index,
        )
        yield stage_progress(
            stage_id,
            progress,
            progress_id,
            progress_label,
            description=progress_description,
            detail=f"{len(slot.get('step_content') or [])} steps",
            state="done",
        )
    verification = {
        "tests_or_checks": tests_or_checks,
        "acceptance_verification": [
            {"acceptance_slot": key, "verification": value}
            for key, value in sorted(acceptance_verification.items())
        ],
    }
    required_skip_slots = {
        int(item["decision_slot"])
        for item in skipped_decision_slots
    }
    verification_issues = ["semantic patch verification has not run"]
    if step_content or skipped_decision_slots:
        messages = [
            system,
            {"role": "user", "content": prompt.build_patch_verification_slot_user(
                message,
                analysis,
                investigation,
                design_plan,
                workspace_dir,
                step_content,
                skipped_decision_slots=skipped_decision_slots,
            )},
        ]
        attempts = app_settings.get_round_limit("patch_json_attempts") or DEFAULT_PATCH_JSON_ATTEMPTS
        for semantic_attempt in _attempt_indexes(attempts):
            yield stage_progress(
                stage_id,
                progress,
                "patch-verification",
                "Patch verification",
                description="Map planned steps and checks back to acceptance criteria.",
                detail=f"Attempt {semantic_attempt}",
            )
            verification = yield from _content_json_stream(
                provider,
                model,
                messages,
                pricing_rules,
                usage_total,
                run_id,
                f"verification-{semantic_attempt}",
            )
            verification_issues = _verification_slot_issues(
                verification,
                analysis,
                step_content,
                facts,
                required_skip_slots,
            )
            if not verification_issues:
                break
            messages.extend([
                {"role": "assistant", "content": json.dumps(verification or {}, ensure_ascii=False)[:4000]},
                {"role": "user", "content": (
                    "The patch verification slot is incomplete:\n- "
                    + "\n- ".join(verification_issues)
                    + "\nReturn only corrected patch_verification JSON."
                )},
            ])
        if verification_issues:
            reason = "Patch verification remains invalid after semantic repair: " + "; ".join(verification_issues)
            yield stage_progress(
                stage_id,
                progress,
                "patch-verification",
                "Patch verification",
                description="Map planned steps and checks back to acceptance criteria.",
                detail="Verification failed",
                state="error",
            )
            yield start_event(f"{run_id}-verification-failed", "output", {
                "content": reason,
                "streaming": False,
            })
            yield {"op": "update", "id": stage_id, "patch": {
                "state": "error",
                "phase": "patch_planning_failed",
            }}
            return
        yield stage_progress(
            stage_id,
            progress,
            "patch-verification",
            "Patch verification",
            description="Map planned steps and checks back to acceptance criteria.",
            detail=f"{len(verification.get('tests_or_checks') or [])} checks",
            state="done",
        )
        audit_messages = [
            {
                "role": "system",
                "content": prompt.build_patch_verification_auditor_system(
                    app_settings.get_output_language()
                ),
            },
            {"role": "user", "content": prompt.build_patch_verification_slot_user(
                message,
                analysis,
                investigation,
                design_plan,
                workspace_dir,
                step_content,
                candidate_verification=verification,
                skipped_decision_slots=skipped_decision_slots,
            )},
        ]
        audit_issues = []
        for semantic_attempt in _attempt_indexes(attempts):
            yield stage_progress(
                stage_id,
                progress,
                "verification-audit",
                "Verification content audit",
                description="Challenge the proposed coverage, checks, and skipped decisions.",
                detail=f"Attempt {semantic_attempt}",
            )
            audited = yield from _content_json_stream(
                provider,
                model,
                audit_messages,
                pricing_rules,
                usage_total,
                run_id,
                f"verification-audit-{semantic_attempt}",
            )
            audit_issues = _verification_slot_issues(
                audited,
                analysis,
                step_content,
                facts,
                required_skip_slots,
            )
            if not audit_issues:
                verification = audited
                break
            audit_messages.extend([
                {"role": "assistant", "content": json.dumps(audited or {}, ensure_ascii=False)[:4000]},
                {"role": "user", "content": (
                    "The semantic audit output is incomplete:\n- "
                    + "\n- ".join(audit_issues)
                    + "\nReturn only corrected patch_verification JSON."
                )},
            ])
        else:
            reason = "Patch verification audit remains invalid after repair: " + "; ".join(audit_issues)
            yield stage_progress(
                stage_id,
                progress,
                "verification-audit",
                "Verification content audit",
                description="Challenge the proposed coverage, checks, and skipped decisions.",
                detail="Audit failed",
                state="error",
            )
            yield start_event(f"{run_id}-verification-audit-failed", "output", {
                "content": reason,
                "streaming": False,
            })
            yield {"op": "update", "id": stage_id, "patch": {
                "state": "error",
                "phase": "patch_planning_failed",
            }}
            return
        yield stage_progress(
            stage_id,
            progress,
            "verification-audit",
            "Verification content audit",
            description="Challenge the proposed coverage, checks, and skipped decisions.",
            detail="Audit passed",
            state="done",
        )
        rejected_skips = _rejected_skip_reviews(verification.get("skip_reviews"))
        if rejected_skips:
            reason = "Patch verification rejected runtime skip candidates: " + "; ".join(
                str(item.get("reason") or item.get("decision_slot"))
                for item in rejected_skips
            )
            yield start_event(f"{run_id}-skip-rejected", "output", {
                "content": reason,
                "streaming": False,
            })
            # 给模型修正机会：去掉无效 skip 或改为具体代码修改，而不是直接判失败
            skip_repair_ok = False
            for skip_repair_attempt in _attempt_indexes(attempts):
                messages.extend([
                    {"role": "assistant", "content": json.dumps(verification or {}, ensure_ascii=False)[:4000]},
                    {"role": "user", "content": (
                        reason
                        + "\nRemove the rejected runtime skip candidates or replace them "
                        + "with concrete code changes, then return corrected patch_verification JSON."
                    )},
                ])
                verification = yield from _content_json_stream(
                    provider,
                    model,
                    messages,
                    pricing_rules,
                    usage_total,
                    run_id,
                    f"verification-skip-repair-{skip_repair_attempt}",
                )
                rejected_skips = _rejected_skip_reviews(verification.get("skip_reviews"))
                if not rejected_skips:
                    skip_repair_ok = True
                    break
            if not skip_repair_ok:
                reason = "Patch verification rejected runtime skip candidates after repair: " + "; ".join(
                    str(item.get("reason") or item.get("decision_slot"))
                    for item in rejected_skips
                )
                yield start_event(f"{run_id}-skip-rejected", "output", {
                    "content": reason,
                    "streaming": False,
                })
                yield {"op": "update", "id": stage_id, "patch": {
                    "state": "error",
                    "phase": "patch_planning_failed",
                }}
                return
        if isinstance(verification, dict):
            _merge_acceptance_verification(
                acceptance_verification,
                verification.get("acceptance_verification"),
            )
            _merge_step_acceptance_coverage(
                step_content,
                verification.get("step_acceptance_coverage"),
                len(analysis.get("acceptance_criteria", [])),
            )
            _apply_verified_step_revisions(
                step_content,
                verification.get("step_revisions"),
            )
            _merge_verified_step_groups(
                step_content,
                verification.get("step_merge_groups"),
            )
            tests_or_checks = _canonical_verification_checks(
                verification,
                step_content,
                facts,
            )
    plan_content = {
        "summary": _patch_summary(step_content),
        "step_content": step_content,
        "tests_or_checks": _unique_strings(tests_or_checks),
        "risks": _unique_strings(risks),
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
        fatal = any(
            "runtime_revision_decision_ids" in item
            or "decision has no evidence" in item
            for item in issues
        )
        if fatal:
            yield start_event(f"{run_id}-output", "output", {
                "content": "Patch plan rejected by runtime validator:\n" + "\n".join(f"- {item}" for item in issues),
                "streaming": False,
            })
            yield {"op": "update", "id": stage_id, "patch": {"state": "error", "phase": "patch_validation_failed"}}
            return
        yield start_event(f"{run_id}-repair-hint", "output", {
            "content": (
                "Patch plan needs minor fixes:\n"
                + "\n".join(f"- {item}" for item in issues)
            ),
            "streaming": False,
        })
        yield {"op": "update", "id": stage_id, "patch": {"state": "running", "phase": "repairing"}}
        plan["_repair_issues"] = issues
        plan["execution_authorization"] = patch_authorization.create_authorization(plan, workspace_dir)
        yield start_event(f"{run_id}-plan", "patch_plan", plan)
        yield {"op": "update", "id": stage_id, "patch": {"state": "done", "phase": "needs_repair"}}
        yield {"op": "done", "patch_plan": plan, "repair_needed": True}
        return
    plan["execution_authorization"] = patch_authorization.create_authorization(plan, workspace_dir)
    yield start_event(f"{run_id}-plan", "patch_plan", plan)
    yield {"op": "update", "id": stage_id, "patch": {"state": "done", "phase": "patch_planned"}}
    yield {"op": "done", "patch_plan": plan}


def _patch_planning_prerequisite(
    analysis: dict, investigation: dict
) -> tuple[str, str] | None:
    implementation = analysis.get("intent", {}).get("type") in {"feature", "bugfix", "refactor"}
    if implementation and not analysis.get("acceptance_criteria"):
        return "analyzing", "Patch planning requires a non-empty canonical acceptance contract."
    if investigation.get("runtime_recovered"):
        return "investigating", "Patch planning requires grounded investigation facts, not runtime recovery output."
    if implementation and not _project_facts(investigation):
        return "investigating", "Patch planning requires at least one grounded project fact."
    return None


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
    if "runtime_revision_decision_ids" in design_plan:
        revision_decision_ids = set(_strings(design_plan.get("runtime_revision_decision_ids")))
        unknown_revision_decisions = sorted(revision_decision_ids - decision_ids)
        if unknown_revision_decisions:
            issues.append(
                "runtime_revision_decision_ids references unknown design decisions: "
                + ", ".join(unknown_revision_decisions)
            )
        required_decision_ids = decision_ids & revision_decision_ids
    else:
        required_decision_ids = decision_ids
    required_decision_ids -= skipped_decision_ids
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
    for item in plan.get("runtime_skipped_decisions") or []:
        if not isinstance(item, dict):
            continue
        decision_id = str(item.get("decision_id") or "?")
        if not str(item.get("reason") or "").strip():
            issues.append(f"runtime_skipped_decisions {decision_id} has no reason")
        refs = set(item.get("project_fact_ids") or [])
        if fact_ids and not refs:
            issues.append(f"runtime_skipped_decisions {decision_id} has no project_fact_ids")
        unknown_refs = sorted(refs - fact_ids)
        if unknown_refs:
            issues.append(
                f"runtime_skipped_decisions {decision_id} references unknown project facts: "
                + ", ".join(unknown_refs)
            )
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
    seen_responsibilities: dict[tuple[str, str, str], str] = {}
    steps_by_file: dict[str, list[dict]] = {}
    for item in steps:
        key = _step_responsibility_key(item)
        if key in seen_responsibilities:
            issues.append(
                f"duplicate implementation responsibility: {item.get('id') or '?'} "
                f"duplicates {seen_responsibilities[key]}"
            )
        else:
            seen_responsibilities[key] = str(item.get("id") or "?")
        if item.get("file"):
            steps_by_file.setdefault(str(item["file"]), []).append(item)
    for file, file_steps in steps_by_file.items():
        modes = {str(item.get("mode") or "modify") for item in file_steps}
        if len(modes) > 1:
            issues.append(f"file has conflicting create/modify steps: {file}")
        if "create" in modes and len(file_steps) > 1:
            issues.append(f"created file must have exactly one implementation step: {file}")
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
    available_step_ids = ", ".join(sorted(step_ids)[:10]) or "(none)"
    for item in plan.get("acceptance_mapping", []):
        if item.get("acceptance_id") not in criteria_ids:
            issues.append(f"acceptance_mapping references unknown acceptance id: {item.get('acceptance_id')}")
        if not item.get("covered_by") and not no_patch_plan:
            issues.append(f"acceptance_mapping {item.get('acceptance_id') or '?'} has no covered_by steps (available step IDs: {available_step_ids})")
        missing_steps = [step for step in item.get("covered_by", []) if step not in step_ids]
        if missing_steps:
            issues.append(f"acceptance_mapping {item.get('acceptance_id') or '?'} references unknown steps: {', '.join(missing_steps)}. Available step IDs: {available_step_ids}")
        if not item.get("verification"):
            issues.append(f"acceptance_mapping {item.get('acceptance_id') or '?'} has no verification")
    workspace = Path(workspace_dir or ".").resolve()
    workspace_files = _known_workspace_files(workspace)
    for step in steps:
        step_id = step.get("id") or "?"
        if not step.get("id"):
            issues.append("implementation step is missing id")
        if not step.get("purpose"):
            issues.append(f"step {step_id} has no purpose")
        if not step.get("file"):
            issues.append(f"step {step_id} has no file")
            continue
        mode = str(step.get("mode") or "modify").strip()
        if mode not in {"modify", "create"}:
            issues.append(f"step {step_id} has invalid mode: {mode}")
        if not step.get("target"):
            issues.append(f"step {step_id} has no target")
        if file_issue := _planned_file_issue(step["file"], mode, workspace):
            issues.append(f"step {step_id} {file_issue}")
        mentioned_files = _mentioned_workspace_files(step, workspace_files)
        structured_file = str(step.get("file") or "").replace("\\", "/")
        reference_files = _derived_reference_files(
            step,
            analysis,
            design_plan,
            workspace,
            workspace_files,
            step_id,
            issues,
        )
        boundary_files = _decision_boundary_files(
            step,
            design_plan,
            workspace_files,
        )
        unlisted_files = sorted(mentioned_files - {structured_file, *reference_files, *boundary_files})
        if unlisted_files:
            issues.append(
                f"step {step_id} references files outside its structured file: "
                + ", ".join(unlisted_files)
            )
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
        assistant = _call_model(provider, model, messages, tools=[])
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


def _skipped_decisions(value, design_plan: dict, facts: list[dict]) -> list[dict]:
    decision_ids = _known_ids(design_plan.get("design_decisions"))
    fact_ids = _known_ids(facts)
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
                "project_fact_ids": _slot_ids(item.get("project_fact_slots"), fact_ids),
            })
    return result


def _plan_from_content(data: dict, analysis: dict, design_plan: dict, investigation: dict) -> dict:
    facts = _project_facts(investigation)
    steps = _runtime_steps(data.get("step_content"), analysis, design_plan, facts)
    skipped_decisions = _skipped_decisions(data.get("skipped_decision_slots"), design_plan, facts)
    if not steps and not skipped_decisions:
        raise ValueError("step_content must contain at least one implementation step")
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
        "tests_or_checks": _unique_strings(data.get("tests_or_checks")),
        "risks": _unique_strings(data.get("risks")),
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
        acceptance_ids = _unique_strings(
            acceptance_ids + _decision_acceptance_ids(decision_refs, design_plan)
        )
        fact_refs = _slot_ids(item.get("project_fact_slots"), fact_ids)
        steps.append({
            "id": f"IS{index}",
            "mode": str(item.get("mode") or "modify").strip(),
            "purpose": str(item.get("purpose") or "").strip(),
            "responsibility_key": str(item.get("responsibility_key") or "").strip(),
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
    return _merge_duplicate_responsibilities(steps)


def _merge_duplicate_responsibilities(steps: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str, str], dict] = {}
    for step in steps:
        key = _step_responsibility_key(step)
        if key not in merged:
            merged[key] = step
            continue
        destination = merged[key]
        for field in (
            "acceptance_ids",
            "decision_ids",
            "project_fact_ids",
            "completion_conditions",
            "out_of_scope",
        ):
            destination[field] = _unique_strings(
                list(destination.get(field) or []) + list(step.get(field) or [])
            )
        for field in (
            "purpose",
            "responsibility_key",
            "action",
            "required_behavior_if_removed",
            "minimality_check",
        ):
            destination[field] = " ".join(_unique_strings([
                destination.get(field),
                step.get(field),
            ]))
    result = list(merged.values())
    for index, step in enumerate(result, start=1):
        step["id"] = f"IS{index}"
    return result


def _has_usable_steps(value) -> bool:
    return isinstance(value, list) and any(isinstance(item, dict) and str(item.get("file") or "").strip() for item in value)


def _normalize_no_change_slot(slot: dict | None) -> None:
    if not isinstance(slot, dict) or slot.get("needed") is False:
        return
    steps = [item for item in slot.get("step_content") or [] if isinstance(item, dict)]
    if not steps or not all(_is_no_change_action(item) for item in steps):
        return
    fact_slots = sorted({
        fact_slot
        for item in steps
        for fact_slot in _numbered_slots(item.get("project_fact_slots"), 10_000)
    })
    slot.update({
        "needed": False,
        "skip_reason": "Runtime normalized verification-only steps that explicitly require no code change.",
        "skip_project_fact_slots": fact_slots,
        "step_content": [],
    })


def _is_no_change_action(step: dict) -> bool:
    text = " ".join([
        str(step.get("purpose") or ""),
        str(step.get("action") or ""),
        str(step.get("minimality_check") or ""),
    ]).casefold()
    return any(marker in text for marker in NO_CHANGE_ACTION_MARKERS)


def _slot_step_issues(
    slot: dict | None,
    workspace_dir: str,
    *,
    acceptance_count: int,
    fact_count: int,
) -> list[str]:
    if not slot:
        return []
    if slot.get("needed") is False:
        issues = []
        if not str(slot.get("skip_reason") or "").strip():
            issues.append("needed=false requires a concrete skip_reason")
        if fact_count and not _numbered_slots(slot.get("skip_project_fact_slots"), fact_count):
            issues.append("needed=false requires at least one skip_project_fact_slots item")
        return issues
    steps = slot.get("step_content")
    if not _has_usable_steps(steps):
        return ["needed=true requires at least one step_content item with a workspace-relative file"]
    workspace = Path(workspace_dir or ".").resolve()
    issues = []
    for index, item in enumerate(steps, start=1):
        if not isinstance(item, dict) or not str(item.get("file") or "").strip():
            continue
        mode = str(item.get("mode") or "modify").strip()
        if mode not in {"modify", "create"}:
            issues.append(f"step_content item {index} has invalid mode: {mode}")
            continue
        try:
            item["file"] = _canonical_workspace_file(str(item["file"]), workspace)
        except ValueError as exc:
            issues.append(f"step_content item {index} {exc}")
            continue
        if mode == "modify":
            item["file"] = _repair_unique_modify_target(str(item["file"]), workspace)
        if issue := _planned_file_issue(str(item["file"]), mode, workspace):
            issues.append(f"step_content item {index} {issue}")
        for field in ("purpose", "target", "action", "required_behavior_if_removed", "minimality_check"):
            if not str(item.get(field) or "").strip():
                issues.append(f"step_content item {index} has no {field}")
        if not _strings(item.get("completion_conditions")):
            issues.append(f"step_content item {index} has no completion_conditions")
        if acceptance_count and not _numbered_slots(item.get("acceptance_slots"), acceptance_count):
            issues.append(f"step_content item {index} has no valid acceptance_slots")
        if fact_count and not _numbered_slots(item.get("project_fact_slots"), fact_count):
            issues.append(f"step_content item {index} has no valid project_fact_slots")
    return issues


def _slot_issues_need_investigation(issues: list[str]) -> bool:
    return any(
        marker in issue
        for issue in issues
        for marker in (
            "outside workspace",
            "modify target does not exist",
            "create target already exists",
            "file path is invalid",
        )
    )


def _verification_slot_issues(
    slot: dict | None,
    analysis: dict,
    steps: list[dict],
    facts: list[dict],
    required_skip_slots: set[int] | None = None,
) -> list[str]:
    if not isinstance(slot, dict):
        return ["patch_verification must be a JSON object"]
    issues = []
    if (
        analysis.get("intent", {}).get("type") in {"feature", "bugfix"}
        and not _strings(slot.get("tests_or_checks"))
    ):
        issues.append("feature/bugfix requires at least one test or check")
    criteria = [
        item for item in analysis.get("acceptance_criteria", [])
        if isinstance(item, dict)
    ]
    covered = set()
    for item in slot.get("acceptance_verification") or []:
        if not isinstance(item, dict) or not str(item.get("verification") or "").strip():
            continue
        try:
            covered.add(int(item.get("acceptance_slot")))
        except (TypeError, ValueError):
            continue
    missing = [str(index) for index in range(1, len(criteria) + 1) if index not in covered]
    if missing:
        issues.append("acceptance verification missing slots: " + ", ".join(missing))
    step_coverage = {
        slot_number
        for step in steps
        if isinstance(step, dict)
        for slot_number in _numbered_slots(step.get("acceptance_slots"), len(criteria))
    }
    coverage_updates, coverage_issues = _step_coverage_updates(
        slot.get("step_acceptance_coverage"),
        len(steps),
        len(criteria),
    )
    issues.extend(coverage_issues)
    for acceptance_slots in coverage_updates.values():
        step_coverage.update(acceptance_slots)
    missing_coverage = [
        str(index)
        for index in range(1, len(criteria) + 1)
        if index not in step_coverage
    ]
    if missing_coverage and steps:
        issues.append("implementation step coverage missing acceptance slots: " + ", ".join(missing_coverage))
    issues.extend(_skip_review_issues(slot.get("skip_reviews"), required_skip_slots or set()))
    issues.extend(
        _check_grounding_issues(
            slot.get("check_grounding"),
            _strings(slot.get("tests_or_checks")),
            facts,
        )
    )
    _, merge_issues = _step_merge_groups(slot.get("step_merge_groups"), steps)
    issues.extend(merge_issues)
    _, revision_issues = _step_revision_updates(slot.get("step_revisions"), len(steps))
    issues.extend(revision_issues)
    return issues


def _skip_review_issues(value, required_slots: set[int]) -> list[str]:
    if not required_slots:
        return [] if value in (None, []) else ["skip_reviews must be empty without runtime skip candidates"]
    if not isinstance(value, list):
        return ["skip_reviews must be an array"]
    reviewed = set()
    issues = []
    for item in value:
        if not isinstance(item, dict):
            issues.append("skip_reviews contains a non-object item")
            continue
        try:
            slot = int(item.get("decision_slot"))
        except (TypeError, ValueError):
            issues.append("skip_reviews has an invalid decision_slot")
            continue
        if slot not in required_slots:
            issues.append(f"skip_reviews references unknown skip slot: {slot}")
            continue
        if slot in reviewed:
            issues.append(f"skip_reviews repeats decision slot: {slot}")
        reviewed.add(slot)
        if not isinstance(item.get("approved"), bool):
            issues.append(f"skip_reviews decision {slot} has no boolean approved value")
        if not str(item.get("reason") or "").strip():
            issues.append(f"skip_reviews decision {slot} has no reason")
    missing = sorted(required_slots - reviewed)
    if missing:
        issues.append("skip_reviews missing decision slots: " + ", ".join(map(str, missing)))
    return issues


def _rejected_skip_reviews(value) -> list[dict]:
    return [
        item
        for item in value or []
        if isinstance(item, dict) and item.get("approved") is False
    ]


def _check_grounding_issues(
    value,
    checks: list[str],
    facts: list[dict],
) -> list[str]:
    check_count = len(checks)
    if check_count == 0:
        return []
    if not isinstance(value, list):
        return ["check_grounding must be an array"]
    covered = set()
    issues = []
    for item in value:
        if not isinstance(item, dict):
            issues.append("check_grounding contains a non-object item")
            continue
        try:
            check_slot = int(item.get("check_slot"))
        except (TypeError, ValueError):
            issues.append("check_grounding has an invalid check_slot")
            continue
        if not 1 <= check_slot <= check_count:
            issues.append(f"check_grounding references unknown check slot: {check_slot}")
            continue
        if check_slot in covered:
            issues.append(f"check_grounding repeats check slot: {check_slot}")
        covered.add(check_slot)
        fact_slots = _numbered_slots(item.get("project_fact_slots"), len(facts))
        if facts and not fact_slots:
            issues.append(f"check_grounding check {check_slot} has no valid project fact slots")
        kind = str(item.get("kind") or "").strip()
        if kind not in {"manual", "command"}:
            issues.append(f"check_grounding check {check_slot} has invalid kind")
        if kind == "command":
            allowed_commands = {
                command
                for fact_slot in fact_slots
                for command in _strings(facts[fact_slot - 1].get("verification_commands"))
            }
            if checks[check_slot - 1] not in allowed_commands:
                issues.append(f"check_grounding check {check_slot} command is not authorized by project facts")
        if not str(item.get("reason") or "").strip():
            issues.append(f"check_grounding check {check_slot} has no reason")
    missing = sorted(set(range(1, check_count + 1)) - covered)
    if missing:
        issues.append("check_grounding missing check slots: " + ", ".join(map(str, missing)))
    return issues


def _canonical_verification_checks(slot: dict, steps: list[dict], facts: list[dict]) -> list[str]:
    checks = _strings(slot.get("tests_or_checks"))
    grounding = {
        int(item["check_slot"]): item
        for item in slot.get("check_grounding", [])
        if isinstance(item, dict) and str(item.get("check_slot") or "").isdigit()
    }
    result = []
    for check_slot, check in enumerate(checks, start=1):
        item = grounding.get(check_slot, {})
        if item.get("kind") == "command":
            result.append(check)
            continue
        if steps:
            targets = [
                f"{step.get('file')}::{step.get('target')}"
                for step in steps
            ]
            conditions = _unique_strings([
                condition
                for step in steps
                for condition in _strings(step.get("completion_conditions"))
            ])
            result.append(
                "Inspect "
                + ", ".join(targets)
                + " and confirm: "
                + "; ".join(conditions)
            )
            continue
        fact_slots = _numbered_slots(item.get("project_fact_slots"), len(facts))
        result.append(
            "Confirm the observed project facts remain true: "
            + "; ".join(facts[index - 1]["text"] for index in fact_slots)
        )
    return _unique_strings(result)


def _step_merge_groups(value, steps: int | list[dict]) -> tuple[list[dict], list[str]]:
    if value is None:
        return [], []
    if not isinstance(value, list):
        return [], ["step_merge_groups must be an array"]
    step_count = steps if isinstance(steps, int) else len(steps)
    groups = []
    used = set()
    issues = []
    for item in value:
        if not isinstance(item, dict):
            issues.append("step_merge_groups contains a non-object item")
            continue
        slots = _numbered_slots(item.get("step_slots"), step_count)
        if len(slots) < 2:
            issues.append("step_merge_groups item requires at least two valid step slots")
            continue
        overlap = used & set(slots)
        if overlap:
            issues.append("step_merge_groups repeats step slots: " + ", ".join(map(str, sorted(overlap))))
            continue
        if isinstance(steps, list):
            files = {
                str(steps[slot - 1].get("file") or "").replace("\\", "/").casefold()
                for slot in slots
            }
            modes = {
                str(steps[slot - 1].get("mode") or "modify").strip().casefold()
                for slot in slots
            }
            if len(files) != 1:
                issues.append("step_merge_groups cannot merge steps from different files")
                continue
            if len(modes) != 1:
                issues.append("step_merge_groups cannot merge steps with different modes")
                continue
        if not str(item.get("reason") or "").strip():
            issues.append("step_merge_groups item has no reason")
            continue
        issue_count = len(issues)
        merged = item.get("merged_content")
        if not isinstance(merged, dict):
            issues.append("step_merge_groups item has no merged_content")
            continue
        for field in ("purpose", "target", "action", "required_behavior_if_removed", "minimality_check"):
            if not str(merged.get(field) or "").strip():
                issues.append(f"step_merge_groups merged_content has no {field}")
        if not _strings(merged.get("completion_conditions")):
            issues.append("step_merge_groups merged_content has no completion_conditions")
        if len(issues) > issue_count:
            continue
        used.update(slots)
        groups.append({"slots": slots, "content": merged})
    return groups, issues


def _step_revision_updates(value, step_count: int) -> tuple[dict[int, dict], list[str]]:
    if value is None:
        return {}, []
    if not isinstance(value, list):
        return {}, ["step_revisions must be an array"]
    updates = {}
    issues = []
    for item in value:
        if not isinstance(item, dict):
            issues.append("step_revisions contains a non-object item")
            continue
        try:
            slot = int(item.get("step_slot"))
        except (TypeError, ValueError):
            issues.append("step_revisions has an invalid step_slot")
            continue
        if not 1 <= slot <= step_count:
            issues.append(f"step_revisions references unknown step slot: {slot}")
            continue
        if slot in updates:
            issues.append(f"step_revisions repeats step slot: {slot}")
            continue
        if not str(item.get("reason") or "").strip():
            issues.append(f"step_revisions step {slot} has no reason")
        revised = item.get("revised_content")
        if not isinstance(revised, dict):
            issues.append(f"step_revisions step {slot} has no revised_content")
            continue
        for field in ("purpose", "target", "action", "required_behavior_if_removed", "minimality_check"):
            if not str(revised.get(field) or "").strip():
                issues.append(f"step_revisions step {slot} revised_content has no {field}")
        if not _strings(revised.get("completion_conditions")):
            issues.append(f"step_revisions step {slot} revised_content has no completion_conditions")
        updates[slot] = revised
    return updates, issues


def _apply_verified_step_revisions(steps: list[dict], value) -> None:
    updates, _ = _step_revision_updates(value, len(steps))
    for slot, revised in updates.items():
        step = steps[slot - 1]
        for field in (
            "purpose",
            "target",
            "action",
            "required_behavior_if_removed",
            "minimality_check",
        ):
            step[field] = str(revised.get(field) or "").strip()
        if revised.get("responsibility_key"):
            step["responsibility_key"] = str(revised.get("responsibility_key") or "").strip()
        step["completion_conditions"] = _strings(revised.get("completion_conditions"))
        step["out_of_scope"] = _strings(revised.get("out_of_scope"))


def _merge_verified_step_groups(steps: list[dict], value) -> None:
    groups, _ = _step_merge_groups(value, steps)
    remove = set()
    for group in groups:
        slots = group["slots"]
        destination = steps[slots[0] - 1]
        for slot in slots[1:]:
            source = steps[slot - 1]
            for field in ("decision_slots", "acceptance_slots", "project_fact_slots"):
                destination[field] = sorted(set(destination.get(field) or []) | set(source.get(field) or []))
            remove.add(slot - 1)
        merged = group["content"]
        for field in (
            "purpose",
            "target",
            "action",
            "required_behavior_if_removed",
            "minimality_check",
        ):
            destination[field] = str(merged.get(field) or "").strip()
        if merged.get("responsibility_key"):
            destination["responsibility_key"] = str(merged.get("responsibility_key") or "").strip()
        destination["completion_conditions"] = _strings(merged.get("completion_conditions"))
        destination["out_of_scope"] = _strings(merged.get("out_of_scope"))
    steps[:] = [step for index, step in enumerate(steps) if index not in remove]


def _merge_step_acceptance_coverage(
    steps: list[dict],
    value,
    acceptance_count: int,
) -> None:
    updates, _ = _step_coverage_updates(value, len(steps), acceptance_count)
    for step_slot, acceptance_slots in updates.items():
        step = steps[step_slot - 1]
        existing = _numbered_slots(step.get("acceptance_slots"), acceptance_count)
        step["acceptance_slots"] = sorted(set(existing) | acceptance_slots)


def _step_coverage_updates(
    value,
    step_count: int,
    acceptance_count: int,
) -> tuple[dict[int, set[int]], list[str]]:
    if value is None:
        return {}, []
    if not isinstance(value, list):
        return {}, ["step_acceptance_coverage must be an array"]
    updates: dict[int, set[int]] = {}
    issues = []
    for item in value:
        if not isinstance(item, dict):
            issues.append("step_acceptance_coverage contains a non-object item")
            continue
        try:
            step_slot = int(item.get("step_slot"))
        except (TypeError, ValueError):
            issues.append("step_acceptance_coverage has an invalid step_slot")
            continue
        if not 1 <= step_slot <= step_count:
            issues.append(f"step_acceptance_coverage references unknown step slot: {step_slot}")
            continue
        slots = set(_numbered_slots(item.get("acceptance_slots"), acceptance_count))
        if not slots:
            issues.append(f"step_acceptance_coverage step {step_slot} has no valid acceptance slots")
            continue
        updates.setdefault(step_slot, set()).update(slots)
    return updates, issues


def _numbered_slots(value, limit: int) -> list[int]:
    if not isinstance(value, list):
        return []
    result = []
    for raw in value:
        try:
            slot = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= slot <= limit and slot not in result:
            result.append(slot)
    return result


def _append_slot_steps(target: list[dict], value, *, decision_slot: int) -> None:
    if not isinstance(value, list):
        return
    for raw in value:
        if not isinstance(raw, dict):
            continue
        candidate = dict(raw)
        candidate["decision_slots"] = [decision_slot]
        target.append(candidate)


def _step_responsibility_key(step: dict) -> tuple[str, str, str]:
    return (
        str(step.get("mode") or "modify").strip().casefold(),
        str(step.get("file") or "").strip().replace("\\", "/").casefold(),
        _normalized_target(step.get("responsibility_key") or step.get("target")),
    )


def _normalized_target(value) -> str:
    return " ".join(str(value or "").replace("`", " ").casefold().split())


def _unique_strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for raw in value:
        text = str(raw or "").strip()
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def _decision_acceptance_ids(decision_ids: list[str], design_plan: dict) -> list[str]:
    requirement_sources = {
        str(item.get("id") or ""): str(item.get("source") or "")
        for item in design_plan.get("requirement_model", [])
        if isinstance(item, dict) and item.get("id") and item.get("source")
    }
    return _unique_strings([
        requirement_sources[requirement_id]
        for item in design_plan.get("design_decisions", [])
        if isinstance(item, dict) and item.get("id") in decision_ids
        for requirement_id in item.get("requirement_ids", [])
        if requirement_id in requirement_sources
    ])


def _decision_reference_ids(decision_ids: list[str], design_plan: dict) -> list[str]:
    return _unique_strings([
        reference_id
        for item in design_plan.get("design_decisions", [])
        if isinstance(item, dict) and item.get("id") in decision_ids
        for reference_id in _strings(item.get("reference_ids"))
    ])


def _reference_files(reference_ids: list[str], analysis: dict) -> list[str]:
    source_paths = {
        str(item.get("id") or ""): str(item.get("path") or "").strip()
        for item in analysis.get("source_catalog", [])
        if isinstance(item, dict) and item.get("id") and str(item.get("path") or "").strip()
    }
    wanted = set(reference_ids)
    return _unique_strings([
        source_paths[source_id]
        for baseline in analysis.get("reference_baselines", [])
        if isinstance(baseline, dict) and str(baseline.get("id") or "") in wanted
        for source_id in _strings(baseline.get("source_refs"))
        if source_id in source_paths
    ])


def _canonical_workspace_file(file: str, workspace: Path) -> str:
    workspace = workspace.resolve()
    raw = str(file or "").strip()
    if not raw:
        raise ValueError("file path is invalid")
    path = Path(raw)
    target = path.resolve() if path.is_absolute() else (workspace / path).resolve()
    try:
        relative = target.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("file is outside workspace") from exc
    if relative == Path("."):
        raise ValueError("file path is invalid")
    return relative.as_posix()


def _repair_unique_modify_target(file: str, workspace: Path) -> str:
    if (workspace / file).is_file():
        return file
    basename = Path(file).name.casefold()
    matches = []
    for root, dirs, files in os.walk(workspace, onerror=lambda _error: None):
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
        for name in files:
            if name.casefold() != basename:
                continue
            matches.append((Path(root) / name).relative_to(workspace).as_posix())
            if len(matches) > 1:
                return file
    return matches[0] if len(matches) == 1 else file


def _planned_file_issue(file: str, mode: str, workspace: Path) -> str:
    if Path(file).is_absolute():
        return "file must be workspace-relative"
    try:
        target = (workspace / file).resolve()
    except OSError:
        return "file path is invalid"
    if workspace not in (target, *target.parents):
        return "file is outside workspace"
    if mode == "modify" and not target.is_file():
        return f"modify target does not exist: {file}"
    if mode == "create" and target.exists():
        return f"create target already exists: {file}"
    return ""


def _known_workspace_files(workspace: Path) -> set[str]:
    result = set()
    if not workspace.is_dir():
        return result
    for root, dirs, files in os.walk(workspace, onerror=lambda _error: None):
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
        for name in files:
            result.add((Path(root) / name).relative_to(workspace).as_posix())
    return result


def _derived_reference_files(
    step: dict,
    analysis: dict,
    design_plan: dict,
    workspace: Path,
    workspace_files: set[str],
    step_id: str,
    issues: list[str],
) -> set[str]:
    reference_ids = _decision_reference_ids(_strings(step.get("decision_ids")), design_plan)
    result = set()
    for raw in _reference_files(reference_ids, analysis):
        try:
            file = _canonical_workspace_file(raw, workspace)
        except ValueError as exc:
            issues.append(f"step {step_id} derived reference file {exc}: {raw}")
            continue
        if file not in workspace_files:
            issues.append(f"step {step_id} derived reference file does not exist: {file}")
            continue
        if file == str(step.get("file") or "").replace("\\", "/"):
            issues.append(f"step {step_id} write file cannot also be a reference file: {file}")
            continue
        result.add(file)
    return result


def _decision_boundary_files(step: dict, design_plan: dict, workspace_files: set[str]) -> set[str]:
    decision_ids = set(_strings(step.get("decision_ids")))
    if not decision_ids:
        return set()
    text_parts = []
    for decision in design_plan.get("design_decisions") or []:
        if not isinstance(decision, dict) or str(decision.get("id") or "") not in decision_ids:
            continue
        boundary = decision.get("data_boundary")
        if not isinstance(boundary, dict):
            continue
        text_parts.extend([
            str(boundary.get("owner") or ""),
            str(boundary.get("contract") or ""),
            " ".join(_strings(boundary.get("producers"))),
            " ".join(_strings(boundary.get("consumers"))),
        ])
    return _mentioned_files_in_text(" ".join(text_parts), workspace_files)


def _mentioned_workspace_files(step: dict, workspace_files: set[str]) -> set[str]:
    text = " ".join([
        str(step.get("target") or ""),
        str(step.get("action") or ""),
        str(step.get("required_behavior_if_removed") or ""),
        " ".join(step.get("completion_conditions") or []),
    ])
    return _mentioned_files_in_text(text, workspace_files)


def _mentioned_files_in_text(text: str, workspace_files: set[str]) -> set[str]:
    lowered = str(text or "").replace("\\", "/").casefold()
    basename_counts = {}
    for file in workspace_files:
        basename = Path(file).name.casefold()
        basename_counts[basename] = basename_counts.get(basename, 0) + 1
    result = set()
    for file in workspace_files:
        relative = file.casefold()
        basename = Path(file).name.casefold()
        if relative in lowered or (basename_counts.get(basename) == 1 and basename in lowered):
            result.add(file)
    return result


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
