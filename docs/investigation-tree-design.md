# StratumCode Investigation Tree Refactor — Design Document

**Status:** Proposed  
**Repository baseline:** `iiishop/StratumCode`, current `main` architecture discussed on 2026-09-08  
**Primary area:** `TaskAnalysis -> Investigation -> Design/Plan`  
**Scope:** Replace TaskAnalysis-defined unknown universe with dynamic tree-based Investigation, split investigation semantics into Requirement and Solution domains, and move investigation termination to Root Audit.

---

## 1. Background

StratumCode currently asks `TaskAnalysis` to generate an initial `unknowns` list before Investigation has acquired meaningful repository/runtime evidence.

The current flow is approximately:

```text
User Request
    |
    v
TaskAnalysis
    |- intent
    |- requirements
    |- acceptance_criteria
    |- behavior_contract
    |- constraints
    |- scope
    |- hypotheses
    |- clues
    |- reference_baselines
    |- investigation_targets
    `- unknowns
          |
          v
Investigation
    |- observations
    |- beliefs
    |- new_unknowns
    |- resolutions
    `- finish_investigation
          |
          v
Design / Finish
```

This creates a structural mismatch:

1. `TaskAnalysis` is the stage with the least repository/runtime evidence.
2. It nevertheless defines a large part of the investigation completion space.
3. Investigation can discover `new_unknowns`, but completion logic is still strongly shaped by the initial contract unknown set and `ready_for_patch_planning`.
4. "All currently known blockers are resolved" is not equivalent to "the task has been investigated completely".
5. Requirement uncertainty ("what does the user actually want?") and solution uncertainty ("how can this repository correctly implement it?") are currently mixed under the same unknown model without an explicit semantic axis.

The refactor changes the meaning of Investigation:

> Investigation is not the execution of a precomputed checklist. It is a dynamic search over Requirement Space and Solution Space until a Root Audit can no longer identify a material blocking gap.

---

## 2. Goals

### 2.1 Primary goals

1. Remove ownership of the unknown universe from `TaskAnalysis`.
2. Preserve the current Task Contract fields that are grounded in the user request.
3. Reuse the existing dynamic `new_unknowns`, observation, belief, resolution, and tool-targeting machinery.
4. Add an explicit semantic distinction between:
   - **Requirement Investigation**: determine what behavior/result the user actually requires.
   - **Solution Investigation**: determine how the current system/repository can satisfy the requirement.
5. Represent unknown dependencies as a tree using the smallest persistent schema change.
6. Use DFS-like dependency traversal and backtracking.
7. Add a Root Audit that decides investigation completeness.
8. Make `ready_for_patch_planning` a runtime-derived fact, not a model-declared completion signal.
9. Preserve existing evidence grounding and resolution validation.
10. Keep Requirement and Solution investigation inside one Investigation runtime; do not create two serial state-machine stages.

### 2.2 Secondary goals

- Reduce premature clarification questions by allowing Requirement nodes to spawn Solution investigations first.
- Allow repository discoveries to create new Requirement decisions.
- Make investigation completion explainable through a tree and audit record.
- Improve resumability because each discovered unknown has a parent and semantic domain.
- Minimize disruption to existing tool schemas and resolution contracts.

---

## 3. Non-goals

This refactor does **not**:

1. Redesign the Design, Plan, Patch, or Validation stages.
2. Replace the existing unknown `type` taxonomy.
3. Replace `resolutions` with node-local status.
4. Introduce a general graph database.
5. Persist `children`, `depth`, or duplicated node status.
6. Require strict textbook DFS ordering.
7. Guarantee that every ambiguity is sent to the user.
8. Remove existing evidence-quality gates.
9. Make TaskAnalysis perform repository discovery.
10. Introduce a separate `REQUIREMENT_INVESTIGATING` and `SOLUTION_INVESTIGATING` ChatState.

---

## 4. Current-state baseline

The design intentionally builds on the current implementation rather than replacing it.

### 4.1 Current TaskAnalysis contract

Current task analysis includes the following logical fields:

```python
{
    "intent": {
        "type": ...,
        "summary": ...,
    },
    "execution_mode": ...,
    "effort": ...,
    "risk": ...,
    "quality_gate": ...,

    "requirements": [...],
    "acceptance_criteria": [...],
    "behavior_contract": {...},
    "constraints": [...],
    "scope": {...},

    "hypotheses": [...],
    "clues": [...],
    "reference_baselines": [...],
    "investigation_targets": [...],

    "unknowns": [...],
}
```

Relevant files:

- `stratumcode/status/task_analysis.py`
- `stratumcode/status/task_contract.py`
- `stratumcode/status/investigation_context.py`

### 4.2 Current Unknown contract

The current normalized unknown shape is:

