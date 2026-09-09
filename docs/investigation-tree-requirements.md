# StratumCode Investigation Tree Refactor — Requirements

**Status:** Proposed implementation requirements  
**Companion document:** `investigation-tree-design.md`  
**Priority:** Architecture-level refactor

---

## 1. Purpose

This document defines the testable requirements for refactoring StratumCode Investigation from a TaskAnalysis-precomputed unknown checklist into a dynamic tree-based investigation system.

The implementation must distinguish between:

- **Requirement Investigation** — establish what the user actually needs.
- **Solution Investigation** — establish how the current repository/runtime can satisfy it.

The two domains must remain within one Investigation runtime and may recursively create unknowns in each other.

Investigation completion must be determined by a Root Audit, not by initial unknown coverage or a model-declared readiness flag.

---

## 2. Terminology

### 2.1 Task Contract

The normalized user/task information produced by TaskAnalysis, including intent, requirements, acceptance criteria, behavior contract, constraints, scope, hypotheses, clues, references, and investigation targets.

### 2.2 Unknown

A material unanswered question that may affect task correctness.

### 2.3 Requirement Unknown

An unknown with:

```python
domain == "requirement"
```

It concerns intended behavior, user decisions, success semantics, scope, or constraints.

### 2.4 Solution Unknown

An unknown with:

```python
domain == "solution"
```

It concerns repository facts, runtime facts, architecture, engineering decisions, implementation risks, or validation paths.

### 2.5 Root Unknown

An unknown with:

```python
parent_id is None
```

### 2.6 Child Unknown

An unknown whose `parent_id` references another unknown.

### 2.7 Open Unknown

An unknown with no closing resolution.

`partially_resolved` and `needs_clearify` remain open.

### 2.8 Closed Unknown

An unknown resolved with:

```text
resolved
deferred
```

subject to existing deferral policy.

### 2.9 Root Audit

A global post-traversal completeness check across the entire Task Contract, investigation tree, evidence, beliefs, and resolutions.

---

# 3. Functional requirements

## 3.1 TaskAnalysis

### TA-001 — TaskAnalysis shall not define the Investigation unknown universe

TaskAnalysis MUST NOT generate an exhaustive `unknowns` list intended to define Investigation completion.

### TA-002 — TaskAnalysis shall retain grounded task-contract extraction

TaskAnalysis MUST continue to produce, where applicable:

```text
intent
execution_mode
effort
risk
quality_gate
requirements
acceptance_criteria
behavior_contract
constraints
scope
hypotheses
clues
reference_baselines
investigation_targets
```

### TA-003 — TaskAnalysis shall preserve compatibility during migration

Until downstream callers no longer require the field:

```python
analysis["unknowns"] = []
```

MUST remain valid.

### TA-004 — `investigation_targets` shall be non-exhaustive

`investigation_targets` MUST be documented and prompted as orientation hints only.

Resolving or covering every `investigation_target` MUST NOT independently imply Investigation completion.

### TA-005 — TaskAnalysis shall not inspect the repository to invent implementation facts

TaskAnalysis MAY consume already-provided context, but MUST NOT replace Investigation as the evidence-acquisition stage.

---

## 3.2 Unknown schema

### UNK-001 — Existing unknown fields shall remain supported

The implementation MUST preserve:

```text
id
question
blocking
type
why
resolution_strategy
acceptance_criteria_ids
origin
reference_id
```

according to existing optionality/normalization rules.

### UNK-002 — Unknowns shall include `domain`

Every newly created unknown MUST contain:

```python
domain: "requirement" | "solution"
```

### UNK-003 — Existing `type` semantics shall not be replaced

The existing `type` values MUST remain supported:

```text
code_fact
doc_fact
runtime_fact
product_decision
engineering_decision
risk
```

`domain` and `type` MUST be treated as independent dimensions.

### UNK-004 — Unknowns shall include `parent_id`

Every newly created unknown MUST contain:

```python
parent_id: str | None
```

### UNK-005 — Root nodes shall use `parent_id=None`

