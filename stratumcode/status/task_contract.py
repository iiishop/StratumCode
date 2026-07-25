from __future__ import annotations

import re

from .. import app_settings

TASK_AUTHORITIES = {"user_explicit", "user_reference", "verified_fact", "derived"}
AUTHORITATIVE_AUTHORITIES = {"user_explicit", "user_reference"}
PRODUCT_DECISION_TERMS = {
    "animation", "animate", "transition duration", "persist", "persistence", "localstorage",
    "toast", "error message", "click title", "click button", "delete", "drag", "sort",
    "动画", "持久化", "错误提示", "点击标题", "点击按钮", "删除", "拖拽", "排序",
}
TASK_UNKNOWN_TYPES = {
    "code_fact",
    "doc_fact",
    "runtime_fact",
    "product_decision",
    "engineering_decision",
    "risk",
}
TASK_UNKNOWN_TYPE_ALIASES = {
    "codebase_fact": "code_fact",
    "user_decision": "product_decision",
}
TASK_UNKNOWN_STRATEGIES = {"investigate_project", "clearify", "deferred"}
LEGACY_ASK_USER_STRATEGY = "ask_user"
LEGACY_NEEDS_USER_STATUS = "needs_user"


def request_from_analysis(analysis: dict | None, fallback: str = "") -> str:
    """返回 task analysis 表示的原始用户请求。

    Args:
        analysis: 可能包含 origin_message 或 intent.summary 的 task analysis 字典。
        fallback: analysis 中没有请求文本时使用的兜底请求。

    Returns:
        去除首尾空白后的 origin message、intent summary 或 fallback。
    """
    if isinstance(analysis, dict):
        origin = str(analysis.get("origin_message") or "").strip()
        if origin:
            return origin
        intent = analysis.get("intent")
        if isinstance(intent, dict):
            summary = str(intent.get("summary") or "").strip()
            if summary:
                return summary
    return str(fallback or "").strip()


def run_request(run) -> str:
    """返回状态机 run 对应的用户请求。

    Args:
        run: 带有 analysis 和 message 属性的运行时对象。

    Returns:
        从 run.analysis 提取的请求；没有时回退到 run.message。
    """
    return request_from_analysis(getattr(run, "analysis", None), getattr(run, "message", ""))


def _string_list(value, field: str) -> list[str]:
    """把可选列表字段规范化为非空字符串列表。

    Args:
        value: task contract 字段中的可选原始列表值。
        field: 校验错误中使用的字段名。

    Returns:
        去除首尾空白后的非空字符串列表。

    Raises:
        ValueError: value 存在但不是列表时抛出。
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result = []
    for item in value:
        text = str(item.get("text") or "").strip() if isinstance(item, dict) else str(item).strip()
        if text:
            result.append(text)
    return result


def _acceptance_criteria(value) -> list[dict]:
    """把原始验收标准规范化为 id/text 对象。

    Args:
        value: 可选列表，元素可以是字符串，也可以是带 text/description 和 id 的字典。

    Returns:
        每项都包含 id 和 text 的验收标准字典列表。

    Raises:
        ValueError: value 存在但不是列表时抛出。
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("acceptance_criteria must be an array")
    items = []
    for index, raw in enumerate(value, start=1):
        if isinstance(raw, dict):
            text = str(raw.get("text") or raw.get("description") or "").strip()
            item_id = str(raw.get("id") or f"AC{index}").strip()
        else:
            text = str(raw).strip()
            item_id = f"AC{index}"
        if text:
            item = {"id": item_id or f"AC{index}", "text": text}
            if isinstance(raw, dict):
                for field in ("authority", "source_refs", "derived_from"):
                    if field in raw:
                        item[field] = raw[field]
            items.append(item)
    return items


