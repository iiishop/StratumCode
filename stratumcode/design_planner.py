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
DEFAULT_DESIGN_JSON_ATTEMPTS = 3


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

    system = {"role": "system", "content": prompt.build_design_planner_system(app_settings.get_output_language())}
    criteria = [item for item in analysis.get("acceptance_criteria", []) if isinstance(item, dict)]
    slots = []
    runtime_warnings = []
    for index, criterion in enumerate(criteria, start=1):
        slot = yield from _content_json_stream(
            provider,
            model,
            [
                system,
                {"role": "user", "content": prompt.build_design_requirement_slot_user(
                    message,
                    analysis,
                    investigation,
                    workspace_dir,
                    slot_index=index,
                    criterion=criterion,
                )},
            ],
            pricing_rules,
            usage_total,
            run_id,
            f"slot-{index}",
        )
        if slot is None:
            runtime_warnings.append(f"Requirement slot {index} fell back to ambiguous after invalid model content.")
            slot = _default_requirement_slot(criterion)
        slots.append(slot)
    decision_data = yield from _content_json_stream(
        provider,
        model,
        [
            system,
            {"role": "user", "content": prompt.build_design_decision_slots_user(
                message,
                analysis,
                investigation,
                workspace_dir,
                _runtime_requirement_slots(slots, criteria),
            )},
        ],
        pricing_rules,
        usage_total,
        run_id,
        "decision",
    )
    if decision_data is None:
        yield start_event(f"{run_id}-output", "output", {
            "content": "Design planning failed to produce valid decision content after repeated invalid responses.",
            "streaming": False,
        })
        yield {"op": "update", "id": stage_id, "patch": {"state": "error", "phase": "design_planning_failed"}}
        return
    plan = _design_from_slots(slots, decision_data, criteria, runtime_warnings)
    plan = normalize_design_plan(plan, analysis, investigation)
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


def normalize_design_plan(plan: dict, analysis: dict, investigation: dict) -> dict:
    """Apply deterministic design-plan fixes that should not be delegated to the model."""
    plan = {
        **plan,
        "decision_gaps": [dict(item) for item in plan.get("decision_gaps", []) if isinstance(item, dict)],
        "design_decisions": [dict(item) for item in plan.get("design_decisions", []) if isinstance(item, dict)],
        "runtime_warnings": list(plan.get("runtime_warnings") or []),
    }
    if not _analysis_authorizes_behavior_preserving_refactor(analysis):
        return plan
    allowed_refactor_symbols = set(_structured_refactor_symbols(investigation))
    filtered_decisions = []
    for decision in plan["design_decisions"]:
        unauthorized_removal = _unauthorized_removal_reason(decision, investigation)
        if unauthorized_removal:
            plan["runtime_warnings"].append(
                f"Runtime removed design decision {decision.get('id') or '?'} because {unauthorized_removal}: "
                + str(decision.get("decision") or "")[:160]
            )
            continue
        unauthorized_public = _unauthorized_public_identifier_reason(decision, allowed_refactor_symbols, investigation)
        if unauthorized_public:
            plan["runtime_warnings"].append(
                f"Runtime removed design decision {decision.get('id') or '?'} because {unauthorized_public}: "
                + str(decision.get("decision") or "")[:160]
            )
            continue
        unauthorized = _unauthorized_extracted_symbols(decision, allowed_refactor_symbols)
        if unauthorized:
            plan["runtime_warnings"].append(
                "Runtime removed design decision "
                f"{decision.get('id') or '?'} because it extracts symbols outside structured investigation candidates: "
                + ", ".join(unauthorized)
            )
            continue
        filtered_decisions.append(decision)
    plan["design_decisions"] = filtered_decisions
    existing_decisions = " ".join(
        str(item.get("decision") or "")
        for item in plan["design_decisions"]
    ).casefold()
    next_decision_index = len(plan["design_decisions"]) + 1
    for gap in plan["decision_gaps"]:
        if not gap.get("blocks_implementation"):
            continue
        if not _gap_is_authorized_refactor_choice(gap):
            continue
        recommended = str(gap.get("recommended_answer") or "").strip()
        if not recommended:
            continue
        if recommended.casefold() not in existing_decisions:
            plan["design_decisions"].append({
                "id": f"DD{next_decision_index}",
                "decision": recommended,
                "because": _strings([gap.get("why") or "", gap.get("question") or ""]),
                "variant_strategy": _variant_strategy_from_gap(gap, investigation),
            })
            existing_decisions += " " + recommended.casefold()
            next_decision_index += 1
        gap["blocks_implementation"] = False
        plan["runtime_warnings"].append(
            f"Runtime converted design gap {gap.get('id') or '?'} into a non-blocking decision because the task already authorizes behavior-preserving refactoring."
        )
    _fill_review_variant_strategies(plan, investigation)
    return plan


