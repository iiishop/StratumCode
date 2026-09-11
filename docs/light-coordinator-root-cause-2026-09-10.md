# Light Agent Completion and Routing

## Incident Evidence

Export: 2026-09-09T18:07:52.785Z, final conversation.
The foreground agent made 15 local tool calls without delegating. Its last
generation used 32,000 output tokens. The 131,409-character final answer was
identical to the last thinking event, repeated `reconsider` 299 times, and ended
inside a code fragment. It was published as answered and used for memory extraction.

The export lacks the original provider response and finish_reason. Hitting an
output limit is strongly suggested, not proven. It does not establish whether
thinking was disabled, which sampling behavior caused repetition, or whether the
provider returned reasoning in content. No claims about those hidden states are made.

## Sources

- [DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/):
  Distinguishes content/reasoning_content and stop/length/tool_calls/resource
  interruption. A stop is a generation endpoint, not a task-completion guarantee.
  Documents required tool selection and runtime argument validation.
- [Anthropic, Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents):
  Separates routing, orchestration and execution. Supports using workflows for
  explicit control boundaries instead of one unrestricted prompt-response loop.

## Confirmed Program Defects

1. No tool call meant success, even for unfinished prose. There was no explicit
   answer-delivery action or linkage to actual delegated task outcomes.
2. All discovery tools were available immediately; coordinator and investigator
   responsibilities were separated only in prompt text.
3. Assistant content/reasoning was duplicated into progress and fed back into the
   free-running conversation, and the final content became answer memory.
4. A thinking/tool_choice or missing reasoning replay error could globally disable
   thinking for a provider/model across subsequent tasks. This is a confirmed code
   defect, not a proven trigger of this specific incident.

## Implemented Boundaries

- Start with explicit answer delivery, bounded-lookup selection, or delegation.
  Local discovery is enabled only in lookup mode and unavailable after delegation.
- finish_light_response is the sole answer exit. Prose with stop cannot finish.
  Transitions, delegation and finish run alone; validate the batch before execution.
- Completion cites actual successful tool-call IDs. Incomplete delegated tasks
  block complete delivery. Implementation requires passed validation, not just
  successful investigation. Partial delivery names gaps and preserves conversation
  memory without final-answer long-term extraction.
- Reject truncated/interrupted/filtered/missing-action generations before side
  effects. Removed the previous automatic generation recovery rather than adding
  another retry or an output-length cap.
- Keep required provider reasoning in tool history. Progress shows actions and
  protocol status, not reasoning transcripts or another copy of the answer.
- Tool-choice incompatibility adapts only that request to auto; runtime action
  validation remains in effect. Never silently disable thinking globally.

## Limits and Verification

These are control-flow and completion invariants, not a semantic truth detector.
The model can still misclassify a complex task as a lookup, provide weak evidence,
or put bad prose in a valid delivery action. No claim is made that this eliminates
all model repetition, hallucination or provider-side faults. No regex detector,
extra audit model, higher output limit or fixed investigation round cap was added.

Ordinary answers still require only one model decision; local lookup selection
costs one additional decision. Deep investigation uses the existing investigator.
Historical polluted memory is not deleted by these code changes. Actual quality,
latency and token savings require live-model replay; offline protocol checks alone
cannot establish them.

Verification: 14 coordinator/stream checks and 4 targeted provider/reasoning checks
passed. The actual incident's 131,409-character output was also replayed as both a
stop and a length response: neither produced an answer, action execution, draft
publication or generation retry. Python compilation and git diff whitespace checks
passed. No full suite or live provider run was performed. Tests and the UML file
remain under the repository's existing ignored directories; no ignore rules changed.