```python
{
    "id": str,
    "question": str,
    "blocking": bool,
    "type": (
        "code_fact"
        | "doc_fact"
        | "runtime_fact"
        | "product_decision"
        | "engineering_decision"
        | "risk"
    ),
    "why": str,
    "resolution_strategy": (
        "investigate_project"
        | "clearify"
        | "deferred"
    ),
    "acceptance_criteria_ids": list[str],

    # optional
    "origin": str,
    "reference_id": str,
}
```

Important: `type` describes the **knowledge/decision kind**, not the Requirement/Solution investigation domain.

### 4.3 Existing dynamic discovery

Investigation already supports runtime-discovered unknowns through:

```text
recorded_findings["new_unknowns"]
```

and merges them with initial unknowns for resolution/completion checks.

Therefore the refactor should reuse `new_unknowns` rather than create a second dynamic-unknown mechanism.

Relevant files:

- `stratumcode/investigator/findings.py`
- `stratumcode/investigator/domain.py`
- `stratumcode/investigator/finalize.py`
- `stratumcode/investigator/__init__.py`

### 4.4 Existing Resolution contract

Existing resolution records already provide the correct source of truth for unknown completion:

```python
{
    "unknown_id": str,
    "status": (
        "resolved"
        | "partially_resolved"
        | "needs_clearify"
        | "deferred"
    ),
    "kind": (
        "direct_fact"
        | "derived_inference"
        | "user_decision"
        | "deferred"
    ),
    "answer": str,
    "observation_ids": list[str],
    "belief_ids": list[str],
    "reason": str,
}
```

This design keeps `resolutions` authoritative.

### 4.5 Existing tool-call investigation contract

Non-clarification Investigation tools already carry:

```python
target_unknown_ids
reason
hypothesis
expected_observation
decision_impact
stop_condition
```

This is sufficient to associate evidence-gathering actions with tree nodes.

No new InvestigationAction persistence model is required.

### 4.6 Existing Investigation runtime

Current `InvestigationState` contains:

```text
messages
observations
caches
progress
findings
verification
control
usage
```

Current `InvestigationPhase` already contains concepts such as:

```text
CLEARIFY
VERIFY
REPAIR
FINISH
FINISH_WITH_EVIDENCE_GAP
SYNTHESIZE
READ_ONLY_FINISH
RESOLVE
DISCOVERY_REQUIRED
DISCOVER
```

The new design extends this rather than replacing it.

---

## 5. Core design decisions

The following decisions are normative.

### D1. Requirement/Solution is a new axis, not a replacement for `type`

Add:

```python
"domain": "requirement" | "solution"
```

Keep the existing `type`.

Examples:

```python
{
    "id": "U12",
    "question": "Should date-only todos remind at a default time?",
    "domain": "requirement",
    "type": "product_decision",
}
```

```python
{
    "id": "U13",
    "question": "How does CalendarModel receive per-date external metadata?",
    "domain": "solution",
    "type": "code_fact",
}
```

### D2. Persist only `parent_id` for tree structure

Add:

```python
"parent_id": str | None
```

Do **not** persist:

```text
children
depth
status
```

Rationale:

- `children` is derivable from `parent_id`.
- `depth` is derivable by walking parents.
- `status` would duplicate the authoritative `resolutions` state.

### D3. Requirement and Solution are not separate state-machine stages

There remains one Investigation runtime.

The active unknown's `domain` changes the prompt/policy, but both domains share:

- observations
- beliefs
- resolutions
- dynamic unknowns
- traversal state
- evidence cache
- tool infrastructure

Cross-domain children are explicitly allowed.

### D4. TaskAnalysis no longer generates the unknown universe

TaskAnalysis retains grounded task-contract extraction.

`analysis["unknowns"]` is retained temporarily as an empty compatibility field:

```python
"unknowns": []
```

`investigation_targets` remains as a bootstrap hint.

### D5. Tree exhaustion does not imply completion

When no currently open blocking node remains, Investigation enters **AUDIT**, not FINISH.

### D6. Root Audit owns semantic termination

Investigation is complete only when:

```text
Requirement Audit == complete
AND
Solution Audit == complete
AND
no open blocking unknown exists
```

### D7. `ready_for_patch_planning` becomes derived

For implementation tasks:

```python
ready_for_patch_planning = (
    audit_result["complete"]
    and not open_blocking_unknowns
)
```

The model may recommend patch planning, but may not independently set readiness.

---

## 6. Proposed data model

### 6.1 Unknown schema

New normalized unknown shape:

```python
{
    # Existing fields
    "id": str,
    "question": str,
    "blocking": bool,
    "type": str,
    "why": str,
    "resolution_strategy": str,
    "acceptance_criteria_ids": list[str],

    # Existing optional fields
    "origin": str | None,
    "reference_id": str | None,

    # New fields
    "domain": "requirement" | "solution",
    "parent_id": str | None,
}
```

### 6.2 Domain semantics

#### `domain="requirement"`

The node asks:

> What must be true about the requested behavior, scope, success semantics, constraints, or product decision for the task to be correctly understood?

Typical `type` values:

- `product_decision`
- `doc_fact`
- `risk`
- occasionally `runtime_fact` when runtime behavior is itself part of the user's requested contract

#### `domain="solution"`

The node asks:

> What repository/runtime/architecture/engineering fact or decision is required to safely achieve the established requirement?

Typical `type` values:

- `code_fact`
- `runtime_fact`
- `doc_fact`
- `engineering_decision`
- `risk`

The mapping is intentionally not one-to-one.

### 6.3 Parent semantics

`parent_id=None` means the unknown is attached directly to the Investigation root.

A child unknown must exist because resolving its parent requires this dependency.

Example:

```text
ROOT
└── U1 requirement: What are reminder semantics?
    └── U4 solution: What reminder primitives already exist?
        └── U5 requirement: Must missed reminders recover after restart?
```

### 6.4 Legacy normalization

For resumed/legacy unknowns that do not contain `domain`:

```python
if type == "product_decision":
    domain = "requirement"
else:
    domain = "solution"
```

For missing `parent_id`:

```python
parent_id = None
```

This heuristic is migration-only. Newly generated unknowns must always provide `domain`.

### 6.5 Unknown openness

Unknown state is derived from resolutions.

Closed:

```text
resolution.status in {"resolved", "deferred"}
```

Open:

```text
no resolution
OR status in {"partially_resolved", "needs_clearify"}
```

`blocking=False` affects traversal/finish gating but does not make a node resolved.

---

## 7. TaskAnalysis changes

### 7.1 Removed responsibility

Remove the TaskAnalysis slot responsible for generating Investigation unknowns:

```text
build_task_unknowns_slot_user(...)
```

TaskAnalysis must not attempt to enumerate repository/runtime facts required for completion.

### 7.2 Retained responsibilities

TaskAnalysis continues to normalize:

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

### 7.3 Meaning of `investigation_targets`

`investigation_targets` becomes explicitly defined as:

> Non-exhaustive high-level orientation hints that may seed Investigation discovery. They are not completion obligations and do not define the unknown universe.

### 7.4 Compatibility output

During migration:

```python
analysis["unknowns"] = []
```

The field remains because downstream code currently expects it.

Later cleanup may remove the field only after all callers have stopped requiring it.

---

## 8. Investigation bootstrap

When Investigation begins and no tree exists:

1. Read the complete Task Contract.
2. Use `requirements`, `acceptance_criteria`, `scope`, `behavior_contract`, `hypotheses`, `clues`, and `investigation_targets`.
3. Create a small number of **provisional root unknowns**.
4. Do not attempt to enumerate all future unknowns.
5. Prefer only blocking root nodes that materially affect correctness.

Example bootstrap:

```text
ROOT
├── U1 requirement: Are reminder semantics sufficiently defined?
├── U2 solution: How does module hover/expansion currently work?
└── U3 solution: How can todo metadata integrate with calendar?
```

The bootstrap generation belongs to Investigation's `DISCOVER` behavior, not TaskAnalysis.

Suggested `origin` values:

```text
investigation_bootstrap
discovery
root_audit
investigation_target
reference_baseline
clearify_followup
```

`origin` remains descriptive metadata and is not used to determine completion.

---

## 9. Traversal state

Add:

```python
@dataclass
class TraversalState:
    active_unknown_id: str = ""
    active_path: list[str] = field(default_factory=list)
    audit_cycle: int = 0
```

Extend:

```python
@dataclass
class InvestigationState:
    messages: ...
    observations: ...
    caches: ...
    progress: ...
    findings: ...
    verification: ...
    control: ...
    usage: ...
    traversal: TraversalState = field(default_factory=TraversalState)
```

### 9.1 `active_unknown_id`

The node currently being investigated.

### 9.2 `active_path`

Transient root-to-active path:

```python
["U1", "U4", "U5"]
```

This is runtime state only. It is not the canonical tree representation.

### 9.3 `audit_cycle`

Counts how many Root Audit cycles occurred in this Investigation lifecycle.

It is a loop-safety/budget metric, not a completion metric.

---

## 10. Traversal algorithm

Traversal is **DFS-like dependency traversal**, not strict textbook DFS.

### 10.1 Default node-selection order

Prefer:

1. Open blocking child of the active node.
2. Other open child of the active node.
3. Open blocking root/subtree node.
4. Open non-blocking node only if useful for quality/risk.
5. AUDIT when no open blocking node remains.

Within siblings, ranking may use:

```text
blocking importance
dependency relationship
expected information gain
estimated tool cost
acceptance-criterion coverage
risk
```

### 10.2 Node loop

Conceptually:

```python
while investigation_running:
    node = select_active_node()

    if node is None:
        enter_audit()
        continue

    if has_open_blocking_child(node):
        descend_to_child(node)
        continue

    gather_or_reuse_evidence(node)

    discoveries = derive_material_new_unknowns(node)

    if discoveries:
        add_children_or_root_nodes(discoveries)
        continue

    if can_resolve(node):
        resolve_unknown(node)

    if node_is_closed(node):
        backtrack()
```

