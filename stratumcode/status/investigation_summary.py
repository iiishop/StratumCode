from __future__ import annotations


class InvestigationSummaryService:
    def merged_investigation_summary(self, run) -> str:
        """Rebuild a final summary from accumulated investigation facts."""
        final = run.last_investigation or {}
        answers = [
            str(item.get("answer") or "").strip()
            for item in final.get("resolutions", [])
            if isinstance(item, dict)
            and item.get("status") == "resolved"
            and str(item.get("answer") or "").strip()
        ]
        if answers:
            return "\n\n".join(answers)
        if final.get("summary"):
            return str(final["summary"]).strip()
        return "Investigation complete."

    def final_summary_text(self, run) -> str:
        """Write the user-facing answer over all investigation rounds."""
        from .. import agent_runtime, model_settings

        facts = self.merged_investigation_summary(run)
        setting = model_settings.resolve(model_settings.SUMMARY_STAGE)
        if setting is None:
            return facts
        criteria_lines = [
            f"- {ac.get('text') or ac.get('id') or ''}"
            for ac in (run.analysis or {}).get("acceptance_criteria", [])
            if isinstance(ac, dict)
        ]
        criteria_text = "\n".join(criteria_lines) or "(none)"
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the final summarizer for a completed code investigation. "
                    "Write a clear, user-facing answer to the original question using ONLY "
                    "the verified facts below. Satisfy every acceptance criterion. "
                    "Do not invent new facts, do not mention the investigation process or "
                    "the tooling used. Write in the same language as the original question."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original question:\n{run.message}\n\n"
                    f"Acceptance criteria:\n{criteria_text}\n\n"
                    f"Verified facts from the investigation:\n{facts}"
                ),
            },
        ]
        try:
            assistant = agent_runtime.call_model(
                setting["provider"],
                setting["model_id"],
                messages,
                use_skills=False,
            )
        except Exception:
            return facts
        text = (assistant.get("content") or "").strip()
        return text or facts
