from __future__ import annotations

from ..agent_runtime import start_event
from .clearifying import queue_clearify, user_question_and_wait
from .investigation_context import InvestigationContextBuilder
from .investigation_contracts import MemoryContextPort
from .investigation_events import InvestigationEventConsumer
from .investigation_summary import InvestigationSummaryService
from .investigation_transitions import InvestigationTransitionPolicy
from .memory_context import LegacyRunMemoryPort
from .user_waiting import user_question_event


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

        if result.investigation is not None:
            run.last_investigation = result.investigation
            self.memory.record_stage_delta(run, "investigation", result.memory_delta)

        br_state = (result.investigation or {}).get("bugfix_readiness_state")
        if br_state:
            run.bugfix_readiness = br_state

        step_question = self._step_clearify_question(run)
        if step_question:
            yield from self._ask_and_continue(run, step_question)
            return

        blocked_question = self._blocked_task_question(run)
        if blocked_question:
            yield from self._ask_and_continue(run, blocked_question)
            return

        decision = self.transition_policy.decide(run, result)
        run.transition(decision.next_state, decision.reason)
        if result.pending_output and run.state not in {chat.ChatState.INVESTIGATING, chat.ChatState.FAILED}:
            yield self._investigation_facts_event(run)
            result.pending_output["data"]["content"] = self.summary.final_summary_text(run)
            yield result.pending_output

    def _ask_and_continue(self, run, question_event: dict):
        """同步等待用户回答 clearify，回答注入后重跑调查（断点续跑，不切状态）。"""
        from .. import chat

        data = question_event.get("data") or {}
        yield from user_question_and_wait(
            run,
            question=str(data.get("question") or "Which behavior should be used?"),
            reason=str(data.get("reason") or "Investigation requires a product decision."),
            unknown_id=str(data.get("unknown_id") or ""),
            checkpoint_phase="investigation_checkpoint",
            resume_state="investigating",
            extra={
                "analysis": run.analysis,
                "investigation": run.last_investigation or {},
            },
        )
        run.transition(chat.ChatState.INVESTIGATING, "User answered clearify; continuing investigation.")

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

    def _step_clearify_question(self, run) -> dict | None:
        investigation = run.last_investigation or {}
        step = investigation.get("step_result") if isinstance(investigation.get("step_result"), dict) else {}
        if step.get("next_step") == "clearify":
            target_ids = [
                self._unknown_tail(item)
                for item in step.get("target_unknown_ids", [])
                if self._unknown_tail(item)
            ]
            unknowns = [
                item for item in (run.analysis or {}).get("unknowns", [])
                if isinstance(item, dict)
            ] + [
                item for item in investigation.get("unknowns", [])
                if isinstance(item, dict)
            ]
            for unknown_id in target_ids:
                unknown = next(
                    (
                        item for item in unknowns
                        if self._unknown_tail(item.get("id")) == unknown_id
                        and item.get("type") == "product_decision"
                    ),
                    None,
                )
                if not unknown:
                    continue
                question = str(unknown.get("question") or step.get("continue_reason") or "").strip()
                if not question:
                    continue
                queue_clearify(
                    run,
                    question,
                    reason=str(step.get("continue_reason") or "Investigation requires a product decision."),
                    unknown_id=str(unknown.get("id") or unknown_id),
                )
                return user_question_event(
                    run,
                    question=question,
                    reason=str(step.get("continue_reason") or "Investigation requires a product decision."),
                    unknown_id=str(unknown.get("id") or unknown_id),
                    checkpoint_phase="investigation_checkpoint",
                    resume_state="investigating",
                    extra={
                        "analysis": run.analysis,
                        "investigation": run.last_investigation or {},
                    },
                )
        # 系统兜底：调查结束后仍有 blocking 的 product_decision 未解决 → 直接 clearify。
        # product_decision 没有"代码证据"可查，模型死磕调查只会无限 continue（曾因此死循环）。
        return self._pending_product_decision_question(run)

    def _pending_product_decision_question(self, run) -> dict | None:
        investigation = run.last_investigation or {}
        unknowns = [
            item for item in (run.analysis or {}).get("unknowns", [])
            if isinstance(item, dict)
        ] + [
            item for item in investigation.get("unknowns", [])
            if isinstance(item, dict)
        ]
        resolved_ids = self._recorded_resolved_ids(investigation)
        for unknown in unknowns:
            if str(unknown.get("type") or "").strip().casefold() != "product_decision":
                continue
            if not unknown.get("blocking"):
                continue
            unknown_id = str(unknown.get("id") or "").strip()
            if not unknown_id or self._unknown_tail(unknown_id) in resolved_ids:
                continue
            question = str(unknown.get("question") or unknown.get("text") or "").strip()
            if not question:
                continue
            reason = str(unknown.get("why") or "Investigation requires a product decision.")
            queue_clearify(run, question, reason=reason, unknown_id=unknown_id)
            return user_question_event(
                run,
                question=question,
                reason=reason,
                unknown_id=unknown_id,
                checkpoint_phase="investigation_checkpoint",
                resume_state="investigating",
                extra={
                    "analysis": run.analysis,
                    "investigation": run.last_investigation or {},
                },
            )
        return None

    def _recorded_resolved_ids(self, investigation: dict) -> set[str]:
        resolved = set()
        for record in investigation.get("resolutions", []) if isinstance(investigation.get("resolutions"), list) else []:
            if not isinstance(record, dict):
                continue
            if str(record.get("status") or "") not in ("resolved", "partially_resolved", "deferred"):
                continue
            tail = self._unknown_tail(str(record.get("unknown_id") or ""))
            if tail:
                resolved.add(tail)
        return resolved

    @staticmethod
    def _unknown_tail(value) -> str:
        text = str(value or "").strip()
        return text.rsplit(":", 1)[-1].strip() if text else ""

    def _investigation_facts_event(self, run) -> dict:
        return start_event(
            f"{run.analysis.get('id', 'run')}-investigation-facts",
            "investigation_facts",
            {"content": self.summary.merged_investigation_summary(run)},
        )