### 10.3 Backtracking

When a child becomes closed:

```text
U5 resolved
  -> active path becomes U1 -> U4
U4 resolved
  -> active path becomes U1
U1 resolved
  -> active path becomes ROOT
```

### 10.4 Cross-domain traversal

Allowed:

```text
requirement -> solution
solution -> requirement
solution -> solution
requirement -> requirement
```

No domain boundary creates a separate runtime.

---

## 11. Requirement Investigation policy

Requirement nodes answer:

> Do we know enough about the user's intended outcome and behavior to implement the correct thing?

### 11.1 Allowed evidence sources

- original user request
- session context
- authoritative user references
- previous clarification answers
- repository evidence when implementation constraints reveal hidden requirement ambiguity
- external docs only when they are part of the task/reference baseline

### 11.2 Clarification policy

Do **not** immediately call `clearify` for every ambiguity.

First ask:

> Can targeted Solution Investigation determine whether this ambiguity is material or eliminate it?

Example:

```text
Requirement:
"What should happen to reminders when the app is closed?"

Before asking:
Solution child:
"Does the project have a background reminder daemon?"
```

If repository evidence shows no daemon, the product distinction becomes material and a `product_decision` clarification may be required.

### 11.3 Requirement resolution

A Requirement node may be resolved by:

- `user_decision`
- direct authoritative fact
- derived inference from explicit task contract + verified constraints

It must not be resolved merely because an implementation approach is convenient.

---

## 12. Solution Investigation policy

Solution nodes answer:

> Do we know enough about the current system and feasible implementation path to satisfy the established requirement safely?

Typical actions:

- `grep`
- `read`
- `glob`
- `code_nav`
- `lsp_tool`
- targeted runtime checks
- `subagent`
- `webfetch` when external technical evidence is genuinely required

### 12.1 Tool-call contract

Reuse the existing fields:

```python
target_unknown_ids
reason
hypothesis
expected_observation
decision_impact
stop_condition
```

No new action schema is needed.

### 12.2 Solution investigation must not rewrite requirements

Invalid inference:

```text
"The existing app lacks background scheduling, therefore the user does not require background reminders."
```

Correct behavior:

```text
Solution evidence:
"No background scheduler exists."

-> spawn Requirement child:
"Must reminders work while the application is not running?"
```

---

## 13. Findings and `new_unknowns`

The existing `record_investigation_findings` flow remains the canonical path for grounded runtime findings.

`new_unknowns` must be extended to require:

```text
id
question
blocking
type
resolution_strategy
domain
parent_id
```

It should also preserve, when supplied:

```text
why
acceptance_criteria_ids
origin
reference_id
```

### 13.1 Child creation rule

When discovery occurs while investigating node `U7`:

```python
new_unknown["parent_id"] = "U7"
```

unless the finding is genuinely root-global.

### 13.2 Root-level discovery rule

Set:

```python
parent_id = None
```

for gaps discovered by Root Audit or global bootstrap.

### 13.3 No duplicate semantic nodes

Existing unknown deduplication/canonical-ID behavior should be retained.

Additionally, avoid inserting a new node when an equivalent open node already exists in the same domain.

---

## 14. Resolution behavior

Existing `resolve_unknowns` remains.

A node may be marked `resolved` only if:

1. Its answer directly addresses the node question.
2. Required evidence is present.
3. All blocking child dependencies are closed.
4. Semantic/evidence gates pass.
5. The resolution does not contradict the Task Contract or authoritative user decisions.

`partially_resolved` remains open.

`needs_clearify` remains open.

`deferred` closes the node only when it is non-blocking or policy explicitly permits deferral.

---

## 15. Root Audit

### 15.1 New phase

Add:

```python
AUDIT = "audit"
```

to `InvestigationPhase`.

AUDIT occurs whenever:

```text
no currently open blocking unknown exists
```

and before FINISH.

### 15.2 Audit purpose

Audit is not:

> Did we resolve every node we happened to create?

It is:

> Given the original task plus everything learned during Investigation, is there still a material unknown that could make the next stage design the wrong behavior or the wrong implementation?

### 15.3 Audit dimensions

#### Requirement Audit

Checks:

- intended outcome
- success semantics
- behavior
- scope boundaries
- meaningful product decisions
- constraints
- edge cases that materially alter implementation
- contradictions between user request and discovered constraints

Question:

> Do we know what must be achieved?

#### Solution Audit

Checks:

- relevant architecture located
- state ownership understood
- required integration points identified
- dependencies understood
- persistence/runtime behavior understood where relevant
- major engineering choices resolved
- risks/blockers known
- validation path understood sufficiently for Design/Plan

Question:

> Do we know enough about how this system can achieve it?

### 15.4 Audit tool/result contract

Recommended new control tool:

```text
audit_investigation
```

Model-facing arguments:

```python
{
    "reason": str,

    "requirement": {
        "complete": bool,
        "reason": str,
    },

    "solution": {
        "complete": bool,
        "reason": str,
    },

    "new_unknowns": [
        # standard unknown shape including domain + parent_id
    ],
}
```

Runtime validates and stores:

```python
audit_result = {
    "requirement": {
        "complete": bool,
        "reason": str,
        "open_unknown_ids": list[str],  # runtime-computed
    },

    "solution": {
        "complete": bool,
        "reason": str,
        "open_unknown_ids": list[str],  # runtime-computed
    },

    "complete": bool,                   # runtime-computed
}
```

Runtime computes:

```python
audit_result["complete"] = (
    audit_result["requirement"]["complete"]
    and audit_result["solution"]["complete"]
    and not open_blocking_unknowns
)
```

### 15.5 Audit consistency rules

If model reports:

```text
requirement.complete = False
```

then at least one of the following must be true:

- an open blocking Requirement unknown already exists
- Audit returns a new blocking Requirement unknown

Same for Solution.

If a new audit unknown is emitted:

```python
origin = "root_audit"
parent_id = None
```

unless the audit explicitly identifies a dependency of a specific existing node.

### 15.6 Audit loop

```text
Tree exhausted
    |
    v
AUDIT
    |
    +-- gaps found --> new_unknowns --> traversal resumes
    |
    `-- no gaps --> complete
```

This loop may occur multiple times.

---

## 16. `ready_for_patch_planning`

### 16.1 New semantics

`ready_for_patch_planning` becomes runtime-derived.

For implementation intent:

```python
ready_for_patch_planning = bool(
    audit_result
    and audit_result["complete"]
    and not open_blocking_unknowns
)
```

For read-only intent it remains `False`.

### 16.2 Model authority

The model may recommend:

```text
patch_planning
continue_investigation
done
```

but cannot override runtime readiness.

Any model-supplied `ready_for_patch_planning=True` should be ignored or rejected.

---

## 17. `finish_investigation` changes

The current finish contract can remain:

```python
{
    "reason": str,
    "summary": str,
    "patch_planning_facts": list[str],
    "patch_planning_context": list[str],
    "recommended_next_step": (
        "patch_planning"
        | "continue_investigation"
        | "done"
    ),
    ...
}
```

### 17.1 Implement task finish gate

`finish_investigation(recommended_next_step="patch_planning")` is accepted only if:

```text
audit_result.complete == True
AND no open blocking unknown
```

Otherwise return a structured tool error requiring:

```text
audit or continued investigation
```

### 17.2 Read-only task finish gate

Read-only tasks may finish with `done` only after Root Audit is complete, unless an explicit evidence-gap completion policy applies.

### 17.3 FINISH no longer decides semantic completeness

FINISH packages already-established findings.

AUDIT decides completeness.

---

## 18. Pass limits and loop safety

The existing outer `MAX_INVESTIGATION_PASSES = 3` should no longer act as the semantic definition of investigation completeness.

Recommended change:

1. Keep model-round/tool budgets (`max_rounds`, effort profile, duplicate-progress protection).
2. Add an audit-cycle safety budget, preferably effort-dependent.
3. If budget is exhausted:
   - do not synthesize `ready_for_patch_planning=True`
   - emit explicit evidence-gap/failure state
   - retain open unknowns and audit reason

Suggested initial defaults:

```text
fast:     max_audit_cycles = 1
standard: max_audit_cycles = 2
deep:     max_audit_cycles = 3
```

These are safety limits, not proofs of completeness.

If experience shows valid deep tasks routinely need more cycles, increase them independently of tree depth.

---

## 19. State transitions

Target high-level flow:

```text
ANALYZING
   |
   v
INVESTIGATING
   |
   +--> DISCOVER
   |      |
   |      v
   |   active unknown
   |      |
   |      +--> Requirement policy
   |      |
   |      `--> Solution policy
   |
   +--> RESOLVE
   |      |
   |      v
   |   backtrack
   |
   +--> AUDIT
   |      |
   |      +--> new unknowns --> DISCOVER
   |      |
   |      `--> audit complete
   |
   `--> FINISH
          |
          +--> implement --> DESIGNING
          `--> read-only --> final state