def _behavior_contract(value) -> dict:
    """规范化 task contract 的行为契约部分。

    Args:
        value: 可选字典，包含输入、输出、成功/失败行为和边界。

    Returns:
        每个行为字段都被规范化为字符串列表的字典。

    Raises:
        ValueError: value 存在但不是字典，或子字段形状非法时抛出。
    """
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("behavior_contract must be an object")
    return {
        "inputs": _string_list(value.get("inputs"), "behavior_contract.inputs"),
        "outputs": _string_list(value.get("outputs"), "behavior_contract.outputs"),
        "success_behaviors": _string_list(value.get("success_behaviors"), "behavior_contract.success_behaviors"),
        "failure_behaviors": _string_list(value.get("failure_behaviors"), "behavior_contract.failure_behaviors"),
        "boundaries": _string_list(value.get("boundaries"), "behavior_contract.boundaries"),
    }


def _scope(value) -> dict:
    """规范化任务范围边界。

    Args:
        value: 可选字典，包含 in、out 和 undecided 列表。

    Returns:
        in/out/undecided 均规范化为字符串列表的字典。

    Raises:
        ValueError: value 存在但不是字典，或子字段形状非法时抛出。
    """
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("scope must be an object")
    return {
        "in": _string_list(value.get("in"), "scope.in"),
        "out": _string_list(value.get("out"), "scope.out"),
        "undecided": _string_list(value.get("undecided"), "scope.undecided"),
    }


def _limited_unknowns(value, criteria=None) -> list[dict]:
    """规范化 unknowns，并执行 task contract 的数量上限。

    Args:
        value: 可选的原始 unknown 列表。
        criteria: 可选验收标准，用于校验 unknown 关联的验收标准 id。

    Returns:
        不超过当前 task_unknowns 设置上限的 unknown 字典；上限为 0 时不限制。

    Raises:
        ValueError: unknowns 形状非法或超过数量上限时抛出。
    """
    unknowns = _unknowns(value, criteria)
    limit = app_settings.get_task_limit("task_unknowns")
    if limit and len(unknowns) > limit:
        raise ValueError(f"unknowns must contain at most {limit} items")
    return unknowns


