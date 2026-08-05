from __future__ import annotations

import hashlib
import re
from bisect import bisect_right
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse

from .. import lsp
from .contracts import CallSite, DocParam, DocReturn, FunctionDoc, LspResolution, SourceRange, Symbol
from .language_packs import LanguagePack, RegexQuery


class SyntaxProvider(Protocol):
    name: str

    def extract_symbols(self, file: Path, rel_path: str, language: LanguagePack, source: str) -> list[Symbol]:
        """Return symbols visible in a source file."""

    def extract_calls(
        self,
        file: Path,
        rel_path: str,
        language: LanguagePack,
        source: str,
        symbols: list[Symbol],
    ) -> list[CallSite]:
        """Return call sites found inside extracted symbols."""


class SemanticProvider(Protocol):
    name: str

    def resolve(self, call: CallSite, symbols: list[Symbol], workspace_dir: str) -> str | None:
        """Return a resolved symbol id when available."""


class RegexSyntaxProvider:
    name = "regex-query"

    def extract_symbols(self, file: Path, rel_path: str, language: LanguagePack, source: str) -> list[Symbol]:
        line_offsets = _line_offsets(source)
        matches = []
        for query in language.symbol_queries:
            matches.extend(_iter_matches(query, source))
        matches.sort(key=lambda item: item[0].start())
        symbols: list[Symbol] = []
        for index, (match, query) in enumerate(matches):
            name = _group(match, query.name_group)
            if not name or name in language.ignored_symbols:
                continue
            signature = _group(match, query.signature_group) or name
            signature_start = _group_start(match, query.signature_group)
            start_line, start_col = _line_col(line_offsets, signature_start)
            if index + 1 < len(matches):
                next_match, next_query = matches[index + 1]
                end_line = _line_col(line_offsets, _group_start(next_match, next_query.signature_group))[0] - 1
            else:
                end_line = source.count("\n") + 1
            end_line = _syntax_end_line(language, source, line_offsets, start_line, start_col, end_line)
            end_line = max(start_line, end_line)
            symbol_id = _stable_id("symbol", language.id, rel_path, name, str(start_line))
            symbols.append(Symbol(
                id=symbol_id,
                name=name,
                signature=" ".join(signature.strip().split()),
                language=language.id,
                file=rel_path,
                range=SourceRange(start_line, start_col, end_line, 1),
                doc=_extract_doc(language, source, line_offsets, match, signature),
                provenance=[self.name, language.provenance],
            ))
        return symbols

    def extract_calls(
        self,
        file: Path,
        rel_path: str,
        language: LanguagePack,
        source: str,
        symbols: list[Symbol],
    ) -> list[CallSite]:
        line_offsets = _line_offsets(source)
        calls: list[CallSite] = []
        for symbol in symbols:
            body_start = _offset_for_line(line_offsets, symbol.range.start_line + 1)
            body_end = _offset_for_line(line_offsets, symbol.range.end_line + 1) if symbol.range.end_line < len(line_offsets) else len(source)
            body = source[body_start:body_end]
            ordered = []
            for query in language.call_queries:
                ordered.extend(_iter_matches(query, body))
            ordered.sort(key=lambda item: item[0].start())
            order = 0
            for match, query in ordered:
                name = _group(match, query.name_group)
                if not name or name in language.ignored_calls:
                    continue
                absolute_start = body_start + match.start()
                absolute_end = body_start + match.end()
                start_line, start_col = _line_col(line_offsets, absolute_start)
                end_line, end_col = _line_col(line_offsets, absolute_end)
                if name == symbol.name and start_line == symbol.range.start_line:
                    continue
                if _ignored_call_context(language, body, match.start()):
                    continue
                order += 1
                calls.append(CallSite(
                    id=_stable_id("call", language.id, rel_path, symbol.id, str(order), str(start_line), name),
                    caller_id=symbol.id,
                    name=name,
                    call_text=" ".join(match.group(0).strip().split()),
                    line_text=_source_line(source, line_offsets, start_line),
                    language=language.id,
                    file=rel_path,
                    range=SourceRange(start_line, start_col, end_line, end_col),
                    order=order,
                    provenance=[self.name, language.provenance],
                ))
        return calls