```

---

## 20. End-to-end example

User request:

> Add a note module to omibar. It stores quick notes and todos. Todos integrate with the calendar; each todo on a date adds a dot below the date, with at most five visible dots although todo count is unlimited. When a todo becomes due, the note module flashes, plays a sound, and widens to about 15 Chinese characters to show the due todo. Hover should fan notes out like macOS Trash, newest lower and oldest higher, and clicking opens a note.

### 20.1 TaskAnalysis

Produces grounded contract only:

```python
{
    "intent": {
        "type": "feature",
        "summary": "Add note/todo module with calendar integration, due reminders, and hover expansion.",
    },

    "execution_mode": "implement",
    "effort": "deep",
    "risk": "medium",
    "quality_gate": "strict",

    "requirements": [
        {"id": "REQ1", "text": "Add a note module for notes and todos."},
        {"id": "REQ2", "text": "Todos are associated with calendar dates."},
        {"id": "REQ3", "text": "Calendar renders up to five todo dots per date."},
        {"id": "REQ4", "text": "Due todos flash, sound, and appear in a widened capsule."},
        {"id": "REQ5", "text": "Hover fans notes out in the requested order and permits opening them."},
    ],

    "acceptance_criteria": [
        {"id": "AC1", "text": "Users can create and access notes/todos."},
        {"id": "AC2", "text": "Calendar reflects todo counts with max five dots."},
        {"id": "AC3", "text": "Due todos produce requested visual/audio reminder."},
        {"id": "AC4", "text": "Hover exposes clickable notes in requested order."},
    ],

    "constraints": [
        "Calendar shows at most five dots.",
        "Todo count is unlimited.",
        "Due capsule targets about fifteen Chinese characters.",
    ],

    "investigation_targets": [
        "existing module architecture",
        "calendar integration mechanism",
        "reminder/timer/audio infrastructure",
        "persistent note storage",
        "hover/capsule animation infrastructure",
    ],

    "unknowns": [],
}
```

### 20.2 Bootstrap

Investigation creates:

```text
ROOT
├── U1 [requirement] Are reminder semantics sufficiently defined?
├── U2 [solution] How do compact/hover/expanded module states work?
└── U3 [solution] How can todo state integrate with Calendar?
```

### 20.3 Requirement -> Solution descent

While investigating U1, the agent realizes the significance of closed-app behavior depends on existing scheduler capability.

Create:

```python
{
    "id": "U4",
    "question": "What date/time and reminder primitives already exist?",
    "domain": "solution",
    "parent_id": "U1",
    "type": "code_fact",
    "blocking": True,
    "resolution_strategy": "investigate_project",
    "origin": "discovery",
}
```

Tree:

```text
U1 requirement
└── U4 solution
```

### 20.4 Tool call

Existing tool metadata is reused:

```python
grep(
    pattern="Timer|calendar|notification|audio|reminder",
    target_unknown_ids=["U4"],
    reason="Locate reminder infrastructure.",
    hypothesis="The project contains reusable runtime reminder primitives.",
    expected_observation="Timer/notification/audio services and lifecycle.",
    decision_impact="Determine whether background behavior requires a product decision.",
    stop_condition="Stop after scheduler ownership and lifecycle are known.",
)
```

Suppose evidence shows reminders run only while the shell process is alive.

### 20.5 Solution -> Requirement child

Create:

```python
{
    "id": "U5",
    "question": "Must missed reminders be recovered after omibar restarts?",
    "domain": "requirement",
    "parent_id": "U4",
    "type": "product_decision",
    "blocking": True,
    "resolution_strategy": "clearify",
    "origin": "discovery",
}
```

Tree:

```text
U1 requirement
└── U4 solution
    └── U5 requirement
