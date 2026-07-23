from __future__ import annotations

from .. import app_settings

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
TASK_UNKNOWN_STRATEGIES = {"investigate_project", "ask_user", "deferred"}


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
    return [str(item).strip() for item in value if str(item).strip()]


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
            items.append({"id": item_id or f"AC{index}", "text": text})
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
            unknown_type = str(raw.get("type") or "").strip().casefold()
            if not unknown_type:
                unknown_type = "product_decision" if strategy == "ask_user" else "code_fact"
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
    if strategy == "ask_user":
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