class NameIndexSemanticProvider:
    name = "name-index"

    def resolve(self, call: CallSite, symbols: list[Symbol], workspace_dir: str) -> str | None:
        lookup_name = call.name.rsplit("::", 1)[-1]
        if "." in call.name:
            return None
        same_name = [item for item in symbols if item.language == call.language and item.name == lookup_name]
        if not same_name:
            return None
        same_file = [item for item in same_name if item.file == call.file]
        if len(same_file) == 1:
            return same_file[0].id
        if len(same_name) == 1:
            return same_name[0].id
        return None


class LspDefinitionSemanticProvider:
    name = "lsp-definition"

    def __init__(self, max_requests: int | None = None) -> None:
        self.max_requests = max_requests
        self._requests = 0
        self._responses = 0
        self._resolved = 0
        self._disabled = False
        self._disabled_languages: set[str] = set()
        self._server = ""
        self._error = ""
        self._cache: dict[tuple[str, int, int], LspResolution | None] = {}

    def resolve(self, call: CallSite, symbols: list[Symbol], workspace_dir: str) -> str | None:
        if self._disabled or call.language in self._disabled_languages:
            return None
        if self.max_requests is not None and self._requests >= self.max_requests:
            return None
        key = (call.file, call.range.start_line, call.range.start_col)
        if key in self._cache:
            cached = self._cache[key]
            return cached.target if cached else None
        self._requests += 1
        try:
            raw = lsp.query({
                "operation": "definition",
                "path": str(Path(workspace_dir, call.file)),
                "line": call.range.start_line,
                "character": call.range.start_col,
            }, workspace_dir)
        except Exception as exc:
            self._disabled_languages.add(call.language)
            self._error = str(exc)
            self._cache[key] = None
            return None
        self._responses += 1
        self._server = str(raw.get("server") or self._server)
        result = _lsp_definition_result(raw.get("result"), symbols, workspace_dir)
        if result and result.target:
            self._resolved += 1
        self._cache[key] = result
        return result.target if result else None

    def resolve_many(
        self,
        calls: list[CallSite],
        symbols: list[Symbol],
        workspace_dir: str,
        concurrency: int = 8,
    ) -> dict[str, LspResolution]:
        """批量解析调用目标：复用 LSP client + 线程池并发（lsp.query_batch）。

        返回 {call_id: LspResolution}。LSP 确认的外部定义（target=None,
        external=True）也会进结果，由调用方决定分类（external / builtin）。
        只处理未缓存的调用；语言被禁用的调用跳过。无配额上限。
        """
        if self._disabled or not calls:
            return {}
        params_list: list[dict] = []
        valid: list[CallSite] = []
        for call in calls:
            if self.max_requests is not None and self._requests >= self.max_requests:
                break
            key = (call.file, call.range.start_line, call.range.start_col)
            if key in self._cache:
                continue
            params_list.append({
                "operation": "definition",
                "path": str(Path(workspace_dir, call.file)),
                "line": call.range.start_line,
                "character": call.range.start_col,
            })
            valid.append(call)
        if not params_list:
            return {}
        responses = lsp.query_batch(params_list, workspace_dir, concurrency=concurrency)
        resolved: dict[str, LspResolution] = {}
        for call, raw in zip(valid, responses):
            key = (call.file, call.range.start_line, call.range.start_col)
            self._requests += 1
            if "error" in raw:
                self._disabled_languages.add(call.language)
                self._error = str(raw.get("error") or self._error)
                self._cache[key] = None
                continue
            self._responses += 1
            self._server = str(raw.get("server") or self._server)
            result = _lsp_definition_result(raw.get("result"), symbols, workspace_dir)
            if result and result.target:
                self._resolved += 1
            self._cache[key] = result
            if result is not None:
                resolved[call.id] = result
        return resolved

    def status(self) -> dict:
        return {
            "provider": self.name,
            "attempted": self._requests > 0,
            "used": self._responses > 0,
            "server": self._server,
            "error": self._error,
            "requests": self._requests,
            "responses": self._responses,
            "resolved": self._resolved,
            "disabled": self._disabled,
            "disabled_languages": sorted(self._disabled_languages),
            "fallback": "name-index" if self._disabled_languages or self._responses == 0 else "",
        }


