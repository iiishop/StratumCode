from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .evidence import EvidenceRun
from .tools import CONTROL_TOOL_NAMES

DISCOVERY_TOOLS = ("glob", "grep", "read", "code_nav", "websearch", "webfetch", "subagent")
EVIDENCE_TOOLS = CONTROL_TOOL_NAMES


class EvidencePhase(StrEnum):
    SUPPORT = "support"
    OPPOSE = "oppose"
    AUDIT = "audit"
    EVALUATE = "evaluate"


PHASE_INSTRUCTIONS = {
    EvidencePhase.SUPPORT: (
        "Support target: look for evidence that would make the hypothesis true. "
        "If a tool result instead contradicts or narrows the hypothesis, record "
        "it with stance=oppose rather than discarding it."
    ),
    EvidencePhase.OPPOSE: (
        "Opposition target: actively search for evidence that contradicts, "
        "narrows, or reframes the hypothesis. For broad project-level claims, "
        "look for other major responsibilities or architecture that makes the "
        "claim too narrow. If a tool result supports the hypothesis, record it "
        "with stance=support rather than discarding it."
    ),
    EvidencePhase.AUDIT: (
        "Audit target: compare the evidence. Link corroborating, contradicting, "
        "or qualifying evidence. If the audit exposes a gap, use discovery tools "
        "again and record more evidence before concluding."
    ),
    EvidencePhase.EVALUATE: (
        "Evaluation target: conclude if the recorded supporting evidence, "
        "opposing evidence, and audit relations are sufficient. If a gap is "
        "still material, keep using discovery tools and record the missing "
        "evidence before concluding."
    ),
}

CHECKPOINT_INSTRUCTION = (
    "Evidence checkpoint: before more discovery, record the strongest "
    "material finding from a completed tool call. Use record_evidence "
    "with the tool_call_id included in tool results. If the completed "
    "tool calls truly contain no material finding, explain that briefly "
    "and continue discovery."
)


@dataclass
class EvidencePolicy:
    """Phase hints and checkpoints for one evidence-gathering run."""

    discovery_tools: tuple[str, ...] = DISCOVERY_TOOLS
    phase: EvidencePhase = EvidencePhase.SUPPORT
    max_rounds: int = 10
    max_read_lines: int = 80
    checkpoint_every: int = 2
    discovery_calls: int = 0
    read_calls: int = 0
    total_read_lines: int = 0
    calls_since_evidence: int = 0
    checkpoint_failures: int = 0
    force_checkpoint: bool = False
    phase_discovery_calls: int = 0

    def next_request(self, run: EvidenceRun) -> tuple[tuple[str, ...], str, str]:
        available_discovery = self.available_discovery_tools()
        has_support = any(item.stance == "support" for item in run.evidence.values())
        has_oppose = any(item.stance == "oppose" for item in run.evidence.values())
        if has_support and has_oppose and self.phase in {EvidencePhase.SUPPORT, EvidencePhase.OPPOSE}:
            self.phase = EvidencePhase.AUDIT
        elif has_support and self.phase == EvidencePhase.SUPPORT:
            self.phase = EvidencePhase.OPPOSE

        if self.checkpoint_due:
            return (
                self._working_tools(available_discovery),
                "required",
                CHECKPOINT_INSTRUCTION,
            )

        if instruction := PHASE_INSTRUCTIONS.get(self.phase):
            return (
                self._working_tools(available_discovery),
                "auto",
                instruction,
            )

        return (
            available_discovery + EVIDENCE_TOOLS,
            "auto",
            "",
        )

    def _working_tools(self, available_discovery: tuple[str, ...]) -> tuple[str, ...]:
        return available_discovery + EVIDENCE_TOOLS

    @property
    def checkpoint_due(self) -> bool:
        return self.force_checkpoint or self.calls_since_evidence >= self.checkpoint_every

    def available_discovery_tools(self) -> tuple[str, ...]:
        return self.discovery_tools

    def prepare_discovery(self, name: str, arguments: dict) -> dict:
        prepared = dict(arguments)
        if name == "read":
            start = max(1, int(prepared.get("start_line", 1)))
            requested_end = int(prepared.get("end_line") or (start + self.max_read_lines - 1))
            prepared["start_line"] = start
            prepared["end_line"] = max(start, requested_end)
        elif name == "grep":
            prepared["pattern"] = prepared.get("pattern") or ""
            if prepared.get("path") is None:
                prepared.pop("path", None)
            if prepared.get("include") is None:
                prepared.pop("include", None)
        elif name == "glob":
            prepared["pattern"] = prepared.get("pattern") or "**/*"
        elif name == "code_nav":
            prepared["operation"] = prepared.get("operation") or "symbols"
            prepared["path"] = prepared.get("path") or ""
        elif name == "websearch":
            prepared["query"] = prepared.get("query") or ""
        elif name == "subagent":
            prepared["agent"] = prepared.get("agent") or prepared.get("name") or "mcp-installer"
            prepared["task"] = prepared.get("task") or prepared.get("hint") or prepared.get("url") or ""
        return prepared

    def note_discovery(self, name: str, arguments: dict, *, useful: bool) -> None:
        self.discovery_calls += 1
        if name == "read":
            self.read_calls += 1
            self.total_read_lines += arguments["end_line"] - arguments["start_line"] + 1
        if useful:
            self.calls_since_evidence += 1
        self.phase_discovery_calls += 1

    def note_evidence(self, stance: str) -> None:
        self.calls_since_evidence = 0
        self.checkpoint_failures = 0
        self.force_checkpoint = False
        self.phase_discovery_calls = 0
        if self.phase == EvidencePhase.SUPPORT and stance == "support":
            self.phase = EvidencePhase.OPPOSE
        elif self.phase == EvidencePhase.OPPOSE and stance == "oppose":
            self.phase = EvidencePhase.AUDIT

    def note_audit(self) -> None:
        if self.phase == EvidencePhase.AUDIT:
            self.phase = EvidencePhase.EVALUATE

    def note_checkpoint_failure(self) -> None:
        self.checkpoint_failures += 1
        if self.checkpoint_failures >= 2:
            self.calls_since_evidence = 0
            self.force_checkpoint = False
            self.checkpoint_failures = 0
        else:
            self.force_checkpoint = True

    def request_checkpoint(self) -> None:
        self.force_checkpoint = True
