from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ... import lsp
from ..spec import ToolDef, ToolResult
from .common import _resolve


_LSP_OPS = {
    "symbols": "document_symbols",
    "definition": "definition",
    "references": "references",
    "hover": "hover",
}
_INSPECT_OPS = ("hover", "definition", "references")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_SYMBOL_KIND = {
    1: "file",
    2: "module",
    3: "namespace",
    4: "package",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    15: "string",
    16: "number",
    17: "boolean",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enumMember",
    23: "struct",
    24: "event",
    25: "operator",
    26: "typeParameter",
}


async def _code_nav(params: dict, ctx: dict) -> ToolResult:
    operation = str(params.get("operation") or "").strip()
    if operation not in {*_LSP_OPS, "inspect"}:
        return ToolResult.err("code_nav", f"unsupported operation: {operation}")

    path = str(params.get("path") or "").strip()
    if not path:
        return ToolResult.err("code_nav", "path is required", operation=operation)

    try:
        resolved = _resolve(path, ctx)
    except Exception as exc:
        return ToolResult.err("code_nav", str(exc), operation=operation, path=path)

    workspace = str(ctx.get("directory", "."))
    symbol = str(params.get("symbol") or "").strip()
    if operation == "symbols":
        part = _run_lsp("symbols", params, workspace, resolved, None)
        return _tool_result("symbols", resolved, symbol, part)

    position = _resolve_position(params, resolved, symbol)
    if position is None:
        return ToolResult.err(
            "code_nav",
            "line/character or symbol is required for positional code navigation.",
            operation=operation,
            path=str(resolved),
        )

    if operation == "inspect":
        parts = {
            item: _run_lsp(item, params, workspace, resolved, position)
            for item in _INSPECT_OPS
        }
        ok = any(part["ok"] for part in parts.values())
        server = next((part.get("server", "") for part in parts.values() if part.get("server")), "")
        payload = {
            "ok": ok,
            "operation": "inspect",
            "server": server,
            "path": str(resolved),
            "symbol": symbol or position.get("symbol", ""),
            "position": _public_position(position),
            "results": parts,
            "hints": _inspect_hints(parts),
        }
        return ToolResult.ok(
            "code_nav inspect" if ok else "code_nav inspect no result",
            _dump(payload),
            operation="inspect",
            server=server,
            path=str(resolved),
            symbol=payload["symbol"],
            line=payload["position"]["line"],
            character=payload["position"]["character"],
            status="ok" if ok else "no_result",
        )

    part = _run_lsp(operation, params, workspace, resolved, position)
    return _tool_result(operation, resolved, symbol or position.get("symbol", ""), part)


def _run_lsp(operation: str, params: dict, workspace: str, resolved: Path, position: dict | None) -> dict:
    request = dict(params)
    request["operation"] = _LSP_OPS[operation]
    request["path"] = str(resolved)
    if position is not None:
        request["line"] = position["line"]
        request["character"] = position["character"]
    try:
        raw = lsp.query(request, workspace)
    except Exception as exc:
        return {
            "ok": False,
            "kind": "error",
            "operation": operation,
            "server": "",
            "path": str(resolved),
            "position": _public_position(position) if position else None,
            "message": str(exc),
        }

    normalized = _normalize(operation, raw.get("result"))
    has_result = _has_result(operation, raw.get("result"), normalized)
    return {
        "ok": has_result,
        "kind": "result" if has_result else "no_result",
        "operation": operation,
        "server": raw.get("server", ""),
        "path": raw.get("path", str(resolved)),
        "position": raw.get("position") or (_public_position(position) if position else None),
        "result": normalized,
        "raw_result_type": type(raw.get("result")).__name__,
        "message": "" if has_result else _no_result_message(operation, position),
    }