Unknowns discovered at bootstrap or Root Audit without a specific parent dependency MUST use:

```python
parent_id = None
```

### UNK-006 — Child nodes shall reference their dependency parent

When an unknown is discovered because the current active unknown cannot be resolved without it, the new unknown MUST use:

```python
parent_id = active_unknown_id
```

### UNK-007 — The schema shall not persist `children`

The canonical Unknown contract MUST NOT require or persist a `children` array.

### UNK-008 — The schema shall not persist `depth`

The canonical Unknown contract MUST NOT require or persist tree depth.

### UNK-009 — The schema shall not persist node `status`

Unknown open/closed state MUST be derived from `resolutions`.

### UNK-010 — Parent references must be valid

A non-null `parent_id` MUST resolve to a known unknown before the tree is accepted as structurally valid.

### UNK-011 — Parent cycles must be rejected

The runtime MUST reject or repair any unknown structure containing a parent cycle.

### UNK-012 — Legacy unknowns shall be normalized

For an old unknown with no `domain`, the migration fallback MUST be deterministic.

Initial required fallback:

```python
domain = "requirement" if type == "product_decision" else "solution"
```

Missing `parent_id` MUST normalize to `None`.

---

## 3.3 Dynamic discovery

### DISC-001 — Investigation shall bootstrap root unknowns

When a new Investigation begins with no open tree, Investigation MUST create provisional root unknowns from the Task Contract.

### DISC-002 — Bootstrap unknowns shall be non-exhaustive

The bootstrap process MUST NOT claim that the generated root unknowns are a complete task decomposition.

### DISC-003 — Existing `new_unknowns` machinery shall be reused

Runtime-discovered unknowns MUST continue to flow through the current grounded-finding/new-unknown mechanism rather than a separate parallel store.

### DISC-004 — `new_unknowns` shall carry tree metadata

The `new_unknowns` recording contract MUST accept/require:

```text
domain
parent_id
```

in addition to the existing normalized unknown fields.

### DISC-005 — Investigation may create unknowns at any depth

A Requirement or Solution node MUST be allowed to create child unknowns while being investigated.

### DISC-006 — Cross-domain discovery shall be supported

All of the following MUST be legal:

```text
requirement -> requirement
requirement -> solution
solution -> requirement
solution -> solution
```

### DISC-007 — Root Audit shall be allowed to create root unknowns

Audit-generated gaps MUST be recorded as normal `new_unknowns` with:

```python
origin = "root_audit"
parent_id = None
```

unless the audit gap explicitly belongs under an existing node.

### DISC-008 — Duplicate semantic unknowns shall be avoided

Before inserting a new unknown, runtime MUST attempt to avoid duplicate/equivalent open unknowns.

---

## 3.4 Investigation domains

### DOM-001 — Requirement and Solution shall share one runtime

The implementation MUST NOT create separate serial ChatStates for Requirement and Solution investigation.

### DOM-002 — Active-node domain shall control investigation policy

The currently active unknown's `domain` MUST influence prompt/directive behavior.

### DOM-003 — Requirement Investigation shall prioritize task semantics

Requirement-domain logic MUST reason primarily about:

```text
user intent
behavior
scope
success semantics
constraints
product decisions
material ambiguity
```

### DOM-004 — Solution Investigation shall prioritize implementation evidence

Solution-domain logic MUST reason primarily about:

```text
repository facts
runtime facts
architecture
state ownership
integration points
engineering decisions
implementation risk
validation path
```

### DOM-005 — Solution constraints shall not silently rewrite requirements

If repository evidence conflicts with or constrains an assumed requirement, the system MUST create/resolve a Requirement unknown rather than silently changing the user contract.

### DOM-006 — Requirement ambiguity shall not always immediately trigger clarification

Before asking the user, Requirement Investigation SHOULD determine whether targeted repository/runtime evidence can eliminate or narrow the ambiguity.

### DOM-007 — Blocking product decisions shall use `clearify` when evidence cannot resolve them

A material Requirement unknown with:

```text
type=product_decision
blocking=True
```