```

User chooses:

> No background daemon; recover missed reminders on next launch.

Resolve U5, backtrack to U4, resolve U4, backtrack to U1.

### 20.6 Additional Requirement child

U1 still lacks exact due-time behavior.

Create U6:

```text
U1
└── U6 requirement: Should todos support exact due time?
```

User answers:

> Exact time optional; date-only defaults to 09:00.

Resolve U6, then U1.

### 20.7 Solution nodes

U2 investigates base-module hover behavior.

U3 investigates Calendar data flow and may create:

```text
U3 solution
└── U7 solution: Can existing shared state expose per-date todo metadata?
```

Additional root Solution unknowns may cover:

```text
U8 persistence
U9 audio
U10 flashing animation
U11 validation/testing path
```

### 20.8 Tree exhaustion

After all current blocking nodes are closed:

```text
ROOT
├── U1 R ✓
│   ├── U4 S ✓
│   │   └── U5 R ✓
│   └── U6 R ✓
├── U2 S ✓
├── U3 S ✓
│   └── U7 S ✓
├── U8 S ✓
├── U9 S ✓
├── U10 S ✓
└── U11 S ✓
```

Do **not** finish.

Enter AUDIT.

### 20.9 Requirement Audit finds new gap

Audit notices:

> Multiple todos can become due simultaneously, but the widened capsule can show only one item.

Generate:

```python
{
    "id": "U12",
    "question": "When multiple todos become due together, which item is shown first?",
    "domain": "requirement",
    "parent_id": None,
    "type": "product_decision",
    "blocking": True,
    "resolution_strategy": "clearify",
    "origin": "root_audit",
}
```

User decides:

> Earliest due first; others queue.

Resolve U12.

### 20.10 Solution Audit reacts to new requirement

Audit now identifies:

```python
{
    "id": "U13",
    "question": "How should simultaneous due reminders be queued in the current state model?",
    "domain": "solution",
    "parent_id": None,
    "type": "engineering_decision",
    "blocking": True,
    "resolution_strategy": "investigate_project",
    "origin": "root_audit",
}
```

Investigate and resolve U13.

### 20.11 Final Audit

```python
{
    "requirement": {
        "complete": True,
        "reason": "All material behavior semantics are defined.",
        "open_unknown_ids": [],
    },
    "solution": {
        "complete": True,
        "reason": "Architecture, state ownership, persistence, scheduling, UI integration, and validation path are sufficiently evidenced.",
        "open_unknown_ids": [],
    },
    "complete": True,
}
```

Runtime derives:

```python
ready_for_patch_planning = True
```

Only now may `finish_investigation(... patch_planning ...)` hand off to Design.

---

## 21. Suggested code-change map

### 21.1 `stratumcode/status/task_analysis.py`

- Stop generating `unknowns_slot`.
- Stop calling `build_task_unknowns_slot_user`.
- Preserve `unknowns=[]` during compatibility phase.
- Update compact/full analyzer readiness rules.
- Update prompts so TaskAnalysis does not invent Investigation unknowns.

### 21.2 `stratumcode/status/task_contract.py`

- Extend unknown normalization with:
  - `domain`
  - `parent_id`
- Add domain validation.
- Add legacy domain fallback.
- Preserve current `type`, strategy, blocking, acceptance IDs, origin/reference fields.

### 21.3 `stratumcode/status/investigation_context.py`

- Do not treat TaskAnalysis unknowns as the semantic initial universe.
- Render `domain` and `parent_id` in context where unknowns exist.
- Resume merged dynamic unknowns correctly.

### 21.4 `stratumcode/investigator/state.py`

- Add `InvestigationPhase.AUDIT`.
- Add `TraversalState`.
- Add `traversal` to `InvestigationState`.

### 21.5 `stratumcode/investigator/findings.py`

- Extend `new_unknowns` slot instructions/contracts to require:
  - `type`
  - `domain`
  - `parent_id`
- Preserve current normalization and deduplication.
- Ensure runtime-created IDs remain stable if runtime owns ID assignment.

### 21.6 `stratumcode/investigator/domain.py`

- Update unknown merge/normalization helpers for tree metadata.
- Update "all initial unknowns resolved" helpers; completion must no longer be based on initial unknown coverage.
- Add helpers:
  - open unknowns
  - open blocking unknowns
  - children-by-parent
  - parent lookup
  - node closed/open
  - domain filtering

### 21.7 `stratumcode/investigator/tools.py`

- Add `_audit_investigation_tool_schema()`.
- Add AUDIT tool selection.
- Extend structured errors for attempted finish before Audit completion.
- Keep existing evidence tool metadata unchanged.

### 21.8 `stratumcode/investigator/directive.py`

- Add domain-aware directive/prompt selection.
- Requirement node policy and Solution node policy must differ.
- Trigger AUDIT when tree has no open blockers.

### 21.9 `stratumcode/investigator/finalize.py`

- Stop treating resolved initial unknown coverage as sufficient completion.
- Merge dynamic tree unknowns.
- Store `audit_result`.
- Derive `ready_for_patch_planning`.
- Reject patch planning without complete Audit.

### 21.10 `stratumcode/status/investigation_transitions.py`

- Remove initial-unknown coverage as a semantic completion fallback.
- Transition to `DESIGNING` only from runtime-derived readiness.
- Revisit `MAX_INVESTIGATION_PASSES`; it must not be the definition of completeness.

### 21.11 `stratumcode/prompt.py`

Update:

- TaskAnalyzer prompt
- Investigation bootstrap/discovery prompt
- Requirement-domain investigation prompt
- Solution-domain investigation prompt
- Root Audit prompt

Remove instruction patterns that imply the TaskAnalysis unknown list is exhaustive.

---

## 22. Migration plan

### Phase A — Schema compatibility

1. Add `domain` and `parent_id`.
2. Preserve all existing tests via migration defaults.
3. Update `new_unknowns` normalization.
4. Do not yet change termination.

### Phase B — Move unknown discovery

1. Stop TaskAnalysis unknown generation.
2. Return `unknowns=[]`.
3. Add Investigation bootstrap discovery.
4. Verify ordinary tasks still generate useful initial root nodes.

### Phase C — Tree traversal

1. Add `TraversalState`.
2. Add parent-aware child selection.
3. Add backtracking.
4. Ensure cross-domain child creation works.
5. Keep existing resolution/evidence machinery.

### Phase D — Root Audit and termination

1. Add AUDIT phase/tool.
2. Store `audit_result`.
3. Make readiness derived.
4. Change finish/transition gates.
5. Remove semantic reliance on `_recorded_covers_unknowns(initial)`.

### Phase E — Cleanup

1. Remove obsolete TaskAnalysis unknown prompts/slots.
2. Remove compatibility-only logic once no longer needed.
3. Rename helpers whose names still imply "initial unknown universe".
4. Update docs/front-end inspector if tree visualization is desired.

---

## 23. Testing strategy

### 23.1 Unit tests — Unknown normalization

Test:

- valid `domain=requirement`
- valid `domain=solution`
- invalid domain rejected/fallback according to compatibility policy
- missing domain legacy mapping
- `parent_id=None`
- valid parent string
- `type` remains unchanged
- no persisted `status/children/depth`

### 23.2 Unit tests — Resolution/open state

Test:

- no resolution -> open
- `partially_resolved` -> open
- `needs_clearify` -> open
- `resolved` -> closed
- `deferred` -> closed when policy allows
- blocking child prevents parent closure

### 23.3 Unit tests — Tree traversal

Scenarios:

```text
R -> S -> R
S -> S
multiple siblings
root-level audit node
backtracking after child resolution
```

Verify `active_path`.

### 23.4 Unit tests — Requirement clarification policy

Case:

```text
Requirement ambiguity
-> Solution child can determine materiality
-> no premature clearify
```

Case:

```text
Solution evidence makes product decision unavoidable
-> Requirement child product_decision
-> clearify
```

### 23.5 Unit tests — Audit

- no blockers but no audit -> cannot finish
- Requirement incomplete -> new Requirement unknown
- Solution incomplete -> new Solution unknown
- model says complete but open blocker exists -> runtime complete=False
- model says incomplete but emits no corresponding gap -> validation error
- both complete + no blockers -> audit complete

### 23.6 Unit tests — readiness

For implement tasks:

```text
audit incomplete -> false
open blocker -> false
audit complete + no blocker -> true
```

Model-provided readiness must not override runtime.

### 23.7 Integration tests

At minimum:

1. Simple feature with only Solution facts.
2. Requirement ambiguity requiring user clarification.
3. Solution discovery creating Requirement child.
4. Requirement discovery creating Solution child.
5. Audit discovers a new Requirement gap.
6. New Requirement causes a new Solution gap on next Audit.
7. Resume from existing `last_investigation`.
8. Legacy run containing old unknowns without `domain`.
9. Read-only investigation.
10. Budget exhaustion with unresolved blockers.

### 23.8 Regression tests

Ensure existing behavior remains for:

- observation grounding
- belief evidence
- duplicate tool-call suppression
- semantic repair
- clearify answer injection
- resolution evidence validation
- bugfix readiness checks

---

## 24. Observability and UI

Not required for first implementation, but event payloads should preserve enough data for the frontend to render:

```text
ROOT
├── U1 requirement
│   └── U4 solution
└── U2 solution
```

Recommended event additions:

```python
{
    "active_unknown_id": ...,
    "active_path": [...],
    "audit_cycle": ...,
}
```

Do not make frontend rendering a blocker for backend correctness.

---

## 25. Invariants

The implementation must maintain these invariants:

1. Every newly generated unknown has exactly one valid `domain`.
2. Every non-root `parent_id` refers to an existing unknown.
3. Parent chains cannot contain cycles.
4. Resolution remains the sole authoritative closed/open state.
5. A parent cannot become fully resolved while a blocking child is open.
6. `ready_for_patch_planning` cannot be true before Audit completion.
7. Root Audit cannot be bypassed by `finish_investigation`.
8. `TaskAnalysis` does not define an exhaustive unknown universe.
9. Requirement and Solution nodes may create each other.
10. Tool evidence remains explicitly targeted to unknown IDs.
11. Audit gaps become real `new_unknowns`, not hidden free-text TODOs.
12. A model recommendation cannot override runtime structural gates.

---

## 26. Final architecture

```text
USER
 |
 v