def _attempt_indexes(limit: int, start: int = 1):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


def validate_design_plan(plan: dict, analysis: dict, investigation: dict) -> list[str]:
    issues = []
    criteria = [item for item in analysis.get("acceptance_criteria", []) if isinstance(item, dict)]
    requirements = plan.get("requirement_model") or []
    alignments = plan.get("project_alignment") or []
    decisions = plan.get("design_decisions") or []
    blocking_gaps = [item for item in plan.get("decision_gaps", []) if item.get("blocks_implementation")]
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
    for symbol in _structured_skip_symbols(investigation):
        for item in decisions:
            text = " ".join([str(item.get("decision") or ""), " ".join(item.get("because") or [])])
            if _extracts_symbol(text, symbol):
                issues.append(f"design decision {item.get('id') or '?'} tries to extract runtime skip candidate: {symbol}")
    for symbol in _structured_review_symbols(investigation):
        for item in decisions:
            text = " ".join([str(item.get("decision") or ""), " ".join(item.get("because") or [])])
            if _extracts_symbol(text, symbol) and not str(item.get("variant_strategy") or "").strip():
                issues.append(f"design decision {item.get('id') or '?'} extracts review candidate without variant_strategy: {symbol}")
    if len(blocking_gaps) > 1:
        issues.append("design plan can ask at most one blocking decision question")
    for item in blocking_gaps:
        if not item.get("question"):
            issues.append(f"blocking decision gap {item.get('id') or '?'} has no question")
        if not item.get("recommended_answer"):
            issues.append(f"blocking decision gap {item.get('id') or item.get('question') or '?'} has no recommended_answer")
    if not (investigation.get("patch_planning_facts") or investigation.get("patch_planning_context")):
        issues.append("design plan has no grounded investigation facts to rely on")
    return issues


def _analysis_authorizes_behavior_preserving_refactor(analysis: dict) -> bool:
    intent_type = str((analysis.get("intent") or {}).get("type") or "").strip()
    if intent_type != "refactor":
        return False
    text = _analysis_text(analysis)
    has_reuse_scope = _contains_any(text, (
        "refactor",
        "reuse",
        "reusable",
        "duplicate",
        "extract",
        "common function",
        "shared",
        "utility",
        "dead code",
        "cleanup",
        "清理",
        "死代码",
        "重复",
        "复用",
        "可复用",
        "提取",
        "公共函数",
        "共享",
        "工具函数",
        "重构",
    ))
    preserves_behavior = _contains_any(text, (
        "preserve",
        "unchanged",
        "same behavior",
        "no behavior change",
        "不改变",
        "不变",
        "保持",
        "行为一致",
        "功能不变",
    ))
    return has_reuse_scope and preserves_behavior


def _gap_is_authorized_refactor_choice(gap: dict) -> bool:
    text = " ".join(
        str(gap.get(key) or "")
        for key in ("question", "recommended_answer", "why")
    )
    asks_for_shared_extraction = _contains_any(text, (
        "extract",
        "reuse",
        "reusable",
        "common",
        "shared",
        "utility",
        "helper",
        "公共",
        "共享",
        "复用",
        "可复用",
        "提取",
        "工具",
    ))
    preserves_behavior = _contains_any(text, (
        "preserve",
        "unchanged",
        "same behavior",
        "without changing",
        "行为不变",
        "功能不变",
        "不改变",
        "保持",
    ))
    return asks_for_shared_extraction and preserves_behavior


def _variant_strategy_from_gap(gap: dict, investigation: dict) -> str:
    text = " ".join(
        str(gap.get(key) or "").strip()
        for key in ("recommended_answer", "why", "question")
        if str(gap.get(key) or "").strip()
    )
    review_symbols = _structured_review_symbols(investigation)
    if review_symbols and any(symbol and symbol in text for symbol in review_symbols):
        return text
    return ""


