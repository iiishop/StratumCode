from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum


class RunState(StrEnum):
    INITIALIZING = "initializing"
    GATHERING = "gathering"
    EVALUATING = "evaluating"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


_TRANSITIONS = {
    RunState.INITIALIZING: {RunState.GATHERING},
    RunState.GATHERING: {RunState.EVALUATING},
    RunState.EVALUATING: {
        RunState.SUPPORTED,
        RunState.REFUTED,
        RunState.INCONCLUSIVE,
    },
}

_SOURCE_RELIABILITY = {
    "runtime": 1.0,
    "code": 0.92,
    "document": 0.76,
    "web": 0.62,
}


@dataclass(slots=True)
class Evidence:
    id: str
    claim: str
    stance: str
    strength: float
    source_type: str
    source_uri: str
    excerpt: str
    tool_call_id: str = ""
    observation_id: str = ""


@dataclass(slots=True)
class EvidenceRelation:
    source_id: str
    target_id: str
    relation: str
    impact: float
    explanation: str


@dataclass(slots=True)
class Observation:
    id: str
    tool: str
    title: str
    summary: str


@dataclass(slots=True)
class Belief:
    id: str
    statement: str
    status: str
    evidence_ids: list[str] = field(default_factory=list)
    observation_ids: list[str] = field(default_factory=list)
    resolves_unknown_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Unknown:
    id: str
    question: str
    blocking: bool
    resolution_strategy: str
    resolved_by_evidence_ids: list[str] = field(default_factory=list)
    resolved_by_belief_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StepResult:
    next_step: str
    continue_reason: str
    target_unknown_ids: list[str] = field(default_factory=list)