MUST eventually route to `clearify` when authoritative evidence cannot determine the answer.

---

## 3.5 Traversal

### TRV-001 — Runtime shall maintain traversal state

`InvestigationState` MUST include transient traversal state containing at least:

```python
active_unknown_id: str
active_path: list[str]
audit_cycle: int
```

### TRV-002 — `active_path` shall represent root-to-active ancestry

If active node U5 has parent U4 and U4 has parent U1:

```python
active_path == ["U1", "U4", "U5"]
```

### TRV-003 — Open blocking children shall block full parent resolution

A parent unknown MUST NOT become fully resolved while it has an open blocking child.

### TRV-004 — Closed children shall permit backtracking

When the current child is closed, traversal MUST be able to return to its parent.

### TRV-005 — Traversal shall be DFS-like, not strictly DFS

The implementation MAY prioritize sibling nodes using information gain, risk, blocking status, or cost.

It MUST preserve dependency descent/backtracking semantics.

### TRV-006 — Open-state derivation shall use resolutions

The runtime MUST determine unknown openness from the existing resolution records.

Required behavior:

```text
no resolution       -> open
partially_resolved  -> open
needs_clearify      -> open
resolved            -> closed
deferred            -> closed subject to policy
```

### TRV-007 — Non-blocking unknowns shall not prevent semantic completion by themselves

Open non-blocking unknowns MAY remain at completion if Audit explicitly considers them non-material and policy permits them.

---

## 3.6 Tool calls and evidence

### TOOL-001 — Existing evidence tools shall remain usable

The refactor MUST preserve existing Investigation evidence tools and tool execution behavior unless independently required.

### TOOL-002 — Tool calls shall remain targeted to unknown IDs

Existing:

```python
target_unknown_ids
```

MUST continue to connect evidence-gathering calls to unknown nodes.

### TOOL-003 — Existing discovery metadata shall be retained

For non-clarification investigation tools, existing metadata SHOULD remain:

```text
reason
hypothesis
expected_observation
decision_impact
stop_condition
```

### TOOL-004 — Tool calls shall not imply automatic resolution

A successful tool call MUST produce evidence/observation, not automatically close an unknown.

### TOOL-005 — Evidence must continue to satisfy existing grounding requirements

The refactor MUST NOT weaken existing observation/belief/resolution evidence validation.

---

## 3.7 Resolution

### RES-001 — Existing Resolution contract shall remain authoritative

The refactor MUST preserve the existing resolution shape and statuses.

### RES-002 — Full resolution requires child closure

A node MUST NOT receive a final `resolved` state while any blocking child remains open.

### RES-003 — Resolution must answer the node question

A resolution MUST materially answer the corresponding Unknown's `question`.

### RES-004 — Evidence requirements shall remain enforced

`direct_fact` and `derived_inference` resolutions MUST continue to use appropriate observation/belief grounding.

### RES-005 — User decisions shall remain distinguishable

Clarification-derived answers MUST remain representable as:

```text
kind=user_decision
```

### RES-006 — Partial resolution shall not count as completion

`partially_resolved` MUST remain open for traversal and Audit.

---

## 3.8 Root Audit

### AUD-001 — A new AUDIT phase shall exist

`InvestigationPhase` MUST include:

```python
AUDIT = "audit"
```

### AUD-002 — Audit shall run before successful semantic finish

When no open blocking unknown remains, runtime MUST enter Audit before accepting Investigation completion.

### AUD-003 — Audit shall evaluate Requirement completeness

Audit MUST answer:

> Are the user's intended behavior, success semantics, scope, constraints, and material decisions sufficiently defined?

### AUD-004 — Audit shall evaluate Solution completeness

Audit MUST answer:

> Is the repository/runtime/implementation path sufficiently understood to proceed safely?

### AUD-005 — Audit shall consider all accumulated context

Audit MUST have access to:

```text
original request
Task Contract
all known unknowns
all resolutions
relevant observations
beliefs
acceptance criteria
scope/constraints
```

### AUD-006 — Audit shall use structured output

Audit MUST produce or be normalized into:

```python
{
    "requirement": {
        "complete": bool,
        "reason": str,
        "open_unknown_ids": list[str],
    },
    "solution": {
        "complete": bool,
        "reason": str,
        "open_unknown_ids": list[str],
    },
    "complete": bool,
}
```

### AUD-007 — Runtime shall compute global `complete`

The model MUST NOT be the sole authority for global completion.

Runtime MUST compute:

```python
complete = (
    requirement_complete
    and solution_complete
    and not open_blocking_unknowns
)
```

### AUD-008 — Incomplete Audit shall create or identify real unknowns

A domain reported incomplete MUST correspond to:

- one or more already-open blocking unknowns, or
- one or more newly created blocking unknowns in that domain.

Free-text audit concerns without a represented Unknown MUST NOT be sufficient.

### AUD-009 — Audit-generated gaps shall re-enter normal traversal

After Audit creates gaps, Investigation MUST resume DISCOVER/RESOLVE traversal rather than use a separate remediation subsystem.

### AUD-010 — Audit may run multiple cycles

The system MUST support:

```text
investigate -> audit -> investigate -> audit
```

until complete or budget exhaustion.

---

## 3.9 Readiness and finish

### FIN-001 — `ready_for_patch_planning` shall be runtime-derived

For implementation tasks:

```python
ready_for_patch_planning = (
    audit_result["complete"]
    and not open_blocking_unknowns
)
```

### FIN-002 — Model-declared readiness shall not override runtime

A model-provided `ready_for_patch_planning=True` MUST NOT bypass structural/audit gates.

### FIN-003 — Patch planning finish requires complete Audit

`finish_investigation` with:

```text
recommended_next_step=patch_planning
```

MUST fail/reject if Audit is incomplete.

### FIN-004 — Open blocking unknowns shall prevent patch planning

Even if Audit output claims completeness, any open blocking unknown MUST force:

```python
ready_for_patch_planning = False
```

### FIN-005 — FINISH shall package conclusions, not decide completeness

Semantic completeness MUST be decided by Audit/runtime before FINISH.

### FIN-006 — Read-only tasks shall also respect Audit

Read-only Investigation MUST not claim fully complete findings before Audit unless an explicit evidence-gap policy is used.

---

# 4. State-machine requirements

## SM-001 — Existing `INVESTIGATING` ChatState shall remain

Requirement/Solution domains MUST NOT require new top-level chat states.

## SM-002 — Investigation phases shall include AUDIT

The internal phase set MUST support Audit alongside existing discovery/resolve/repair/finish phases.

## SM-003 — No-blocker state shall route to AUDIT

A state with no open blocking unknown MUST route to AUDIT unless a valid current Audit already covers the unchanged tree state.

## SM-004 — Audit gaps shall route back to Investigation

If Audit creates or identifies blocking gaps:

```text
AUDIT -> DISCOVER/RESOLVE
```

## SM-005 — Complete Audit shall permit finish

If Audit is complete:

```text
AUDIT -> FINISH
```

## SM-006 — Implement completion shall route to DESIGNING

Only runtime-derived readiness may cause:

```text
INVESTIGATING -> DESIGNING
```

---

# 5. Compatibility and migration requirements

## MIG-001 — Existing persisted runs shall remain readable

Runs containing legacy unknowns without `domain` or `parent_id` MUST normalize successfully.

## MIG-002 — Existing `type` values shall remain valid

No migration shall reinterpret `type` as Requirement/Solution.

## MIG-003 — Existing resolutions shall remain valid

Historical resolution records MUST remain readable without tree-specific additions.

## MIG-004 — Existing observations and beliefs shall remain valid

No evidence migration should be required.

## MIG-005 — Existing `new_unknowns` shall receive compatibility defaults

Old runtime records missing new tree fields MUST be normalized deterministically.

## MIG-006 — TaskAnalysis `unknowns` removal shall be staged

The field MAY remain present and empty until all downstream assumptions are removed.

## MIG-007 — Initial unknown coverage shall cease to be a completion proof

