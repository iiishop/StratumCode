from __future__ import annotations

import hashlib

from .contracts import CallEdge, CallSite, CodeStructureGraph, Diagnostic, GraphNode, Symbol
from .language_packs import LanguagePack
from .providers import ProviderRegistry


class CallGraphBuilder:
    def __init__(self, providers: ProviderRegistry | None = None) -> None:
        self.providers = providers or ProviderRegistry()

    def build(
        self,
        *,
        symbols: list[Symbol],
        calls: list[CallSite],
        packs: dict[str, LanguagePack],
        diagnostics: list[Diagnostic],
        meta: dict,
        workspace_dir: str = "",
    ) -> CodeStructureGraph:
        symbol_nodes = [
            GraphNode(
                id=symbol.id,
                label=symbol.signature,
                name=symbol.name,
                signature=symbol.signature,
                language=symbol.language,
                file=symbol.file,
                line=symbol.range.start_line,
                kind="project",
                doc=symbol.doc,
                provenance=symbol.provenance,
            )
            for symbol in symbols
        ]
        nodes_by_id = {node.id: node for node in symbol_nodes}
        edges: list[CallEdge] = []
        for call in calls:
            target, kind, confidence, provenance = self._resolve_call(call, symbols, packs.get(call.language), workspace_dir)
            if target is None:
                target = self._external_node_id(call, kind)
                nodes_by_id.setdefault(target, GraphNode(
                    id=target,
                    label=call.name,
                    name=call.name,
                    signature=call.name,
                    language=call.language,
                    file="",
                    line=0,
                    kind=kind,
                    provenance=provenance,
                ))
            edges.append(CallEdge(
                id=_stable_id("edge", call.id, call.caller_id, target),
                source=call.caller_id,
                target=target,
                order=call.order,
                call_text=call.call_text,
                line_text=call.line_text,
                language=call.language,
                file=call.file,
                range=call.range,
                kind=kind,
                confidence=confidence,
                provenance=provenance,
            ))
        return CodeStructureGraph(
            nodes=list(nodes_by_id.values()),
            edges=edges,
            diagnostics=diagnostics,
            meta={
                **meta,
                "symbol_count": len(symbols),
                "call_count": len(calls),
                "edge_count": len(edges),
            },
        )

    def _resolve_call(
        self,
        call: CallSite,
        symbols: list[Symbol],
        pack: LanguagePack | None,
        workspace_dir: str,
    ) -> tuple[str | None, str, float, list[str]]:
        for provider in self.providers.semantic:
            target = provider.resolve(call, symbols, workspace_dir)
            if target:
                return target, "static_resolved", 0.9, call.provenance + [provider.name]
        if pack and (call.name in pack.builtin_symbols or call.name.rsplit(".", 1)[-1] in pack.builtin_symbols):
            return None, "builtin_call", 0.8, call.provenance + ["language-pack:builtin"]
        if "." in call.name:
            return None, "external_member_call", 0.55, call.provenance + ["member-call"]
        return None, "unresolved", 0.35, call.provenance

    @staticmethod
    def _external_node_id(call: CallSite, kind: str) -> str:
        return _stable_id("node", kind, call.language, call.name)


def _stable_id(*parts: str) -> str:
    raw = "\0".join(parts)
    return parts[0] + ":" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