@dataclass
class EvidenceRun:
    hypothesis: str
    state: RunState = RunState.INITIALIZING
    evidence: dict[str, Evidence] = field(default_factory=dict)
    relations: list[EvidenceRelation] = field(default_factory=list)
    observations: dict[str, Observation] = field(default_factory=dict)
    beliefs: dict[str, Belief] = field(default_factory=dict)
    unknowns: dict[str, Unknown] = field(default_factory=dict)
    step_results: list[StepResult] = field(default_factory=list)
    summary: str = ""

    def transition(self, next_state: RunState) -> None:
        if next_state not in _TRANSITIONS.get(self.state, set()):
            raise ValueError(f"invalid state transition: {self.state} -> {next_state}")
        self.state = next_state

    def add_evidence(
        self,
        *,
        evidence_id: str,
        claim: str,
        stance: str,
        strength: float,
        source_type: str,
        source_uri: str,
        excerpt: str,
        tool_call_id: str = "",
        observation_id: str = "",
    ) -> Evidence:
        if self.state != RunState.GATHERING:
            raise ValueError(f"cannot add evidence in state {self.state}")
        if evidence_id in self.evidence:
            raise ValueError(f"duplicate evidence id: {evidence_id}")
        if any(
            item.source_uri == source_uri.strip() and item.excerpt == excerpt.strip()
            for item in self.evidence.values()
        ):
            raise ValueError("the same source excerpt cannot be recorded twice")
        if stance not in {"support", "oppose"}:
            raise ValueError("stance must be support or oppose")
        if source_type not in _SOURCE_RELIABILITY:
            raise ValueError("source_type must be runtime, code, document, or web")
        item = Evidence(
            id=evidence_id,
            claim=claim.strip(),
            stance=stance,
            strength=_unit(strength),
            source_type=source_type,
            source_uri=source_uri.strip(),
            excerpt=excerpt.strip(),
            tool_call_id=tool_call_id,
            observation_id=observation_id or tool_call_id,
        )
        if not item.claim or not item.source_uri or not item.excerpt:
            raise ValueError("claim, source_uri, and excerpt are required")
        self.evidence[item.id] = item
        return item

    def add_observation(self, *, observation_id: str, tool: str, title: str, summary: str) -> Observation:
        item = Observation(
            id=observation_id,
            tool=tool.strip(),
            title=title.strip(),
            summary=summary.strip(),
        )
        self.observations[item.id] = item
        return item

    def upsert_unknown(
        self,
        *,
        unknown_id: str,
        question: str,
        blocking: bool,
        resolution_strategy: str,
        resolved_by_evidence_ids: list[str] | None = None,
        resolved_by_belief_ids: list[str] | None = None,
    ) -> Unknown:
        if resolution_strategy not in {"investigate_project", "ask_user", "deferred"}:
            raise ValueError("resolution_strategy must be investigate_project, ask_user, or deferred")
        item = Unknown(
            id=unknown_id,
            question=question.strip(),
            blocking=bool(blocking),
            resolution_strategy=resolution_strategy,
            resolved_by_evidence_ids=list(resolved_by_evidence_ids or []),
            resolved_by_belief_ids=list(resolved_by_belief_ids or []),
        )
        if not item.id or not item.question:
            raise ValueError("unknown id and question are required")
        self.unknowns[item.id] = item
        return item

    def upsert_belief(
        self,
        *,
        belief_id: str,
        statement: str,
        status: str,
        evidence_ids: list[str] | None = None,
        observation_ids: list[str] | None = None,
        resolves_unknown_ids: list[str] | None = None,
    ) -> Belief:
        if status not in {
            "unverified",
            "plausible",
            "supported",
            "strongly_supported",
            "runtime_confirmed",
            "contradicted",
            "invalidated",
        }:
            raise ValueError("invalid belief status")
        item = Belief(
            id=belief_id,
            statement=statement.strip(),
            status=status,
            evidence_ids=list(evidence_ids or []),
            observation_ids=list(observation_ids or []),
            resolves_unknown_ids=list(resolves_unknown_ids or []),
        )
        if not item.id or not item.statement:
            raise ValueError("belief id and statement are required")
        self.beliefs[item.id] = item
        return item

    def report_step(
        self,
        *,
        next_step: str,
        continue_reason: str,
        target_unknown_ids: list[str] | None = None,
    ) -> StepResult:
        if next_step not in {"continue_investigation", "ask_user", "write_code", "done", "failed"}:
            raise ValueError("next_step must be continue_investigation, ask_user, write_code, done, or failed")
        targets = list(target_unknown_ids or [])
        if next_step == "continue_investigation" and not targets:
            raise ValueError("continue_investigation requires target_unknown_ids")
        if next_step != "continue_investigation" and targets:
            raise ValueError("target_unknown_ids are only allowed when continuing investigation")
        blockers = [
            item.id for item in self.unknowns.values()
            if item.blocking
            and item.resolution_strategy == "investigate_project"
            and not item.resolved_by_evidence_ids
            and not item.resolved_by_belief_ids
        ]
        if next_step == "write_code" and blockers:
            raise ValueError(
                "write_code is blocked by unresolved investigate_project unknowns: "
                + ", ".join(blockers)
            )
        if not continue_reason.strip():
            raise ValueError("continue_reason is required")
        item = StepResult(next_step=next_step, continue_reason=continue_reason.strip(), target_unknown_ids=targets)
        self.step_results.append(item)
        return item

    def link_evidence(
        self,
        *,
        source_id: str,
        target_id: str,
        relation: str,
        impact: float,
        explanation: str,
    ) -> EvidenceRelation:
        if source_id not in self.evidence or target_id not in self.evidence:
            raise ValueError("both evidence ids must exist before linking")
        if source_id == target_id:
            raise ValueError("evidence cannot link to itself")
        if relation not in {"corroborates", "contradicts", "qualifies", "unrelated"}:
            raise ValueError("relation must be corroborates, contradicts, qualifies, or unrelated")
        edge = EvidenceRelation(
            source_id=source_id,
            target_id=target_id,
            relation=relation,
            impact=_unit(impact),
            explanation=explanation.strip(),
        )
        self.relations.append(edge)
        return edge

    @property
    def confidence(self) -> float:
        modifiers = {evidence_id: 1.0 for evidence_id in self.evidence}
        for edge in self.relations:
            source = self.evidence[edge.source_id]
            source_weight = source.strength * _SOURCE_RELIABILITY[source.source_type]
            influence = edge.impact * source_weight
            if edge.relation == "corroborates":
                modifiers[edge.target_id] += influence * 0.35
            elif edge.relation == "contradicts":
                modifiers[edge.target_id] -= influence * 0.75
            elif edge.relation == "qualifies":
                modifiers[edge.target_id] -= influence * 0.35

        log_odds = 0.0
        for item in self.evidence.values():
            direction = 1 if item.stance == "support" else -1
            reliability = _SOURCE_RELIABILITY[item.source_type]
            modifier = min(1.5, max(0.0, modifiers[item.id]))
            log_odds += direction * item.strength * reliability * modifier * 2.5
        confidence = 1 / (1 + math.exp(-log_odds))
        if _is_absolute_claim(self.hypothesis):
            strongest_counterexample = max(
                (
                    item.strength * _SOURCE_RELIABILITY[item.source_type]
                    for item in self.evidence.values()
                    if item.stance == "oppose"
                ),
                default=0.0,
            )
            if strongest_counterexample >= 0.45:
                confidence = min(confidence, 0.24)
        return round(confidence, 4)

    def conclude(self, summary: str) -> RunState:
        if self.state == RunState.GATHERING:
            self.transition(RunState.EVALUATING)
        if self.state != RunState.EVALUATING:
            raise ValueError(f"cannot conclude from state {self.state}")
        self.summary = summary.strip()
        confidence = self.confidence
        if confidence >= 0.72:
            verdict = RunState.SUPPORTED
        elif confidence <= 0.28:
            verdict = RunState.REFUTED
        else:
            verdict = RunState.INCONCLUSIVE
        self.transition(verdict)
        return verdict

    def snapshot(self) -> dict:
        return {
            "hypothesis": self.hypothesis,
            "state": self.state.value,
            "confidence": self.confidence,
            "evidence": [asdict(item) for item in self.evidence.values()],
            "relations": [asdict(edge) for edge in self.relations],
            "observations": [asdict(item) for item in self.observations.values()],
            "beliefs": [asdict(item) for item in self.beliefs.values()],
            "unknowns": [asdict(item) for item in self.unknowns.values()],
            "step_results": [asdict(item) for item in self.step_results],
            "summary": self.summary,
        }


def _unit(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _is_absolute_claim(value: str) -> bool:
    lowered = value.casefold()
    return any(
        token in lowered
        for token in (
            "必须",
            "只能",
            "一定",
            "所有",
            "全部",
            "always",
            "only",
            "must",
            "requires",
            "required",
            "cannot",
            "never",
        )
    )