class ProviderRegistry:
    def __init__(
        self,
        syntax: Iterable[SyntaxProvider] | None = None,
        semantic: Iterable[SemanticProvider] | None = None,
    ) -> None:
        self.syntax = list(syntax or [RegexSyntaxProvider()])
        self.semantic = list(semantic or [NameIndexSemanticProvider()])

    @classmethod
    def with_lsp(cls, max_requests: int | None = None) -> "ProviderRegistry":
        return cls(semantic=[
            LspDefinitionSemanticProvider(max_requests=max_requests),
            NameIndexSemanticProvider(),
        ])

    def semantic_status(self, mode: str) -> dict:
        if mode != "lsp":
            return {
                "mode": mode,
                "provider": "",
                "attempted": False,
                "used": False,
                "server": "",
                "error": "",
                "requests": 0,
                "responses": 0,
                "resolved": 0,
                "disabled": False,
                "disabled_languages": [],
                "fallback": "name-index",
            }
        for provider in self.semantic:
            status = getattr(provider, "status", None)
            if callable(status):
                return {
                    "mode": mode,
                    **status(),
                }
        return {
            "mode": mode,
            "provider": "",
            "attempted": False,
            "used": False,
            "server": "",
            "error": "",
            "requests": 0,
            "responses": 0,
            "resolved": 0,
            "disabled": False,
            "disabled_languages": [],
            "fallback": "name-index",
        }


def _iter_matches(query: RegexQuery, source: str):
    if not query.pattern:
        return []
    regex = re.compile(query.pattern, re.MULTILINE)
    return [(match, query) for match in regex.finditer(source)]


def _extract_doc(language: LanguagePack, source: str, line_offsets: list[int], match: re.Match, signature: str) -> FunctionDoc:
    raw = ""
    if language.id == "python":
        raw = _python_docstring(source, line_offsets, match) or _leading_comment(source, line_offsets, match.start())
    else:
        raw = _leading_comment(source, line_offsets, match.start())
    return _parse_doc(raw, signature)


def _python_docstring(source: str, line_offsets: list[int], match: re.Match) -> str:
    body_offset = _offset_for_line(line_offsets, _line_col(line_offsets, match.end())[0] + 1)
    body = source[body_offset:]
    doc_match = re.match(r"\s*(?P<quote>\"\"\"|''')(?P<body>.*?)(?P=quote)", body, re.DOTALL)
    if not doc_match:
        return ""
    return _clean_doc_lines(doc_match.group("body").splitlines())


def _leading_comment(source: str, line_offsets: list[int], offset: int) -> str:
    prefix = source[:offset].rstrip()
    block_match = re.search(r"(?s)(/\*\*?.*?\*/)\s*$", prefix)
    if block_match:
        return _clean_block_comment(block_match.group(1))
    line_index = bisect_right(line_offsets, offset) - 2
    lines = source.splitlines()
    collected: list[str] = []
    while line_index >= 0:
        text = lines[line_index].strip()
        if not text:
            if collected:
                break
            line_index -= 1
            continue
        if text.startswith("#"):
            collected.append(text[1:].strip())
        elif text.startswith("//"):
            collected.append(text.lstrip("/").strip())
        else:
            break
        line_index -= 1
    collected.reverse()
    return _clean_doc_lines(collected)


def _clean_block_comment(comment: str) -> str:
    body = re.sub(r"^/\*\*?", "", comment.strip())
    body = re.sub(r"\*/$", "", body.strip())
    return _clean_doc_lines(body.splitlines())


def _clean_doc_lines(lines: list[str]) -> str:
    cleaned = []
    for line in lines:
        text = line.strip()
        if text.startswith("*"):
            text = text[1:].strip()
        cleaned.append(text)
    while cleaned and not cleaned[0]:
        cleaned.pop(0)
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return "\n".join(cleaned).strip()


