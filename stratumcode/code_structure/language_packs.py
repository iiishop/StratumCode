from __future__ import annotations

import builtins as _builtins_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

import yaml

# 运行时内置符号来源：yaml 里声明 runtime_builtins，加载时动态获取，
# 免去手写白名单。目前支持 Python（dir(builtins)），其他语言可扩展。
_RUNTIME_BUILTIN_PROVIDERS: dict[str, callable] = {
    "python.builtins": lambda: frozenset(
        name for name in dir(_builtins_module) if not name.startswith("_")
    ),
}


@dataclass(frozen=True, slots=True)
class RegexQuery:
    pattern: str
    name_group: str = "name"
    signature_group: str = "signature"

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        return cls(
            pattern=str(data.get("pattern") or ""),
            name_group=str(data.get("name_group") or "name"),
            signature_group=str(data.get("signature_group") or "signature"),
        )


@dataclass(frozen=True, slots=True)
class LanguagePack:
    id: str
    extensions: tuple[str, ...]
    symbol_queries: tuple[RegexQuery, ...] = ()
    call_queries: tuple[RegexQuery, ...] = ()
    builtin_symbols: frozenset[str] = frozenset()
    dependency_roots: tuple[str, ...] = ()
    ignored_symbols: frozenset[str] = frozenset()
    ignored_calls: frozenset[str] = frozenset()
    semantic_provider: str = ""
    provenance: str = ""

    @classmethod
    def from_dict(cls, data: dict, source: Path) -> Self:
        syntax = data.get("syntax") if isinstance(data.get("syntax"), dict) else {}
        semantic = data.get("semantic") if isinstance(data.get("semantic"), dict) else {}
        dependency = data.get("dependency") if isinstance(data.get("dependency"), dict) else {}
        builtin_symbols = frozenset(str(item) for item in data.get("builtin_symbols", []))
        for runtime_source in data.get("runtime_builtins", []):
            provider = _RUNTIME_BUILTIN_PROVIDERS.get(str(runtime_source))
            if provider:
                builtin_symbols = builtin_symbols | provider()
        return cls(
            id=str(data["id"]),
            extensions=tuple(str(item).casefold() for item in data.get("extensions", [])),
            symbol_queries=tuple(
                RegexQuery.from_dict(item)
                for item in syntax.get("symbols", [])
                if isinstance(item, dict)
            ),
            call_queries=tuple(
                RegexQuery.from_dict(item)
                for item in syntax.get("calls", [])
                if isinstance(item, dict)
            ),
            builtin_symbols=builtin_symbols,
            dependency_roots=tuple(str(item) for item in dependency.get("roots", [])),
            ignored_symbols=frozenset(str(item) for item in data.get("ignored_symbols", [])),
            ignored_calls=frozenset(str(item) for item in data.get("ignored_calls", [])),
            semantic_provider=str(semantic.get("provider") or ""),
            provenance=source.as_posix(),
        )


class LanguagePackRegistry:
    def __init__(self, packs: list[LanguagePack]) -> None:
        self._packs = list(packs)
        self._by_extension: dict[str, LanguagePack] = {}
        for pack in packs:
            for extension in pack.extensions:
                self._by_extension[extension] = pack

    @classmethod
    def load_default(cls) -> Self:
        root = Path(__file__).with_name("language_packs")
        packs = []
        for path in sorted(root.glob("*.yaml")):
            with path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
            if isinstance(data, dict):
                packs.append(LanguagePack.from_dict(data, path))
        return cls(packs)

    def for_path(self, path: Path) -> LanguagePack | None:
        return self._by_extension.get(path.suffix.casefold())

    def all(self) -> list[LanguagePack]:
        return list(self._packs)
