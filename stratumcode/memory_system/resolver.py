from __future__ import annotations

import re

from . import store

_ORDINAL_RE = re.compile(r"(?:第\s*)?([0-9一二三四五六七八九十]+)\s*(?:点|项|条|个)")


def resolve_references(workspace_dir: str, session_id: int | None, query: str) -> list[dict]:
    refs = store.list_refs(workspace_dir, session_id, limit=80)
    if not refs:
        return []
    ordinal = _ordinal(query)
    if ordinal is not None:
        matches = [
            {**ref, "confidence": "high", "reason": "ordinal_reference"}
            for ref in refs
            if _ref_index(ref) == ordinal
        ]
        if matches:
            return matches[:5]
    terms = _terms(query)
    if not terms:
        return []
    scored = []
    for ref in refs:
        haystack = _terms(" ".join(str(ref.get(key) or "") for key in ("label", "content", "ref_key")))
        score = len(terms & haystack)
        if score:
            scored.append((score, ref))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {**ref, "confidence": "medium" if score >= 2 else "low", "reason": "lexical_reference"}
        for score, ref in scored[:5]
    ]


def _ordinal(value: str) -> int | None:
    match = _ORDINAL_RE.search(value or "")
    if not match:
        return None
    return _parse_number(match.group(1))


def _parse_number(value: str) -> int | None:
    text = value.strip()
    if text.isdigit():
        return int(text)
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + digits.get(text[1:], 0)
    if text.endswith("十"):
        return digits.get(text[:-1], 0) * 10
    if "十" in text:
        left, right = text.split("十", 1)
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits.get(text)


def _ref_index(ref: dict) -> int | None:
    payload = ref.get("payload") if isinstance(ref.get("payload"), dict) else {}
    index = payload.get("index")
    if isinstance(index, int):
        return index
    key = str(ref.get("ref_key") or "")
    match = re.search(r":([0-9]+)$", key)
    return int(match.group(1)) if match else None


def _terms(value: str) -> set[str]:
    return {item.casefold() for item in re.findall(r"[\w\u4e00-\u9fff]+", value or "") if len(item) > 1}
