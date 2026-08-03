from __future__ import annotations

from pathlib import Path

from .contracts import CallSite, CodeStructureGraph, Diagnostic, Symbol
from .graph_builder import CallGraphBuilder
from .language_packs import LanguagePack, LanguagePackRegistry
from .providers import ProviderRegistry

IGNORED_DIRS = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
})
MAX_FILE_BYTES = 512_000
MAX_FILES = 2_000


class CodeStructureEngine:
    def __init__(
        self,
        *,
        packs: LanguagePackRegistry | None = None,
        providers: ProviderRegistry | None = None,
        graph_builder: CallGraphBuilder | None = None,
        semantic_mode: str = "fast",
    ) -> None:
        self.packs = packs or LanguagePackRegistry.load_default()
        self.providers = providers or ProviderRegistry()
        self.graph_builder = graph_builder or CallGraphBuilder(self.providers)
        self.semantic_mode = semantic_mode

    def analyze(self, workspace_dir: str) -> CodeStructureGraph:
        root = Path(workspace_dir).resolve()
        diagnostics: list[Diagnostic] = []
        symbols: list[Symbol] = []
        calls: list[CallSite] = []
        files = self._source_files(root, diagnostics)
        packs_by_language = {pack.id: pack for pack in self.packs.all()}
        for path, pack in files:
            rel_path = path.relative_to(root).as_posix()
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                diagnostics.append(Diagnostic("warning", "Skipped non UTF-8 source file.", rel_path))
                continue
            except OSError as exc:
                diagnostics.append(Diagnostic("warning", f"Failed to read source file: {exc}", rel_path))
                continue
            file_symbols = self._extract_symbols(path, rel_path, pack, source, diagnostics)
            symbols.extend(file_symbols)
            calls.extend(self._extract_calls(path, rel_path, pack, source, file_symbols, diagnostics))
        return self.graph_builder.build(
            symbols=symbols,
            calls=calls,
            packs=packs_by_language,
            diagnostics=diagnostics,
            meta={
                "workspace": str(root),
                "file_count": len(files),
                "packs": [pack.id for pack in self.packs.all()],
                "semantic_mode": self.semantic_mode,
                "semantic_providers": [provider.name for provider in self.providers.semantic],
            },
            workspace_dir=str(root),
        )

    def _source_files(self, root: Path, diagnostics: list[Diagnostic]) -> list[tuple[Path, LanguagePack]]:
        files: list[tuple[Path, LanguagePack]] = []
        for path in root.rglob("*"):
            if len(files) >= MAX_FILES:
                diagnostics.append(Diagnostic("warning", f"Stopped after {MAX_FILES} source files."))
                break
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if any(part in IGNORED_DIRS or part.startswith(".") for part in rel.parts):
                continue
            pack = self.packs.for_path(path)
            if pack is None:
                continue
            if any(part in pack.dependency_roots for part in rel.parts):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    diagnostics.append(Diagnostic("info", f"Skipped file over {MAX_FILE_BYTES} bytes.", rel.as_posix()))
                    continue
            except OSError:
                continue
            files.append((path, pack))
        return files

    def _extract_symbols(
        self,
        path: Path,
        rel_path: str,
        pack: LanguagePack,
        source: str,
        diagnostics: list[Diagnostic],
    ) -> list[Symbol]:
        for provider in self.providers.syntax:
            try:
                symbols = provider.extract_symbols(path, rel_path, pack, source)
            except Exception as exc:
                diagnostics.append(Diagnostic("warning", f"Symbol provider {provider.name} failed: {exc}", rel_path))
                continue
            if symbols:
                return symbols
        return []

    def _extract_calls(
        self,
        path: Path,
        rel_path: str,
        pack: LanguagePack,
        source: str,
        symbols: list[Symbol],
        diagnostics: list[Diagnostic],
    ) -> list[CallSite]:
        for provider in self.providers.syntax:
            try:
                return provider.extract_calls(path, rel_path, pack, source, symbols)
            except Exception as exc:
                diagnostics.append(Diagnostic("warning", f"Call provider {provider.name} failed: {exc}", rel_path))
        return []


def analyze_workspace(workspace_dir: str, *, semantic: str = "fast") -> dict:
    providers = ProviderRegistry.with_lsp() if semantic == "lsp" else ProviderRegistry()
    return CodeStructureEngine(providers=providers, semantic_mode=semantic).analyze(workspace_dir).to_json()
