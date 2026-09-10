# Investigation Cost and Traversal Review

Source: session export 2026-09-09T08:38:32.075Z, final conversation.

## Observed Usage

The 214 exported usage deltas report 15,549,599 input tokens and 70,785 output
tokens. Cached input is 13,391,488 tokens, already included in input, not additive.
These are recorded usage totals, not a measured post-fix saving. Failed internal
slot calls could previously lose their usage events on exception.

| Input per request | Requests | Summed input |
| --- | ---: | ---: |
| At least 100,000 | 69 | 11,827,454 |
| 20,000 to 99,999 | 67 | 3,108,113 |
| Below 20,000 | 78 | 614,032 |

Ten read events contain 64,771 characters of requested output plus 167,021
characters of metadata.full_text. Even bounded reads returned whole-file
snapshots in model-facing metadata, which remained in subsequent request history.

## Root Causes and Changes

- U25 completed while sibling U26 remained open, but traversal selected their
  parent U23 and forced another recording step. Traverse remaining siblings
  first; synthesize the parent only after its child subtrees are complete.
- Internal new_unknowns expected an object while the public tool used an array.
  Repeated internal shape failures eventually triggered repeated_tool_error.
  Rename the internal slot next_inquiry, permit existing-child continuation,
  and reuse the primary model's explicit decision instead of asking twice.
- Parent continuation could regenerate questions belonging to an ancestor.
  Prompts now keep child completion separate from future parent work.
- Remove only runtime-only full_text from model tool messages. Keep requested
  output intact and preserve complete snapshots in observations and caches.
- Restrict record-slot findings to the active question, retaining completed
  child answers/evidence separately. No original evidence is deleted.
- Publish internal usage events even when slot generation raises an error.

No additional audit model, regex question splitting, arbitrary tree-depth cap,
or reduced evidence-quality gate is introduced. Full live-model replay is still
needed to measure actual token reduction and assess model behavior.
