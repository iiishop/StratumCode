from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True, slots=True)
class DocParam:
    name: str
    type: str = ""
    description: str = ""
    default: str = ""


@dataclass(frozen=True, slots=True)
class DocReturn:
    type: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class FunctionDoc:
    summary: str = ""
    description: str = ""
    params: list[DocParam] = field(default_factory=list)
    returns: DocReturn = field(default_factory=DocReturn)
    raw: str = ""


@dataclass(frozen=True, slots=True)
class SourceRange:
    start_line: int
    start_col: int
    end_line: int
    end_col: int

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Symbol:
    id: str
    name: str
    signature: str
    language: str
    file: str
    range: SourceRange
    kind: str = "function"
    container: str = ""
    doc: FunctionDoc = field(default_factory=FunctionDoc)
    provenance: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        data = asdict(self)
        data["range"] = self.range.to_json()
        data["line"] = self.range.start_line
        return data


@dataclass(frozen=True, slots=True)
class CallSite:
    id: str
    caller_id: str
    name: str
    call_text: str
    line_text: str
    language: str
    file: str
    range: SourceRange
    order: int
    provenance: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CallEdge:
    id: str
    source: str
    target: str
    order: int
    call_text: str
    line_text: str
    language: str
    file: str
    range: SourceRange
    kind: str
    confidence: float
    provenance: list[str]

    def to_json(self) -> dict:
        data = asdict(self)
        data["range"] = self.range.to_json()
        data["line"] = self.range.start_line
        return data


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    label: str
    name: str
    signature: str
    language: str
    file: str
    line: int
    kind: str
    doc: FunctionDoc = field(default_factory=FunctionDoc)
    provenance: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Diagnostic:
    level: str
    message: str
    file: str = ""
    line: int = 0

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CodeStructureGraph:
    nodes: list[GraphNode]
    edges: list[CallEdge]
    diagnostics: list[Diagnostic]
    meta: dict

    def to_json(self) -> dict:
        return {
            "nodes": [node.to_json() for node in self.nodes],
            "edges": [edge.to_json() for edge in self.edges],
            "diagnostics": [item.to_json() for item in self.diagnostics],
            "meta": self.meta,
        }