def _parse_doc(raw: str, signature: str) -> FunctionDoc:
    params = _signature_params(signature)
    returns = DocReturn(type=_signature_return_type(signature))
    if not raw:
        return FunctionDoc(params=params, returns=returns)

    lines = raw.splitlines()
    summary = _first_text_line(lines)
    param_docs: dict[str, str] = {}
    return_description = ""
    description_lines: list[str] = []
    section = "description"
    for line in lines[1:]:
        text = line.strip()
        if not text:
            continue
        lower = text.lower().rstrip(":")
        if lower in {"args", "arguments", "parameters", "params"}:
            section = "params"
            continue
        if lower in {"returns", "return"}:
            section = "returns"
            continue
        js_param = re.match(r"@param\s+(?:\{(?P<type>[^}]+)\}\s+)?(?P<name>[\w$.-]+)\s*(?P<desc>.*)", text)
        if js_param:
            name = js_param.group("name").strip("[]")
            param_docs[name] = js_param.group("desc").strip()
            _merge_param_type(params, name, js_param.group("type") or "")
            continue
        js_return = re.match(r"@returns?\s+(?:\{(?P<type>[^}]+)\}\s+)?(?P<desc>.*)", text)
        if js_return:
            returns = DocReturn(type=(js_return.group("type") or returns.type).strip(), description=js_return.group("desc").strip())
            continue
        py_param = re.match(r"(?P<name>[\w$.-]+)\s*:\s*(?P<desc>.*)", text)
        if section == "params" and py_param:
            param_docs[py_param.group("name")] = py_param.group("desc").strip()
            continue
        if section == "returns":
            return_description = " ".join(part for part in [return_description, text] if part).strip()
            continue
        description_lines.append(text)

    merged_params = [
        DocParam(param.name, param.type, param_docs.get(param.name, param.description), param.default)
        for param in params
    ]
    if return_description and not returns.description:
        returns = DocReturn(type=returns.type, description=return_description)
    return FunctionDoc(
        summary=summary,
        description="\n".join(description_lines).strip(),
        params=merged_params,
        returns=returns,
        raw=raw,
    )


def _first_text_line(lines: list[str]) -> str:
    for line in lines:
        text = line.strip()
        if text and not text.startswith("@"):
            return text
    return ""


def _signature_params(signature: str) -> list[DocParam]:
    match = re.search(r"\((?P<params>[^)]*)\)", signature)
    if not match:
        return []
    params: list[DocParam] = []
    for raw_param in _split_params(match.group("params")):
        item = raw_param.strip()
        if not item or item in {"self", "cls"}:
            continue
        item = item.lstrip("*")
        left, _, default = item.partition("=")
        name_part, _, type_part = left.partition(":")
        name, type_name = _param_name_type(name_part.strip(), type_part.strip())
        if not name:
            continue
        params.append(DocParam(
            name=name,
            type=type_name,
            default=default.strip(),
        ))
    return params


def _param_name_type(name_part: str, type_part: str) -> tuple[str, str]:
    if type_part:
        return name_part.strip(), type_part.strip()
    modifiers = {"const", "final", "readonly", "ref", "out", "in"}
    tokens = [token for token in name_part.split() if token not in modifiers]
    if len(tokens) < 2:
        return name_part.strip(), ""
    c_like_types = {
        "bool", "boolean", "byte", "char", "decimal", "double", "float", "int", "integer",
        "long", "number", "short", "string", "str", "void",
    }
    first = tokens[0].strip("*&")
    last = tokens[-1].strip("*&")
    if first.lower() in c_like_types or any(char in tokens[0] for char in "<>[]*&"):
        return last, " ".join(tokens[:-1]).strip()
    return tokens[0].strip("*&"), " ".join(tokens[1:]).strip()


def _signature_return_type(signature: str) -> str:
    arrow = re.search(r"->\s*(?P<type>[^:]+)", signature)
    if arrow:
        return arrow.group("type").strip()
    colon = re.search(r"\)\s*:\s*(?P<type>[^{=]+)", signature)
    if colon:
        return colon.group("type").strip()
    return ""


def _split_params(text: str) -> list[str]:
    params: list[str] = []
    current: list[str] = []
    depth = 0
    pairs = {"(": ")", "[": "]", "{": "}", "<": ">"}
    closers = set(pairs.values())
    for char in text:
        if char in pairs:
            depth += 1
        elif char in closers and depth > 0:
            depth -= 1
        if char == "," and depth == 0:
            params.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        params.append("".join(current))
    return params