def _unknowns(value, criteria=None) -> list[dict]:
    """把原始 unknowns 规范化为 task contract 使用的形状。

    Args:
        value: 可选列表，元素可以是描述 unknown 的字符串或字典。
        criteria: 可选验收标准，用于保证 acceptance_criteria_ids 有效。

    Returns:
        unknown 字典列表，每项包含 id、question、blocking、type、why、
        resolution_strategy 和 acceptance_criteria_ids。

    Raises:
        ValueError: value 存在但不是列表时抛出。
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("unknowns must be an array")
    criteria_ids = [item["id"] for item in _acceptance_criteria(criteria)]
    items = []
    for index, raw in enumerate(value, start=1):
        if isinstance(raw, dict):
            question = str(raw.get("question") or raw.get("text") or raw.get("description") or "").strip()
            item_id = str(raw.get("id") or f"U{index}").strip()
            strategy = str(raw.get("resolution_strategy") or "investigate_project").strip().casefold()
            if strategy == LEGACY_ASK_USER_STRATEGY:
                strategy = "clearify"
            unknown_type = str(raw.get("type") or "").strip().casefold()
            if not unknown_type:
                unknown_type = "product_decision" if strategy == "clearify" else "code_fact"
            accepted_ids = raw.get("acceptance_criteria_ids")
            if not isinstance(accepted_ids, list):
                accepted_ids = []
            accepted_ids = [str(item).strip() for item in accepted_ids if str(item).strip()]
            blocking = bool(raw.get("blocking", True))
            why = str(raw.get("why") or raw.get("reason") or "").strip()
        else:
            question = str(raw).strip()
            item_id = f"U{index}"
            unknown_type = "code_fact"
            strategy = "investigate_project"
            accepted_ids = criteria_ids
            blocking = True
            why = ""
        if not question:
            continue
        if unknown_type == "deferred":
            unknown_type, strategy, blocking = "risk", "deferred", False
        unknown_type = TASK_UNKNOWN_TYPE_ALIASES.get(unknown_type, unknown_type)
        if unknown_type not in TASK_UNKNOWN_TYPES:
            unknown_type = "code_fact"
        if strategy not in TASK_UNKNOWN_STRATEGIES:
            strategy = "investigate_project"
        unknown_type, strategy, blocking = _normalize_unknown_policy(unknown_type, strategy, blocking)
        if criteria_ids:
            accepted_ids = [item for item in accepted_ids if item in criteria_ids] or criteria_ids
        items.append({
            "id": item_id or f"U{index}",
            "question": question,
            "blocking": blocking,
            "type": unknown_type,
            "why": why,
            "resolution_strategy": strategy,
            "acceptance_criteria_ids": accepted_ids,
            **({
                field: raw[field]
                for field in ("origin", "reference_id")
                if isinstance(raw, dict) and field in raw
            }),
        })
    return items


def _normalize_unknown_policy(unknown_type: str, strategy: str, blocking: bool) -> tuple[str, str, bool]:
    """应用 unknown type、处理策略和阻塞状态之间的联动规则。

    Args:
        unknown_type: 已规范化的 unknown 类别。
        strategy: 请求使用的处理策略。
        blocking: 该 unknown 是否阻塞实现。

    Returns:
        规范化后的 type、strategy 和 blocking 三元组。
    """
    if strategy == "deferred" or not blocking:
        return unknown_type, "deferred", False
    if strategy == "clearify":
        if unknown_type != "product_decision":
            return unknown_type, "investigate_project", True
        return unknown_type, strategy, True
    return unknown_type, strategy, bool(blocking)


def _ensure_task_contract(analysis: dict) -> dict:
    """确保 task analysis 中包含规范化后的 task contract。

    Args:
        analysis: 需要原地规范化的可变 task analysis 字典。

    Returns:
        同一个 analysis 字典，其中 contract 字段已被规范化。

    Raises:
        ValueError: 任一 contract 字段形状非法时抛出。
    """
    if analysis.get("source_catalog") and not analysis.get("_canonicalized"):
        _canonicalize_task_contract(analysis)
    analysis.setdefault("constraints", [])
    analysis.setdefault("hypotheses", [])
    analysis.setdefault("clues", [])
    analysis.setdefault("acceptance_criteria", [])
    analysis.setdefault("behavior_contract", {})
    analysis.setdefault("scope", {"in": [], "out": [], "undecided": []})
    analysis["acceptance_criteria"] = _acceptance_criteria(analysis.get("acceptance_criteria"))
    analysis["behavior_contract"] = _behavior_contract(analysis.get("behavior_contract"))
    analysis["scope"] = _scope(analysis.get("scope"))
    analysis["unknowns"] = _limited_unknowns(analysis.get("unknowns"), analysis.get("acceptance_criteria"))
    return analysis


def _canonicalize_task_contract(analysis: dict) -> None:
    """Validate provenance once, then serialize the canonical contract."""
    sources = {
        str(item.get("id") or ""): item
        for item in analysis.get("source_catalog", [])
        if isinstance(item, dict) and item.get("id")
    }
    warnings = analysis.setdefault("analyzer_warnings", [])
    origin = str(analysis.get("origin_message") or sources.get("SRC1", {}).get("text") or "").strip()
    requirements = [{
        "id": "REQ1",
        "text": origin,
        "authority": "user_explicit",
        "source_refs": ["SRC1"],
        "derived_from": [],
    }] if origin else []
    analysis["requirements"] = requirements
    intent = analysis.get("intent") if isinstance(analysis.get("intent"), dict) else {}
    intent_statements = _canonical_statements(
        [{"text": intent.get("summary", ""), "derived_from": ["REQ1"]}],
        "intent.summary",
        sources,
        warnings,
        valid_derived_ids={"REQ1"},
    )
    if not intent_statements and origin:
        intent_statements = _canonical_statements(
            [{"text": origin[:160], "derived_from": ["REQ1"]}],
            "intent.summary",
            sources,
            warnings,
            valid_derived_ids={"REQ1"},
        )
    analysis["intent_statement"] = intent_statements[0] if intent_statements else {}
    if intent_statements:
        analysis["intent"]["summary"] = intent_statements[0]["text"]

    references = []
    for raw in analysis.get("reference_baselines", []):
        if not isinstance(raw, dict):
            warnings.append("reference_baselines: removed invalid item")
            continue
        source_refs = _source_refs(raw)
        excerpt = str(raw.get("source_excerpt") or "").strip()
        target = _canonical_reference_target(
            str(raw.get("target") or "").strip(),
            source_refs,
            sources,
        )
        if (
            not target
            or not _refs_support(source_refs, target, sources, user_only=True)
            or not _refs_support(source_refs, excerpt or target, sources, user_only=True)
        ):
            warnings.append(f"reference_baselines: removed unsupported item {target or '(empty)'}")
            continue
        references.append({
            "id": f"REF{len(references) + 1}",
            "target": target,
            "policy": "inherit_unspecified_behavior",
            "authority": "user_reference",
            "source_refs": source_refs,
            "source_excerpt": excerpt,
        })
    analysis["reference_baselines"] = references
    for hypothesis in analysis.get("hypotheses", []):
        text = str(hypothesis.get("text") or "") if isinstance(hypothesis, dict) else ""
        if text and not _matching_source_refs(text, sources, user_only=True):
            hypothesis["certainty"] = "guess"

    constraints = _canonical_statements(
        analysis.get("constraints"), "constraint", sources, warnings, authoritative=True
    )
    analysis["constraint_statements"] = constraints
    analysis["constraints"] = [item["text"] for item in constraints]

    criteria = _canonical_statements(
        analysis.get("acceptance_criteria"), "acceptance_criteria", sources, warnings,
        default_derived_from=["REQ1"],
        valid_derived_ids={"REQ1", *(item["id"] for item in references)},
    )
    analysis["acceptance_criteria"] = [
        {"id": f"AC{index}", **item}
        for index, item in enumerate(criteria, start=1)
    ]

    behavior = analysis.get("behavior_contract")
    behavior = behavior if isinstance(behavior, dict) else {}
    behavior_statements = {}
    for field in ("inputs", "outputs", "success_behaviors"):
        behavior_statements[field] = _canonical_statements(
            behavior.get(field), f"behavior_contract.{field}", sources, warnings,
            default_derived_from=["REQ1"],
            valid_derived_ids={"REQ1", *(item["id"] for item in references)},
        )
    for field in ("failure_behaviors", "boundaries"):
        behavior_statements[field] = _canonical_statements(
            behavior.get(field), f"behavior_contract.{field}", sources, warnings, authoritative=True
        )
    analysis["behavior_statements"] = behavior_statements
    analysis["behavior_contract"] = {
        field: [item["text"] for item in items]
        for field, items in behavior_statements.items()
    }

    raw_scope = analysis.get("scope")
    raw_scope = raw_scope if isinstance(raw_scope, dict) else {}
    scope_statements = {
        "in": _canonical_statements(
            raw_scope.get("in"), "scope.in", sources, warnings,
            default_derived_from=["REQ1"], valid_derived_ids={"REQ1"},
        ),
        "out": _canonical_statements(
            raw_scope.get("out"), "scope.out", sources, warnings, authoritative=True
        ),
        "undecided": _canonical_statements(
            raw_scope.get("undecided"), "scope.undecided", sources, warnings,
            default_derived_from=["REQ1"], valid_derived_ids={"REQ1"},
        ),
    }
    out_text = {_normalized(item["text"]) for item in scope_statements["out"]}
    scope_statements["undecided"] = [
        item for item in scope_statements["undecided"]
        if _normalized(item["text"]) not in out_text
    ]
    analysis["scope_statements"] = scope_statements
    analysis["scope"] = {
        field: [item["text"] for item in items]
        for field, items in scope_statements.items()
    }

    clues, rejected = _canonical_clues(analysis.get("clues"), sources, warnings)
    analysis["clues"] = clues
    targets = [
        str(item.get("text") or "").strip() if isinstance(item, dict) else str(item).strip()
        for item in analysis.get("investigation_targets", [])
    ]
    targets = [item for item in targets if item]
    if rejected and not targets:
        targets = ["Locate the existing code path responsible for the requested behavior."]
    analysis["investigation_targets"] = targets
    analysis["unknowns"] = _canonical_unknowns(
        analysis.get("unknowns"), targets, references, rejected
    )
    unknown_limit = app_settings.get_task_limit("task_unknowns")
    if unknown_limit and len(analysis["unknowns"]) > unknown_limit:
        warnings.append(
            f"unknowns: kept {unknown_limit} canonical items and removed "
            f"{len(analysis['unknowns']) - unknown_limit} lower-priority items"
        )
        analysis["unknowns"] = analysis["unknowns"][:unknown_limit]
    analysis["statements"] = (
        requirements
        + intent_statements
        + constraints
        + analysis["acceptance_criteria"]
        + [item for values in behavior_statements.values() for item in values]
        + [item for values in scope_statements.values() for item in values]
    )
    analysis["_canonicalized"] = True


def _canonical_statements(
    value,
    field: str,
    sources: dict[str, dict],
    warnings: list[str],
    *,
    authoritative: bool = False,
    default_derived_from: list[str] | None = None,
    valid_derived_ids: set[str] | None = None,
) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append(f"{field}: removed invalid non-array value")
        return []
    items = []
    for raw in value:
        data = raw if isinstance(raw, dict) else {"text": raw}
        text = str(data.get("text") or data.get("description") or "").strip()
        if not text:
            continue
        authority = str(data.get("authority") or ("derived" if not authoritative else "")).strip()
        source_refs = _source_refs(data)
        excerpt = str(data.get("source_excerpt") or text).strip()
        if authoritative:
            if not source_refs:
                source_refs = _matching_source_refs(excerpt, sources, user_only=False)
            if authority not in AUTHORITATIVE_AUTHORITIES:
                authority = str(sources.get(source_refs[0], {}).get("authority") or "") if source_refs else ""
            if authority not in AUTHORITATIVE_AUTHORITIES or not _refs_support(source_refs, excerpt, sources):
                warnings.append(f"{field}: removed unsupported statement {text}")
                continue
            text = excerpt
            derived_from = []
        else:
            authority = "derived"
            derived_from = [
                str(item) for item in data.get("derived_from", default_derived_from or [])
                if str(item)
            ]
            if valid_derived_ids is not None:
                derived_from = [item for item in derived_from if item in valid_derived_ids]
            if not derived_from and default_derived_from:
                derived_from = [
                    item for item in default_derived_from
                    if valid_derived_ids is None or item in valid_derived_ids
                ]
            if not derived_from:
                warnings.append(f"{field}: removed statement without valid derived_from {text}")
                continue
            if _introduces_product_decision(text, sources, derived_from):
                warnings.append(f"{field}: removed ungrounded product decision {text}")
                continue
            source_refs = []
        items.append({
            "text": text,
            "authority": authority,
            "source_refs": source_refs,
            "derived_from": derived_from,
        })
    return items


def _canonical_clues(value, sources: dict[str, dict], warnings: list[str]) -> tuple[list[dict], list[str]]:
    if not isinstance(value, list):
        return [], []
    clues = []
    rejected = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        clue = str(raw.get("value") or raw.get("path") or raw.get("symbol") or "").strip()
        refs = _source_refs(raw) or _matching_source_refs(clue, sources, user_only=False)
        if not clue or not _refs_support(refs, clue, sources):
            if clue:
                rejected.append(clue)
                warnings.append(f"clues: removed unsupported clue {clue}")
            continue
        item = dict(raw)
        item["source_refs"] = refs
        item["authority"] = str(sources[refs[0]].get("authority") or "verified_fact")
        clues.append(item)
    return clues, rejected


def _canonical_unknowns(
    value, targets: list[str], references: list[dict], rejected: list[str]
) -> list[dict]:
    items = [{
        "question": target,
        "type": "code_fact",
        "blocking": True,
        "resolution_strategy": "investigate_project",
        "origin": "investigation_target",
    } for target in targets if not _reference_related_unknown({"question": target}, references)]
    items.extend({
        "question": f"What are the confirmed existing behaviors of {reference['target']}?",
        "type": "code_fact",
        "blocking": True,
        "resolution_strategy": "investigate_project",
        "reference_id": reference["id"],
    } for reference in references)
    if isinstance(value, list):
        for raw in value:
            text = str(
                raw.get("question") or raw.get("text") or ""
                if isinstance(raw, dict) else raw
            ).strip()
            if text and not any(_normalized(term) in _normalized(text) for term in rejected):
                item = dict(raw) if isinstance(raw, dict) else {"question": text}
                if _reference_related_unknown(item, references):
                    continue
                items.append(item)
    result = []
    seen = set()
    for item in items:
        text = str(item.get("question") or item.get("text") or "" if isinstance(item, dict) else item).strip()
        key = _normalized(text)
        if key and key not in seen:
            seen.add(key)
            normalized = dict(item) if isinstance(item, dict) else {"question": text}
            normalized["id"] = f"U{len(result) + 1}"
            result.append(normalized)
    return result


def _source_refs(item: dict) -> list[str]:
    refs = item.get("source_refs")
    if not isinstance(refs, list):
        refs = [item.get("source_ref")] if item.get("source_ref") else []
    return [str(value).strip() for value in refs if str(value).strip()]


def _matching_source_refs(text: str, sources: dict[str, dict], *, user_only: bool) -> list[str]:
    return [
        source_id
        for source_id, source in sources.items()
        if _source_contains(source, text)
        and (not user_only or str(source.get("authority")) == "user_explicit")
    ][:1]


def _refs_support(
    refs: list[str], excerpt: str, sources: dict[str, dict], *, user_only: bool = False
) -> bool:
    return bool(refs) and all(
        ref in sources
        and (not user_only or sources[ref].get("authority") == "user_explicit")
        and _source_contains(sources[ref], excerpt)
        for ref in refs
    )


def _canonical_reference_target(
    target: str, refs: list[str], sources: dict[str, dict]
) -> str:
    if target and _refs_support(refs, target, sources, user_only=True):
        return target
    for candidate in re.findall(r"[A-Za-z][A-Za-z0-9_-]*|[\u4e00-\u9fff]{2,}", target):
        if _refs_support(refs, candidate, sources, user_only=True):
            return candidate
    return ""


def _reference_related_unknown(item: dict, references: list[dict]) -> bool:
    if not references:
        return False
    text = _normalized(f"{item.get('question', '')} {item.get('why', '')}")
    targets = [_normalized(item["target"]) for item in references]
    reference_ids = {str(item.get("id") or "") for item in references}
    return (
        str(item.get("reference_id") or "") in reference_ids
        or any(target and target in text for target in targets)
    )


def _source_contains(source: dict, text: str) -> bool:
    needle = _normalized(text)
    haystack = _normalized(f"{source.get('text', '')} {source.get('path', '')}")
    if not needle:
        return False
    return needle in haystack or needle.replace(" ", "") in haystack.replace(" ", "")


def _introduces_product_decision(
    text: str, sources: dict[str, dict], derived_from: list[str]
) -> bool:
    normalized = _normalized(text)
    grounding = [sources["SRC1"]] if "REQ1" in derived_from and "SRC1" in sources else []
    source_text = _normalized(" ".join(str(item.get("text") or "") for item in grounding))
    return any(_normalized(term) in normalized and _normalized(term) not in source_text for term in PRODUCT_DECISION_TERMS)


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())
