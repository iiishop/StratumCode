from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class MemorySnapshot:
    """Stage input supplied by an external memory boundary."""

    selected_context: dict = field(default_factory=dict)
    context_lines: list[str] = field(default_factory=list)
    previous_observations: list[dict] = field(default_factory=list)
    previous_knowledge: list[dict] = field(default_factory=list)
    previous_findings: dict | None = None


@dataclass(slots=True)
class MemoryWriteSet:
    """Stage output for an external memory boundary to persist or merge."""

    observations: list[dict] = field(default_factory=list)
    grounding_observations: list[dict] = field(default_factory=list)
    knowledge: list[dict] = field(default_factory=list)
    task_updates: list[dict] = field(default_factory=list)
    task_update_changes: list[dict] = field(default_factory=list)
    investigation_summary: dict | None = None


@dataclass(slots=True)
class InvestigationRunInput:
    request: str
    analysis: dict
    context: list[str]
    previous_observations: list[dict]
    previous_knowledge: list[dict]
    previous_findings: dict | None


@dataclass(slots=True)
class InvestigationStreamResult:
    investigation: dict | None = None
    pending_question: dict | None = None
    pending_output: dict | None = None
    memory_delta: MemoryWriteSet = field(default_factory=MemoryWriteSet)


@dataclass(slots=True)
class InvestigationTransitionDecision:
    next_state: object
    reason: str


class MemoryContextPort(Protocol):
    """External memory boundary used by status use cases."""

    def current_snapshot(self, run, stage: str) -> MemorySnapshot:
        """Return memory visible to a stage."""

    def record_stage_delta(self, run, stage: str, delta: MemoryWriteSet) -> None:
        """Record stage-produced memory without exposing persistence details."""
