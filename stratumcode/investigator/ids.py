from __future__ import annotations

import re

from ..status.task_contract import _unknowns as _contract_unknowns


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


def _initial_unknowns(analysis: dict | None) -> list[dict]:
    if not isinstance(analysis, dict):
        return []
    value = analysis.get("unknowns")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        return []
    return _unknowns(value)


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


def _unknowns(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    try:
        return _contract_unknowns(value)
    except ValueError:
        return []