Helpers that test whether initial TaskAnalysis unknown IDs are covered MUST NOT be used as a semantic substitute for Root Audit in the final architecture.

---

# 6. Safety and loop-control requirements

## SAFE-001 — Tree cycles shall be prevented

Parent relationships MUST be cycle-safe.

## SAFE-002 — Duplicate no-progress protections shall remain

Current duplicate-tool/no-progress protections MUST continue functioning.

## SAFE-003 — Audit loops shall have a budget

Runtime MUST have a configurable maximum Audit cycle budget or equivalent investigation budget.

## SAFE-004 — Budget exhaustion shall not imply readiness

If budget is exhausted with incomplete Audit:

```python
ready_for_patch_planning = False
```

## SAFE-005 — Budget exhaustion shall preserve unresolved gaps

The resulting investigation/failure/evidence-gap output MUST retain the open unknown IDs and audit reasons.

## SAFE-006 — Pass count shall not define completeness

A fixed number of passes MUST NOT be treated as proof that investigation is complete.

---

# 7. Non-functional requirements

## NFR-001 — Single source of truth

Unknown closure MUST have one authoritative source: `resolutions`.

## NFR-002 — Minimal schema duplication

Persistent tree metadata MUST be limited to what cannot be derived cheaply/correctly.

## NFR-003 — Deterministic structural gates

The following MUST be runtime-deterministic:

```text
parent validity
cycle detection
open blocking unknown set
audit global complete
ready_for_patch_planning
finish eligibility
```

## NFR-004 — Evidence traceability

It MUST remain possible to trace:

```text
Unknown
 -> targeted tool call
 -> Observation
 -> Belief/Resolution
 -> Audit
```

## NFR-005 — Resume support

A resumed investigation MUST be able to reconstruct the tree from known unknowns and `parent_id`.

## NFR-006 — Domain visibility

Debug/event output SHOULD make `domain`, `parent_id`, active node, and active path observable.

## NFR-007 — No unnecessary repository-wide rediscovery

Tree traversal and existing evidence caches SHOULD reduce repeated broad discovery calls.

---

# 8. Required helper behavior

The implementation SHOULD provide clear helpers equivalent to:

```python
all_unknowns(investigation, analysis) -> list[dict]

unknown_by_id(...) -> dict | None

children_of(parent_id, ...) -> list[dict]

resolution_for(unknown_id, ...) -> dict | None

is_unknown_open(unknown_id, ...) -> bool

open_unknowns(...) -> list[dict]

open_blocking_unknowns(...) -> list[dict]

open_blocking_children(parent_id, ...) -> list[dict]

path_to_root(unknown_id, ...) -> list[str]

validate_tree(...) -> None

unknowns_by_domain(domain, ...) -> list[dict]
```

Names may differ, but behavior MUST be centralized enough to prevent duplicated inconsistent logic.

---

# 9. Required prompt behavior

## PROMPT-001 — TaskAnalyzer

TaskAnalyzer MUST be told not to generate the investigation unknown universe.

## PROMPT-002 — Bootstrap discovery

Investigation MUST be told to create only currently justified provisional root unknowns.

## PROMPT-003 — Requirement-domain prompt

Requirement mode MUST explicitly distinguish "what is required" from "what is convenient to implement".

## PROMPT-004 — Solution-domain prompt

Solution mode MUST explicitly avoid changing user requirements merely because current architecture is limited.

## PROMPT-005 — Audit prompt

Audit MUST search for material counterexamples/gaps against both Requirement and Solution completeness.

## PROMPT-006 — Audit gap discipline

Audit MUST express material gaps as structured unknowns, not only prose.

---

# 10. Acceptance criteria

The refactor is accepted only when all of the following are demonstrated.

### AC-001 — TaskAnalysis produces no initial unknown universe

Given a normal feature request, TaskAnalysis completes with the existing Task Contract but no generated investigation checklist.

### AC-002 — Investigation creates root unknowns dynamically

A new feature task causes Investigation to create provisional root unknowns.

### AC-003 — Requirement and Solution domains coexist

