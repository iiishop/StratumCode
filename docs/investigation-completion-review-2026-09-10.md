# Completion boundary review

Source: export 2026-09-10T05:39:18.750Z, AuraGit commit-graph review.

## Observed failures

- Events 347 and 416: root audit returned resolution_quality_incomplete and
  downgraded accepted U8/U9 and other nodes. Its additional prose-path checks
  complained about src/main.js, GitPath.vue/src/main.js and store/modules/cache.js.
  These were a different acceptance policy from the node submission path.
- Event 451: the third root audit was refused because the lifetime count was two,
  even though intervening investigation had supplied new resolutions.
- Final Light input: 228,562 tokens. Delivery then threw because the model supplied
  complete together with unresolved_questions. The rejected finish arguments were
  not retained in this old export, so their exact contents cannot be reconstructed.
- Event 158 also used invented descriptive observation IDs. Its repair instruction
  contradicted the actual call_-prefixed observation IDs by saying not to use tool
  call IDs.

## Ownership changes

Changed resolutions now pass reference/evidence/closure checks before commit.
The existing semantic auditor, when enabled, runs only for changed resolved nodes
at that boundary. Accepted siblings are not subjected to a second global evidence
audit. No new audit model was added and acceptance was not simply removed.

Root audit owns coverage and cross-question consistency. New gaps become new root
questions with optional related_unknown_ids, not new dependencies under completed
questions. Truly contradictory observations can still explicitly invalidate an old
conclusion; reopening requires real observation IDs not cited by that conclusion.
This checks provenance, not the semantic truth of the contradiction by itself.

The audit counter now limits repeated attempts at an unchanged content signature.
Lifetime audit_cycle remains diagnostic. Ordinary progress is not a failed retry;
unchanged loops remain bounded, as do the investigator's existing overall limits.

Final packaging no longer guesses mandatory file reads or launches LSP searches
from free-form answer prose. Structured observation and belief references remain
validated. Evidence sufficiency is evaluated at node acceptance.

Light owns answer text and its stated gaps, while the coordinator owns completion
status based on those gaps and actual delegation/implementation outcomes. Useful
partial answers are delivered with explicit limitations instead of being discarded.
This is a contract change, not an exception fallback or a forced success.

## Context optimization

Read-only handoff keeps full resolution answers, additional findings, evidence
locations, follow-ups and failure information. Raw observations and duplicate task
updates remain in the event log but are not replayed to Light. Write handoff remains
unchanged. The task-status message no longer duplicates resolution answer history.

Offline projection of this log's investigation response: 391,304 to 71,040
characters, an 81.8% reduction. This measures that handoff only, not total task
tokens or future quality. No live model rerun was performed.

Focused offline checks cover partial delivery, new root gaps preserving accepted
subtrees, commit-time missing evidence, targeted semantic acceptance, per-state
audit attempts, and read-only handoff content preservation.
