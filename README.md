<p align="center">
  <img src="docs/assets/stratumcode-logo.png" alt="StratumCode" width="120" />
</p>

<p align="center">
  <img src="docs/assets/stratumcode-wordmark.png" alt="StratumCode" width="640" />
</p>

<h1 align="center">StratumCode</h1>

<p align="center">
  <strong>Evidence-driven. Contract-first.</strong>
</p>

<p align="center">
  A local-first software engineering agent. It does not jump from your instruction straight to editing code —<br/>
  it runs the full pipeline: task analysis, code investigation, design decisions, an executable patch plan,<br/>
  transactional edits, and independent validation. Every step is traceable, auditable, and reversible.
</p>

<p align="center">
  <a href="./README.md">English</a> | <a href="./README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <img alt="Status: Alpha" src="https://img.shields.io/badge/status-alpha-f4b942" />
  <img alt="Python 3.13+" src="https://img.shields.io/badge/Python-3.13%2B-3776AB?logo=python&logoColor=white" />
  <img alt="Vue 3" src="https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white" />
  <img alt="Local first" src="https://img.shields.io/badge/runtime-local--first-1756d1" />
  <img alt="MCP" src="https://img.shields.io/badge/tools-MCP-6658c7" />
  <img alt="LSP" src="https://img.shields.io/badge/code%20intelligence-LSP-0f7d65" />
  <img alt="GitHub stars" src="https://img.shields.io/github/stars/iiishop/StratumCode?style=flat-square" />
</p>

---

## Why this exists

About a year ago, I handed almost all of my coding work to AI. At first I stayed disciplined — designing the architecture and interfaces myself, then having Claude produce files one by one. Then Copilot's agent mode landed, and I stopped even doing the design. Throw the requirement in, wait for a result, and if it is not right, try again. This is what people now call "wishful programming."

It was certainly faster. During my thesis project, output speed tripled or quadrupled. A thousand lines used to be a good day's work; now it is a single conversation.

But something was disappearing. I used to be able to run the code in my head before it ran on the screen — tracing the flow, predicting behavior, then watching something I had built brick by brick actually work. That feeling was the whole point. Along with it went my obsession with code quality: the intolerance for hardcoding, for tight coupling, for nights spent on maintainability.

The agent changed all of that. The output volume got so large that I stopped reviewing and only checked the final result. Code became a black box, and I became an acceptance machine.

About six months in, pure agent coding hit a wall. I tried switching back to writing code by hand, but once you have tasted that efficiency it is hard to go back. It is like trying to use a spinning jenny while everyone else has moved to the assembly line.

So I started reading the early agent papers — ReAct, Toolformer — tried every mainstream coding agent on the market, and finally decided to build my own.

**StratumCode**, literally "stratum analysis of code." But I prefer to call it "code archaeology."

Before agents, the most common thing a programmer did was stare at the screen. Jump from a function name to its implementation, follow a field to its references, run the whole module in your head, and slowly build a mental map of a large codebase. It is a lot like archaeology — starting from a small fragment, expanding outward, and eventually reconstructing the truth of a vast artifact.

This agent works the same way. I want programmatic contracts and runtime enforcement to constrain the process — not a pile of skill prompts that politely beg the model to behave.

## Core philosophy

> **The model proposes. The runtime validates, authorizes, records, and routes.**

Most coding agents chase the shortest path from requirement to patch. That is efficient — until the requirement's assumption is wrong, the codebase tells a different story, or a reasonable-looking change quietly breaks a contract somewhere.

StratumCode starts from a different premise: **a software change should be explainable before it is executed.**

## Highlights

| Highlight | Why it matters |
|---|---|
| Evidence-driven state machine | Each stage has its own responsibility and transition rules; investigation never leaks into design |
| Runtime-level hard constraints | Correctness does not depend on the model following a prompt — patch authorization, snapshots, atomic commits, and rollback are enforced by the runtime |
| Persistent engineering memory | Cross-session `memory_system`: graph structure, compression, freshness, selector — project knowledge accumulates |
| Local-first | Data stays on your machine; OpenAI-compatible endpoints plus experimental Codex OAuth |
| Desktop application | pywebview + Vue 3: timeline, memory panel, usage stats, self-update all in the UI |

## Workflow

StratumCode drives the whole process with an explicit state machine. Each stage has its own responsibilities and transition rules:

```text
User request → Task Analysis → Investigation → Design → Patch Planning → Implementation → Validation
                  ↑               ↑                         ↑                      │
                  └── evidence ───┘                         └── repair ────────────┘
                  insufficient                                    redesign ─────────┘
                                                                   user input ───────→ ask
```

### 1. Task Analysis — figure out what you actually want

More than natural-language parsing. This stage produces:

- **Task classification**: Feature / Bugfix / Refactor / Investigation etc.
- **Goal**: the core intent extracted from your input
- **Unknowns**: what is not yet clear and needs investigation
- **Acceptance Criteria**: which observable facts must hold when the task ends
- **Behavior Contract**: how this feature behaves at the interface level — inputs, outputs, what success and failure mean, and where the boundaries are
- **Scope**: what this task does, explicitly does not do, and what remains open

### 2. Investigation — survey the code like a real programmer

Driven by the Unknowns, resolved one by one. The model can call:

- Basic tools: Read, Grep, Glob
- LSP tools: symbol info, go-to-definition, find-references, go-to-implementation — just like an IDE
- MCP: if you have configured it and it is relevant
- Web: search and fetch

There is also a dedicated **hypothesis verification** subagent. For assumptions that need large-scale validation, it forces a strict loop: "find supporting evidence → find opposing evidence → audit the relationship → state a conclusion." The result is not "I think so" but "97% confidence, hypothesis holds."

Investigation ends not when "the model feels done," but when every Unknown is resolved and every Acceptance is backed by project facts.

### 3. Design — think before you touch code

Design never touches code; it only produces a design document. It converts Acceptance Criteria into:

- **Requirements**: preserving the link to the original Acceptances — restating must not change the acceptance semantics
- **Project Alignment**: each requirement checked against the current project — Matched, Missing, or Ambiguous
- **Design Decision**: the final design given all requirements and the current state. Every decision must state which requirements it resolves and which facts it is based on

This is about behavior, responsibility boundaries, state flow, and data flow. Not "which file to edit."

### 4. Patch Planning — break the design into an executable construction plan

For each Design Decision, first decide whether code changes are even needed (the existing implementation may already satisfy it). If needed, produce concrete Implementation Steps:

- Which file, which function/component
- What operation
- What the completion condition is
- What would be lost if this step were removed
- What this step should not touch along the way

Then a second audit pass: which Steps cover each Acceptance? Are the validation commands and operations backed by project facts? Do any Steps need merging? Has any Step's Purpose, Action, or Completion Conditions drifted from the original design?

### 5. Implementation — transactional edits

This is the first stage that actually changes code. But not arbitrary edits in an IDE — every change must specify a Step ID and execute through `apply_patch`. The runtime injects the authorization ID, Plan Hash, and the linked Acceptances and Design Decisions. The model cannot touch files outside the plan, nor silently expand a step's scope.

The file snapshot must be read before modification. After the patch, the runtime verifies: does the file exist? Was the target text added or removed? Zero-diff patches are rejected outright.

If Implementation fails midway, applied patches are rolled back in reverse order via the Rollback Record — no half-finished construction debris.

### 6. Validation — independent acceptance

Validation does not trust Implementation's claim of "done." It re-reads the modified code and, against the original Acceptances, Patch Plan, and Change Records, independently judges:

- Was every Step fully executed?
- Does the actual change match the design — anything missed, drifted, or expanded?
- Does the final behavior satisfy the Acceptance Criteria?

At least one successful tool check is required for `passed`. Declaring success from a plan summary without actually reading code or running checks is not allowed.

Possible outcomes:

| Result | Meaning |
|---|---|
| `passed` | Validation passed, task complete |
| `local_repair` | Design is right but the implementation is flawed; return to Design |
| `redesign` | The design itself is insufficient or wrong |
| `missing_evidence` | Not enough evidence to judge; return to Investigation |
| `clearify` | A product choice needs your input |
| `inconclusive` | No reliable conclusion possible |

## Skills system

Skills are deployed per stage rather than stuffed into the prompt all at once. You can define them globally, for a specific stage, or both — independently configured or stacked.

The built-in `find-skills` command can search and install skills online. At the start of every stage the model is forced to choose zero or more skills, and can load more mid-stage if needed. Progressive disclosure keeps the prompt clean.

## Safety guarantees

StratumCode's correctness does not depend on the model following a piece of prompt text. These are hard constraints at the runtime level:

- **Explicit execution mode**: read-only tasks never silently become implementation tasks
- **Scope authorization**: patches may only touch files specified by the authorized Step
- **Immutable Plan Hash**: patch requests must match the authorized plan
- **File snapshots**: existing files must be read before modification
- **Staleness detection**: concurrent edits invalidate stale snapshots
- **Atomic commits**: multi-file patches commit as one transaction
- **Zero-diff rejection**: no real change, no claimed progress
- **Patch records**: every Step's intent, file, hash, and diff are recorded
- **Rollback**: a failed Implementation can restore committed files
- **Evidence gate**: `passed` requires at least one successful validation tool result

## Quick Start

### Requirements