def _tool_result(operation: str, resolved: Path, symbol: str, part: dict) -> ToolResult:
    payload = {
        "ok": part["ok"],
        "operation": operation,
        "server": part.get("server", ""),
        "path": part.get("path", str(resolved)),
        "symbol": symbol,
        "position": part.get("position"),
        "result": part.get("result"),
        "message": part.get("message", ""),
        "hints": _hints(operation, part, symbol),
    }
    meta = {
        "operation": operation,
        "server": payload["server"],
        "path": payload["path"],
        "symbol": symbol,
        "status": "ok" if part["ok"] else part.get("kind", "no_result"),
    }
    if part.get("position"):
        meta.update({
            "line": part["position"].get("line"),
            "character": part["position"].get("character"),
        })

    if part.get("kind") == "error":
        return ToolResult.err("code_nav", _dump(payload), **meta)
    title = f"code_nav {operation}" if part["ok"] else f"code_nav {operation} no result"
    return ToolResult.ok(title, _dump(payload), **meta)


def _resolve_position(params: dict, path: Path, symbol: str) -> dict | None:
    if symbol:
        match = _find_symbol_position(path, symbol)
        if match:
            return match
    if "line" not in params and "character" not in params and "column" not in params:
        return None
    line = max(1, int(params.get("line") or 1))
    character = max(1, int(params.get("character") or params.get("column") or 1))
    return _snap_to_identifier(path, line, character)


def _find_symbol_position(path: Path, symbol: str) -> dict | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    matches: list[tuple[int, int, int]] = []
    for index, text in enumerate(lines, start=1):
        for match in pattern.finditer(text):
            matches.append((_symbol_score(text, symbol), index, match.start() + 1))
    if not matches:
        return None
    _, line, character = sorted(matches)[0]
    return {"line": line, "character": character, "symbol": symbol, "source": "symbol"}


def _symbol_score(text: str, symbol: str) -> int:
    stripped = text.strip()
    if stripped.startswith("#") or stripped.startswith("//"):
        return 90
    if re.search(rf"\b(class|def|function|const|let|var)\s+{re.escape(symbol)}\b", text):
        return 0
    if re.search(rf"\b{re.escape(symbol)}\s*[:=]", text):
        return 1
    if re.search(rf"\b{re.escape(symbol)}\s*\(", text):
        return 5
    return 20


def _snap_to_identifier(path: Path, line: int, character: int) -> dict:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"line": line, "character": character, "source": "input"}
    if line < 1 or line > len(lines):
        return {"line": line, "character": character, "source": "input"}
    text = lines[line - 1]
    zero = max(0, character - 1)
    spans = list(_IDENTIFIER_RE.finditer(text))
    if not spans:
        return {"line": line, "character": character, "source": "input"}
    ranked = sorted(
        spans,
        key=lambda match: 0 if match.start() <= zero < match.end() else min(
            abs(zero - match.start()),
            abs(zero - max(match.end() - 1, match.start())),
        ),
    )
    match = ranked[0]
    probe = match.start() + min(1, max(0, len(match.group(0)) - 1)) + 1
    return {
        "line": line,
        "character": probe,
        "symbol": match.group(0),
        "source": "identifier" if probe != character else "input",
    }


def _normalize(operation: str, value: Any) -> Any:
    if operation == "symbols":
        items: list[dict] = []
        _flatten_symbols(value or [], items)
        return {"items": items[:120], "total": len(items), "truncated": len(items) > 120}
    if operation in {"definition", "references"}:
        values = value if isinstance(value, list) else ([value] if value else [])
        items = [_normalize_location(item) for item in values]
        items = [item for item in items if item]
        return {"items": items[:80], "total": len(items), "truncated": len(items) > 80}
    if operation == "hover":
        return {"text": _hover_text(value), "range": value.get("range") if isinstance(value, dict) else None}
    return value


def _flatten_symbols(value: Any, items: list[dict], container: str = "") -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if not isinstance(item, dict):
            continue
        location = item.get("location") or {}
        range_value = item.get("range") or location.get("range") or {}
        start = (range_value.get("start") or {}) if isinstance(range_value, dict) else {}
        name = item.get("name", "")
        items.append({
            "name": name,
            "kind": _SYMBOL_KIND.get(item.get("kind"), item.get("kind")),
            "container": item.get("containerName") or container,
            "path": _uri_to_path(location.get("uri", "")) if location.get("uri") else "",
            "line": int(start.get("line", 0)) + 1,
            "character": int(start.get("character", 0)) + 1,
        })
        _flatten_symbols(item.get("children") or [], items, name or container)


