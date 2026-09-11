from __future__ import annotations

import json
from copy import deepcopy

from ..status.task_updates import _apply_task_updates
from .task_authoring import author_task_analysis, contextual_fallback_task_analysis
from .task_seed import light_task_events


LIGHT_AUTHORED_TASK_TOOLS = {"run_investigation", "run_write_loop"}


class LightTaskState:
    def __init__(self, analysis: dict) -> None:
        self.origin_message = str(analysis.get("origin_message") or "")
        self.analyses: dict[str, dict] = {}
        self.active_id = ""
        self.published_ids: set[str] = set()
        self.delegate_artifacts: dict[str, dict] = {}
        self.set_analysis(analysis)

    def retain_delegate(self, call: dict, output: str) -> str:
        if call.get("function", {}).get("name") not in {"run_investigation", "run_full_pipeline"}:
            return ""
        try:
            payload = json.loads(output)
        except (TypeError, ValueError):
            return ""
        if not isinstance(payload, dict) or not isinstance(payload.get("investigation"), dict):
            return ""
        source = str(call.get("id") or "")
        analysis = payload.get("analysis") or self.current()
        if not source or not isinstance(analysis, dict):
            return ""
        self.delegate_artifacts[source] = deepcopy({"analysis": analysis, "investigation": payload["investigation"]})
        return source

    def set_analysis(self, analysis: dict) -> None:
        analysis_id = str(analysis.get("id") or "").strip()
        if not analysis_id:
            return
        self.analyses[analysis_id] = deepcopy(analysis)
        self.active_id = analysis_id

    def observe(self, event: dict) -> None:
        if event.get("op") != "start":
            return
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        event_type = event.get("event")
        if event_type == "task_analysis":
            self.set_analysis(data)
            return
        if event_type == "task_update":
            self.apply_update(data)

    def apply_update(self, update: dict) -> None:
        analysis = self.current()
        analysis_id = str(update.get("analysis_id") or self.active_id).strip()
        if analysis is None or not analysis_id:
            return
        items = update.get("items")
        if not isinstance(items, list):
            return
        applied = _apply_task_updates(
            analysis_id,
            analysis.get("task_updates", []),
            items,
            [],
        )
        analysis["task_updates"] = applied["items"]
        self.analyses[analysis_id] = analysis
        self.active_id = analysis_id

    def current(self) -> dict | None:
        return self.analyses.get(self.active_id)

    def render(self) -> str:
        analysis = self.current()
        if analysis is None:
            return ""
        items = analysis.get("task_updates")
        payload = {
            "task_analysis_id": analysis.get("id", ""),
            "intent": analysis.get("intent", {}),
            "execution_mode": analysis.get("execution_mode", ""),
            "tasks": [{key: item[key] for key in ("id", "kind", "text", "status", "parent_id") if key in item}
                      for item in items if isinstance(item, dict)] if isinstance(items, list) else [],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def publish_events_for_tool(
        self,
        tool_name: str,
        *,
        messages: list[dict],
        provider: dict,
        model: str,
    ) -> tuple[list[dict], dict | None]:
        if tool_name not in LIGHT_AUTHORED_TASK_TOOLS:
            return [], None
        analysis = self.current()
        if analysis is None:
            return [], None
        analysis_id = str(analysis.get("id") or "").strip()
        if not analysis_id or analysis_id in self.published_ids:
            return [], None
        authored, assistant = author_task_analysis(
            base_analysis=analysis,
            messages=messages,
            provider=provider,
            model=model,
        )
        self.set_analysis(authored)
        self.published_ids.add(analysis_id)
        return light_task_events(authored), assistant

    def fallback_events_for_tool(self, tool_name: str, *, messages: list[dict]) -> list[dict]:
        if tool_name not in LIGHT_AUTHORED_TASK_TOOLS:
            return []
        analysis = self.current()
        if analysis is None:
            return []
        analysis_id = str(analysis.get("id") or "").strip()
        if not analysis_id or analysis_id in self.published_ids:
            return []
        analysis = contextual_fallback_task_analysis(
            base_analysis=analysis,
            messages=messages,
            tool_name=tool_name,
        )
        self.set_analysis(analysis)
        self.published_ids.add(analysis_id)
        return light_task_events(analysis)
