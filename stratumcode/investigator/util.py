from __future__ import annotations

import os
import re
from itertools import count

from .constants import _EXTENSION_LANGUAGE


def _round_indexes(limit: int, start: int = 0):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


def _read_path_norm(path: str) -> str:
    return str(path or "").replace("\\", "/")


def _skip_ws(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


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


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for raw in value if (item := str(raw).strip())]


def _normalize_path(p: str) -> str:
    p = str(p).replace("\\", "/").strip()
    p = re.sub(r"^[A-Za-z]:/", "", p)
    return p.lstrip("./").lower()


def _extension_language(path: str) -> str:
    ext = os.path.splitext(str(path).lower())[1]
    return _EXTENSION_LANGUAGE.get(ext, "")