A single Investigation run contains both:

```text
domain=requirement
domain=solution
```

### AC-004 — Cross-domain child creation works

At least one integration test demonstrates:

```text
Requirement -> Solution -> Requirement
```

### AC-005 — Tool evidence targets tree nodes

Tool observations preserve the correct `target_unknown_ids`.

### AC-006 — Parent resolution waits for blocking children

A test must prove that a parent cannot fully close while a blocking child is open.

### AC-007 — Resolution remains the only closure source

No test or runtime code needs a persisted `unknown.status`.

### AC-008 — Tree exhaustion enters Audit

No-open-blocker state transitions to Audit rather than directly to successful finish.

### AC-009 — Audit can reopen investigation

An Audit-discovered Requirement or Solution gap creates a normal new unknown and resumes investigation.

### AC-010 — Second-order Audit discovery works

A clarified Requirement discovered by Audit may lead to a new Solution unknown before final completion.

### AC-011 — Model cannot force readiness

A model output claiming readiness while Audit is incomplete does not enter Design.

### AC-012 — Runtime readiness works

Complete Requirement Audit + complete Solution Audit + zero open blocking unknowns produces runtime-derived readiness for implementation tasks.

### AC-013 — Finish gate works

Patch-planning finish is rejected before Audit completion and accepted after valid completion.

### AC-014 — Legacy run migration works

A stored investigation with old unknown schema resumes successfully.

### AC-015 — Existing evidence validation still passes regression suite

No weakening of observation/belief/resolution grounding is introduced.

---

# 11. Example required behavior

Given:

> Add a note module with todos, calendar dots, due reminders, and hover-expanded notes.

The system SHOULD be capable of producing a tree equivalent to:

```text
ROOT
├── U1 [requirement] reminder semantics
│   ├── U4 [solution] existing reminder primitives
│   │   └── U5 [requirement] missed reminder behavior
│   └── U6 [requirement] exact due-time semantics
├── U2 [solution] module hover/expanded architecture
├── U3 [solution] calendar integration
│   └── U7 [solution] shared per-date metadata
├── U8 [solution] persistence
├── U9 [solution] audio
├── U10 [solution] flashing animation
└── U11 [solution] validation path
```

After those nodes close, Root Audit MAY identify:

```text
U12 [requirement] simultaneous reminder ordering
```

and after that requirement is clarified, a later Audit MAY identify:

```text
U13 [solution] simultaneous reminder queue implementation
```

Investigation MUST not finish before both are resolved and a final Audit passes.

---

# 12. Out of scope for this implementation

The following are explicitly not required for initial delivery:

1. Frontend tree visualization.
2. Persisted `children` lists.
3. Graph traversal beyond parent-child tree relationships.
4. Automatic optimal information-gain scheduling.
5. Cross-run global knowledge graph construction.
6. Replacing existing belief/evidence semantics.
7. Redesigning Design/Plan/Patch.
8. Renaming all legacy "unknown" helpers in the first patch.
9. Removing `analysis["unknowns"]` immediately if compatibility requires it.
10. Reworking all effort-profile budgets beyond what Audit safety requires.

---

# 13. Definition of Done

Implementation is done when:

- TaskAnalysis no longer owns the unknown universe.
- Unknowns support `domain` and `parent_id`.
- Existing `type` semantics remain intact.
- Dynamic `new_unknowns` builds the investigation tree.
- Requirement and Solution investigation use one runtime with domain-specific policy.
- Traversal can descend and backtrack across domains.
- Existing tool calls continue to generate targeted observations.
- Existing resolutions remain the sole node-closure state.
- Root Audit evaluates Requirement and Solution completeness.
- Audit gaps become normal unknowns and reopen traversal.
- `ready_for_patch_planning` is runtime-derived.
- `finish_investigation` cannot bypass Audit.
- Legacy unknowns remain readable.
- Regression tests preserve existing grounding/repair/clarification behavior.
- The note-module scenario (or an equivalent fixture) demonstrates multi-level cross-domain discovery followed by Audit-driven completion.