def _merge_param_type(params: list[DocParam], name: str, type_name: str) -> None:
    if not type_name:
        return
    for index, param in enumerate(params):
        if param.name == name and not param.type:
            params[index] = DocParam(param.name, type_name.strip(), param.description, param.default)
            return


def _group(match: re.Match, name: str) -> str:
    try:
        return str(match.group(name) or "").strip()
    except IndexError:
        return ""


def _group_start(match: re.Match, name: str) -> int:
    try:
        start = match.start(name)
    except IndexError:
        return match.start()
    return start if start >= 0 else match.start()


def _ignored_call_context(language: LanguagePack, source: str, offset: int) -> bool:
    if language.id != "csharp":
        return False
    prefix = source[:offset].rstrip()
    return bool(re.search(r"\bnew$", prefix))


def _syntax_end_line(
    language: LanguagePack,
    source: str,
    offsets: list[int],
    start_line: int,
    start_col: int,
    fallback_end_line: int,
) -> int:
    if language.id != "python":
        return fallback_end_line
    return _python_indented_block_end_line(source, offsets, start_line, start_col, fallback_end_line)


def _python_indented_block_end_line(
    source: str,
    offsets: list[int],
    start_line: int,
    start_col: int,
    fallback_end_line: int,
) -> int:
    def_indent = max(0, start_col - 1)
    last_body_line = start_line
    for line in range(start_line + 1, fallback_end_line + 1):
        text = _source_line(source, offsets, line)
        if not text:
            continue
        indent = _line_indent(source, offsets, line)
        if indent <= def_indent:
            return last_body_line
        last_body_line = line
    return fallback_end_line


def _line_indent(source: str, offsets: list[int], line: int) -> int:
    start = _offset_for_line(offsets, line)
    end = _offset_for_line(offsets, line + 1) if line < len(offsets) else len(source)
    indent = 0
    for char in source[start:end]:
        if char == " ":
            indent += 1
        elif char == "\t":
            indent += 4
        else:
            break
    return indent


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    offsets.extend(index + 1 for index, char in enumerate(source) if char == "\n")
    return offsets


def _line_col(offsets: list[int], offset: int) -> tuple[int, int]:
    line_index = bisect_right(offsets, offset) - 1
    return line_index + 1, offset - offsets[line_index] + 1


def _offset_for_line(offsets: list[int], line: int) -> int:
    if line <= 1:
        return 0
    if line - 1 >= len(offsets):
        return offsets[-1]
    return offsets[line - 1]


def _source_line(source: str, offsets: list[int], line: int) -> str:
    start = _offset_for_line(offsets, line)
    end = _offset_for_line(offsets, line + 1) if line < len(offsets) else len(source)
    return source[start:end].strip()


def _stable_id(*parts: str) -> str:
    raw = "\0".join(parts)
    return parts[0] + ":" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _lsp_definition_result(value, symbols: list[Symbol], workspace_dir: str) -> LspResolution | None:
    """把 LSP definition 返回映射为解析结果。

    定义在 workspace 内 → LspResolution(target=symbol_id)。
    定义在 workspace 外（内置函数 typeshed、标准库、第三方包）→
    LspResolution(external=True) —— LSP 确认了符号存在，只是不在项目内。
    无有效位置 → None。
    """
    locations = value if isinstance(value, list) else ([value] if isinstance(value, dict) else [])
    root = Path(workspace_dir).resolve()
    for item in locations:
        if not isinstance(item, dict):
            continue
        uri = str(item.get("targetUri") or item.get("uri") or "")
        range_value = item.get("targetSelectionRange") or item.get("targetRange") or item.get("range") or {}
        start = range_value.get("start") if isinstance(range_value, dict) else {}
        if not uri or not isinstance(start, dict):
            continue
        parsed = urlparse(uri)
        path_text = unquote(parsed.path or uri)
        if path_text.startswith("/") and len(path_text) > 2 and path_text[2] == ":":
            path_text = path_text[1:]
        try:
            rel = Path(path_text).resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            # 定义位置无法映射到 workspace —— 外部符号（typeshed / 标准库 / 第三方）
            return LspResolution(target=None, external=True)
        line = int(start.get("line", 0)) + 1
        for symbol in symbols:
            if symbol.file == rel and symbol.range.start_line <= line <= symbol.range.end_line:
                return LspResolution(target=symbol.id)
    return None