def _fill_review_variant_strategies(plan: dict, investigation: dict) -> None:
    review_symbols = _structured_review_symbols(investigation)
    if not review_symbols:
        return
    for decision in plan.get("design_decisions", []):
        if str(decision.get("variant_strategy") or "").strip():
            continue
        text = " ".join([str(decision.get("decision") or ""), " ".join(decision.get("because") or [])])
        if any(_extracts_symbol(text, symbol) for symbol in review_symbols):
            decision["variant_strategy"] = text
            plan.setdefault("runtime_warnings", []).append(
                f"Runtime filled variant_strategy for review candidate in design decision {decision.get('id') or '?'}."
            )


def _analysis_text(analysis: dict) -> str:
    chunks = [
        str((analysis.get("intent") or {}).get("summary") or ""),
        " ".join(str(item.get("text") or "") for item in analysis.get("acceptance_criteria", []) if isinstance(item, dict)),
        " ".join(str(item) for item in analysis.get("constraints", []) if item),
    ]
    behavior = analysis.get("behavior_contract") if isinstance(analysis.get("behavior_contract"), dict) else {}
    for value in behavior.values():
        if isinstance(value, list):
            chunks.extend(str(item) for item in value if item)
        elif value:
            chunks.append(str(value))
    scope = analysis.get("scope") if isinstance(analysis.get("scope"), dict) else {}
    for value in scope.values():
        if isinstance(value, list):
            chunks.extend(str(item) for item in value if item)
        elif value:
            chunks.append(str(value))
    return " ".join(chunks)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = str(text or "").casefold()
    return any(needle.casefold() in lowered for needle in needles)


def _structured_skip_symbols(investigation: dict) -> list[str]:
    return _structured_symbols_by_action(investigation, "skip")


def _structured_review_symbols(investigation: dict) -> list[str]:
    return _structured_symbols_by_action(investigation, "review")


def _structured_refactor_symbols(investigation: dict) -> list[str]:
    return sorted(set(
        _structured_symbols_by_action(investigation, "extract")
        + _structured_symbols_by_action(investigation, "review")
    ))


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


def _extracts_symbol(text: str, symbol: str) -> bool:
    lowered = str(text or "").casefold()
    return symbol.casefold() in lowered and any(
        word in lowered
        for word in ("extract", "shared", "common", "utility", "helper", "提取", "公共", "复用", "共享")
    )


def _unauthorized_extracted_symbols(decision: dict, allowed_symbols: set[str]) -> list[str]:
    text = " ".join([str(decision.get("decision") or ""), " ".join(decision.get("because") or [])])
    if not _contains_any(text, (
        "extract",
        "shared",
        "common",
        "utility",
        "helper",
        "提取",
        "公共",
        "共享",
        "复用",
        "工具",
    )):
        return []
    symbols = sorted(set(re.findall(r"\b_[A-Za-z][A-Za-z0-9_]*\b", text)))
    return [symbol for symbol in symbols if symbol not in allowed_symbols]


def _unauthorized_removal_reason(decision: dict, investigation: dict) -> str:
    text = " ".join([str(decision.get("decision") or ""), " ".join(decision.get("because") or [])])
    if not _contains_any(text, ("remove", "delete", "drop", "移除", "删除", "删掉")):
        return ""
    allowed_tokens = _structured_removal_tokens(investigation)
    if any(token and token in text for token in allowed_tokens):
        return ""
    return "it removes code or files that were not authorized by structured investigation findings"


def _structured_removal_tokens(investigation: dict) -> set[str]:
    structured = investigation.get("structured_findings")
    if not isinstance(structured, dict):
        return set()
    tokens: set[str] = set(_structured_refactor_symbols(investigation))
    candidates = []
    for key in ("dead_code_candidates", "refactor_candidates", "duplicate_candidates"):
        value = structured.get(key)
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
    for item in candidates:
        if symbol := str(item.get("symbol") or "").strip():
            tokens.add(symbol)
        for file_item in item.get("files") or []:
            if isinstance(file_item, dict) and file_item.get("path"):
                tokens.add(str(file_item["path"]).strip())
    return tokens


