from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ObservationState:
    items: list[dict] = field(default_factory=list)
    pending_ids: list[str] = field(default_factory=list)


@dataclass
class CacheState:
    tool: dict = field(default_factory=dict)
    tool_observation_ids: dict[str, str] = field(default_factory=dict)
    audit: dict = field(default_factory=dict)
    read_file: dict = field(default_factory=dict)
    failed_tool: dict = field(default_factory=dict)


@dataclass
class ProgressState:
    repeated_tool_error_name: str = ""
    repeated_tool_error_count: int = 0
    already_resolved_error_count: int = 0
    repeated_record_no_progress: int = 0
    duplicate_no_progress_signature: str = ""
    duplicate_no_progress_count: int = 0
    duplicate_no_progress_total: int = 0


@dataclass
class FindingsState:
    recorded: dict = field(default_factory=dict)
    last_record_signature: str = ""
    last_quality_audit: dict = field(default_factory=dict)


@dataclass
class VerificationState:
    queue: list[dict] = field(default_factory=list)
    attempted: set = field(default_factory=set)
    clearify_questions: dict[str, str] = field(default_factory=dict)


@dataclass
class ControlState:
    stop_investigation: bool = False
    finalization_reason: str = ""
    current_tool_choice: object = None
    current_tools: list[dict] = field(default_factory=list)
    finish_evidence_blocked: bool = False
    force_synthesis_reason: str = ""
    force_discovery_ids: list[str] = field(default_factory=list)
    final: dict | None = None


@dataclass
class UsageState:
    total: dict = field(default_factory=dict)


@dataclass(frozen=True)
class InvestigationRuntime:
    """Immutable runtime environment for one investigation lifecycle."""

    provider: dict
    model: str
    pricing_rules: dict | None
    run_id: str
    stage_id: str
    tools: list[dict]
    analysis: dict
    context: list[str]
    workspace_dir: str
    max_rounds: int
    min_rounds: int
    effort_profile: dict
    quality_gate: str
    rounds_per_unknown: int
    semantic_gate_enabled: bool
    subagent_enabled: bool
    preserve_grounding_evidence: bool
    previous_observations: list[dict] | None
    previous_knowledge: list[dict] | None
    clearify_runtime: object


@dataclass
class InvestigationState:
    messages: list[dict] = field(default_factory=list)
    observations: ObservationState = field(default_factory=ObservationState)
    caches: CacheState = field(default_factory=CacheState)
    progress: ProgressState = field(default_factory=ProgressState)
    findings: FindingsState = field(default_factory=FindingsState)
    verification: VerificationState = field(default_factory=VerificationState)
    control: ControlState = field(default_factory=ControlState)
    usage: UsageState = field(default_factory=UsageState)