TaskAnalysis
 |  grounded task contract
 |  no unknown universe
 v
Investigation ROOT
 |
 +--> bootstrap provisional unknowns
 |
 +--> Requirement node
 |      |
 |      +--> inspect request/context
 |      +--> optionally spawn Solution child
 |      `--> clearify only when materially required
 |
 +--> Solution node
 |      |
 |      +--> targeted tool calls
 |      +--> observations/beliefs
 |      `--> may spawn Requirement child
 |
 +--> resolve children
 |      |
 |      `--> backtrack
 |
 +--> tree currently exhausted
 |
 v
ROOT AUDIT
 |
 +--> Requirement incomplete --> new Requirement unknown
 |
 +--> Solution incomplete ----> new Solution unknown
 |
 `--> both complete + no blockers
             |
             v
 runtime derives ready_for_patch_planning
             |
             v
 finish_investigation
             |
             +--> implement -> DESIGNING
             `--> read-only -> final response
```

---

## 27. Definition of success

The refactor is successful when StratumCode no longer finishes Investigation because it merely covered a TaskAnalysis-generated list.

Instead, the system must be able to demonstrate:

1. what the user requires,
2. how the current repository can satisfy it,
3. how every material dependency was resolved,
4. what evidence supports the conclusions,
5. that a final Root Audit found no remaining blocking Requirement or Solution gap.

That is the new semantic definition of "Investigation complete".