def _unauthorized_public_identifier_reason(decision: dict, allowed_refactor_symbols: set[str], investigation: dict) -> str:
    text = " ".join([str(decision.get("decision") or ""), " ".join(decision.get("because") or [])])
    word_exclusions = {"Add", "Apply", "Delete", "Drop", "Extract", "Move", "Remove", "Replace", "Static", "Use"}
    public_identifiers = sorted(
        identifier
        for identifier in set(re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", text))
        if identifier not in word_exclusions
        and not re.fullmatch(r"[A-Z]+[0-9]*", identifier)
    )
    if not public_identifiers:
        return ""
    allowed_tokens = _structured_removal_tokens(investigation)
    if any(identifier in allowed_tokens for identifier in public_identifiers):
        return ""
    if any(symbol and symbol in text for symbol in allowed_refactor_symbols):
        return ""
    return "it targets public identifiers not authorized by structured investigation findings"


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
    recommended = gap.get("recommended_answer") or "Use best engineering judgment."
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
                "description": recommended,
                "value": recommended,
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
        raise ValueError("design content must be a JSON object")
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
    attempts = app_settings.get_round_limit("design_json_attempts") or DEFAULT_DESIGN_JSON_ATTEMPTS
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
                    "Do not write ids, schema wrappers, or Markdown."
                )},
            ])
            if repeated_invalid >= DEFAULT_DESIGN_JSON_ATTEMPTS:
                return None
    return None


def _default_requirement_slot(criterion: dict) -> dict:
    text = str(criterion.get("text") or "").strip()
    return {
        "concept": text,
        "behavior": text,
        "alignment_status": "ambiguous",
        "project_fact": "",
        "evidence": [],
    }


def _runtime_requirement_slots(slots: list[dict], criteria: list[dict]) -> list[dict]:
    return [
        {
            "index": index,
            "source_acceptance_text": str(criteria[index - 1].get("text") or "") if index - 1 < len(criteria) else "",
            "concept": str(slot.get("concept") or ""),
            "behavior": str(slot.get("behavior") or ""),
            "alignment_status": str(slot.get("alignment_status") or ""),
        }
        for index, slot in enumerate(slots, start=1)
    ]


def _design_from_slots(slots: list[dict], data: dict, criteria: list[dict], runtime_warnings: list[str] | None = None) -> dict:
    requirements = []
    alignments = []
    for index, criterion in enumerate(criteria, start=1):
        req_id = f"RM{index}"
        content = slots[index - 1] if index - 1 < len(slots) and isinstance(slots[index - 1], dict) else {}
        requirements.append({
            "id": req_id,
            "concept": str(content.get("concept") or criterion.get("text") or "").strip(),
            "behavior": str(content.get("behavior") or criterion.get("text") or "").strip(),
            "source": str(criterion.get("id") or f"AC{index}"),
        })
        alignments.append({
            "requirement_id": req_id,
            "status": _one_of(content.get("alignment_status") or content.get("status"), {"matched", "missing", "ambiguous"}, "ambiguous"),
            "project_fact": str(content.get("project_fact") or "").strip(),
            "evidence": _strings(content.get("evidence")),
        })
    decisions = [
        {
            "id": f"DD{index}",
            "decision": str(item.get("decision") or "").strip(),
            "because": _strings(item.get("because")),
            "variant_strategy": str(item.get("variant_strategy") or "").strip(),
        }
        for index, item in enumerate(data.get("decision_content") or [], start=1)
        if isinstance(item, dict) and str(item.get("decision") or "").strip()
    ]
    gaps = [
        {
            "id": f"DG{index}",
            "question": str(item.get("question") or "").strip(),
            "recommended_answer": str(item.get("recommended_answer") or "").strip(),
            "blocks_implementation": bool(item.get("blocks_implementation")),
            "why": str(item.get("why") or "").strip(),
        }
        for index, item in enumerate((data.get("gap_content") or [])[:1], start=1)
        if isinstance(item, dict) and (item.get("question") or item.get("why"))
    ]
    return {
        "summary": str(data.get("summary") or "").strip(),
        "requirement_model": requirements,
        "project_alignment": alignments,
        "decision_gaps": gaps,
        "design_decisions": decisions,
        "out_of_scope": _strings(data.get("out_of_scope")),
        "runtime_warnings": runtime_warnings or [],
    }


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for raw in value if (item := str(raw).strip())]


def _one_of(value, allowed: set[str], fallback: str) -> str:
    value = str(value or "").strip()
    return value if value in allowed else fallback