def _normalize_location(item: Any) -> dict | None:
    if not isinstance(item, dict):
        return None
    target_uri = item.get("targetUri") or item.get("uri")
    range_value = item.get("targetSelectionRange") or item.get("targetRange") or item.get("range") or {}
    start = range_value.get("start") or {}
    end = range_value.get("end") or {}
    if not target_uri:
        return None
    return {
        "path": _uri_to_path(target_uri),
        "line": int(start.get("line", 0)) + 1,
        "character": int(start.get("character", 0)) + 1,
        "end_line": int(end.get("line", start.get("line", 0))) + 1,
        "end_character": int(end.get("character", start.get("character", 0))) + 1,
    }


def _uri_to_path(uri: str) -> str:
    parsed = urlparse(uri)
    raw = unquote(parsed.path or uri)
    if re.match(r"^/[A-Za-z]:", raw):
        raw = raw[1:]
    return raw.replace("/", "\\") if "\\" in str(Path.cwd()) else raw


def _hover_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    contents = value.get("contents")
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return str(contents.get("value") or contents.get("language") or "")
    if isinstance(contents, list):
        return "\n".join(filter(None, (_hover_text({"contents": item}) for item in contents)))
    return ""


def _has_result(operation: str, raw: Any, normalized: Any) -> bool:
    if raw is None or raw == []:
        return False
    if operation == "hover":
        return bool(normalized.get("text"))
    if operation in {"definition", "references", "symbols"}:
        return bool(normalized.get("total"))
    return True


def _public_position(position: dict | None) -> dict:
    if not position:
        return {}
    return {
        "line": int(position.get("line") or 1),
        "character": int(position.get("character") or 1),
        "symbol": position.get("symbol", ""),
        "source": position.get("source", "input"),
    }


def _no_result_message(operation: str, position: dict | None) -> str:
    if operation == "symbols":
        return "The language server returned no document symbols for this file."
    symbol = (position or {}).get("symbol")
    if symbol:
        return f"The language server returned no {operation} result for symbol '{symbol}' at this position."
    return f"The language server returned no {operation} result at this position."


def _hints(operation: str, part: dict, symbol: str) -> list[str]:
    if part["ok"] or part.get("kind") == "error":
        return []
    hints = [
        "Empty LSP result is not the same as server failure.",
        "Use operation='inspect' with a symbol name when exact cursor coordinates are uncertain.",
    ]
    if operation == "references" and symbol:
        hints.append("If semantic references are empty, confirm the identifier spelling and then use grep as a text fallback.")
    return hints


def _inspect_hints(parts: dict[str, dict]) -> list[str]:
    if any(part["ok"] for part in parts.values()):
        return []
    return [
        "No inspect sub-operation produced a result.",
        "Retry with a concrete symbol name or a cursor position inside an identifier, not on indentation.",
    ]


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


code_nav_tool = ToolDef(
    name="code_nav",
    description=(
        "Use LSP semantic code navigation for a workspace file. Prefer operation='inspect' "
        "with a symbol name when you are investigating code and exact cursor coordinates "
        "are uncertain. Supports document symbols, definition, references, and hover. "
        "line/character are 1-based and should point inside an identifier; the tool will "
        "snap nearby whitespace to the closest identifier on the same line."
    ),
    parameters={
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["inspect", "symbols", "definition", "references", "hover"],
                "description": "Semantic navigation operation. Use inspect for hover+definition+references together.",
            },
            "path": {"type": "string", "description": "File path relative to the workspace root."},
            "symbol": {"type": "string", "description": "Optional identifier name. Preferred when coordinates are uncertain."},
            "line": {"type": "integer", "description": "1-based line number for inspect/definition/references/hover."},
            "character": {"type": "integer", "description": "1-based character offset for inspect/definition/references/hover."},
            "server_id": {"type": "string", "description": "Optional explicit LSP server id."},
        },
        "required": ["operation", "path"],
    },
    execute=_code_nav,
)

TOOL = code_nav_tool