- Python **3.13+**
- Node.js and npm
- [`uv`](https://docs.astral.sh/uv/)
- A model provider configured in-app

### Install and run

```bash
git clone https://github.com/iiishop/StratumCode.git
cd StratumCode

uv sync
npm --prefix frontend install

# Production mode
uv run stratumcode

# Development mode (API + Vite HMR + pywebview)
uv run stratumcode-dev
```

On first production launch, if `frontend/dist` does not exist, the Vue frontend is built automatically.

## Desktop UI

A `pywebview` native window hosts the Vue 3 frontend. This is not a CLI tool — you get:

- **Workspace and session management**: repositories stay isolated and resumable
- **Provider configuration**: OpenAI-compatible endpoints + experimental Codex OAuth
- **MCP integration**: external tools and repository intelligence
- **LSP support**: symbols, definitions, references, hover, diagnostics
- **Skills panel**: capability configuration per global, stage, and subagent
- **Timeline events**: evidence, tools, agents, tasks, transitions, plans, patches, validation results
- **Usage stats**: token, cache, and cost information (per currency)
- **Memory panel**: inspect the cross-session memory graph, selection, and compression results
- **Code structure panel**: repository structure browsing
- **Git panel**: repository status and common operations
- **Self-update check**: check for new versions in-app

## Project structure

```text
StratumCode/
├── frontend/              Vue 3 + Vite desktop UI
│   └── src/
│       ├── components/    pages, timeline, inspector, memory, usage, settings
│       └── composables/   frontend state and API integration
│
├── stratumcode/
│   ├── chat.py            explicit runtime state machine
│   ├── status/            per-stage state handlers and task contracts
│   ├── investigator/      evidence collection, findings, Unknown resolution
│   ├── hypothesis_verifier.py  hypothesis verification subagent
│   ├── design_planner.py  fact-based design authoring
│   ├── patch_planner.py   executable Patch Plan generation
│   ├── implementation_runner.py  implementation and validation loop
│   ├── patch_engine.py    snapshots, atomic edits, rollback
│   ├── patch_authorization.py  Plan- and Step-level write authorization
│   ├── memory_system/     persistent engineering memory (graph, compression, selection)
│   ├── code_structure/    repository structure building
│   ├── agent/             agent policy and evidence
│   ├── clearify_runtime.py  routing for user-confirmation questions
│   ├── git_panel.py       Git integration
│   ├── updates.py         self-update check
│   ├── skill_runtime.py   per-stage/subagent skill loading
│   ├── tools/             built-in tool registration
│   ├── lsp/               Language Server Protocol integration
│   ├── mcp/               Model Context Protocol client
│   ├── providers.py       model providers and Codex transport
│   ├── sessions.py        persistent session artifacts
│   └── server.py          local app API
│
└── pyproject.toml         Python package and CLI entry points
```

## Current status

> [!IMPORTANT]
> StratumCode is in **Alpha** (currently v0.0.17). Contract formats, storage schemas, provider transports, and UI may change with every commit. Use it on version-controlled repositories and review diffs before relying on changes.

### Implemented

- [x] Local desktop app with workspace and session management
- [x] Full Task Analysis → Investigation → Design → Patch → Validation state machine
- [x] Structured task contracts, Unknowns, observations, beliefs, acceptance criteria
- [x] Independent hypothesis verification subagent (supporting/opposing evidence → audit → confidence conclusion)
- [x] Persistent engineering memory (graph structure, compression, freshness, selector)
- [x] OpenAI-compatible Provider + experimental Codex OAuth
- [x] MCP service discovery and dynamic tool registration
- [x] LSP symbols, definitions, references, hover, diagnostics
- [x] Dynamic Skills configured per global/stage/subagent
- [x] Patch authorization, snapshots, staleness detection, atomic writes, rollback
- [x] Incremental session saving (avoids memory leaks in large sessions)
- [x] Usage statistics (per-currency cost tracking)
- [x] Self-update check and Git panel
- [x] Linux platform support

### In progress

- [ ] Richer repository intelligence and reusable context
- [ ] Task-type-specific investigation strategies
- [ ] More complete deterministic validation of executable Patch Plans
- [ ] Broader tool support (tests, builds, formatting, static checks)
- [ ] Complete repair routing (scope, environment, product-decision failures)
- [ ] A fixed benchmark suite (Bugfix, Feature, Refactor, UI, Config, Concurrency)

## Near-term direction

- Dedicated architecture design subagent
- Cross-frontend/backend call-chain visualization — from trigger to completion
- CI/CD integration

## Contributing

Contributions are especially welcome in these areas:

- Runtime contracts and deterministic validators
- Repository indexing and code navigation
- Safe patching and concurrent edit protection
- LSP and MCP interoperability
- Evaluation tasks and regression tests
- Provider adapters and local model support
- Developer experience improvements in the Vue UI

For large architectural changes, open an Issue first and confirm responsibility boundaries before starting.

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=iiishop%2FStratumCode&type=Date)](https://star-history.com/#iiishop/StratumCode&Date)

---

<p align="center">
  <strong>StratumCode</strong><br/>
  Review the evidence. Understand the decision. Trust the diff.
</p>
