from __future__ import annotations

from ..agent_runtime import start_event
from .clearifying import queue_clearify
from .investigation_context import InvestigationContextBuilder
from .investigation_contracts import MemoryContextPort
from .investigation_events import InvestigationEventConsumer
from .investigation_summary import InvestigationSummaryService
from .investigation_transitions import InvestigationTransitionPolicy
from .memory_context import LegacyRunMemoryPort
from .user_waiting import prepared_user_question_event, user_question_event


class InvestigatingUseCase:
    def __init__(
        self,
        *,
        memory: MemoryContextPort | None = None,
        context_builder: InvestigationContextBuilder | None = None,
        event_consumer: InvestigationEventConsumer | None = None,
        transition_policy: InvestigationTransitionPolicy | None = None,
        summary: InvestigationSummaryService | None = None,
    ) -> None:
        self.memory = memory or LegacyRunMemoryPort()
        self.context_builder = context_builder or InvestigationContextBuilder()
        self.event_consumer = event_consumer or InvestigationEventConsumer()
        self.transition_policy = transition_policy or InvestigationTransitionPolicy()
        self.summary = summary or InvestigationSummaryService()

    def prepare(self, run):
        yield from self.context_builder.prepare(run)

    def run(self, run):
        from .. import chat

        memory_snapshot = self.memory.current_snapshot(run, "investigation")
        input_data = self.context_builder.build(run, memory_snapshot)
        result = yield from self.event_consumer.consume(run, input_data)

        if result.investigation is None:
            run.transition(chat.ChatState.FAILED, "Investigation failed.")
            return

        run.last_investigation = result.investigation
        self.memory.record_stage_delta(run, "investigation", result.memory_delta)

        br_state = result.investigation.get("bugfix_readiness_state")
        if br_state:
            run.bugfix_readiness = br_state

        if result.pending_question:
            yield self._prepare_pending_question(run, result.pending_question)
            self._queue_pending_question(run, result.pending_question)
            run.transition(chat.ChatState.WAITING_FOR_USER, "Investigation queued a clearify decision.")
            return

        blocked_question = self._blocked_task_question(run)
        if blocked_question:
            yield blocked_question
            run.transition(chat.ChatState.WAITING_FOR_USER, "Investigation queued a clearify decision.")
            return

        decision = self.transition_policy.decide(run, result)
        run.transition(decision.next_state, decision.reason)
        if result.pending_output and run.state != chat.ChatState.INVESTIGATING:
            yield self._investigation_facts_event(run)
            result.pending_output["data"]["content"] = self.summary.final_summary_text(run)
            yield result.pending_output

    def _queue_pending_question(self, run, pending_question: dict) -> None:
        data = pending_question.get("data") or {}
        queue_clearify(
            run,
            data.get("question") or "Which behavior should be used?",
            reason=data.get("reason") or "Investigation requires a product decision.",
            unknown_id=str(data.get("unknown_id") or ""),
        )

    def _prepare_pending_question(self, run, pending_question: dict) -> dict:
        return prepared_user_question_event(
            pending_question,
            checkpoint_phase="investigation_checkpoint",
            resume_state="investigating",
            extra={
                "analysis": run.analysis,
                "investigation": run.last_investigation or {},
            },
        )

    def _blocked_task_question(self, run) -> dict | None:
        for item in (run.last_investigation or {}).get("task_updates", []):
            if not isinstance(item, dict) or item.get("status") != "blocked":
                continue
            question = str(item.get("text") or item.get("question") or "").strip()
            if not question:
                continue
            queue_clearify(
                run,
                question,
                reason=str(item.get("reason") or "Investigation requires a product decision."),
                unknown_id=str(item.get("id") or ""),
            )
            return user_question_event(
                run,
                question=question,
                reason=str(item.get("reason") or "Investigation requires a product decision."),
                unknown_id=str(item.get("id") or ""),
                checkpoint_phase="investigation_checkpoint",
                resume_state="investigating",
                extra={
                    "analysis": run.analysis,
                    "investigation": run.last_investigation or {},
                },
            )
        return None

    def _investigation_facts_event(self, run) -> dict:
        return start_event(
            f"{run.analysis.get('id', 'run')}-investigation-facts",
            "investigation_facts",
            {"content": self.summary.merged_investigation_summary(run)},
        )
