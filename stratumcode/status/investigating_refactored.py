from __future__ import annotations

from .investigation_use_case import InvestigatingUseCase

SKILL_GUIDE = (
    "While investigating, the agent traces the codebase, verifies hypotheses, "
    "and records grounded evidence for unknowns. Best for: code investigation "
    "and evidence-gathering workflows, read/grep/git-search procedures, "
    "debugging and root-cause analysis playbooks, grounding and verification "
    "checklists."
)

_USE_CASE = InvestigatingUseCase()


def prepare_investigation(run):
    yield from _USE_CASE.prepare(run)


def handle(run):
    yield from _USE_CASE.run(run)
