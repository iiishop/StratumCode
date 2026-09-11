from __future__ import annotations

import json
from dataclasses import dataclass, field


LOOKUP = "begin_light_lookup"
FINISH = "finish_light_response"
DELEGATES = {"run_investigation", "run_write_loop", "run_full_pipeline", "run_subagent"}


def _schema(name: str, description: str, properties: dict) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": list(properties), "additionalProperties": False},
    }}


def control_schemas() -> list[dict]:
    return [
        _schema(LOOKUP, "Enter local lookup for ONE bounded factual question or a shallow overview. "
                "For causal debugging, intermittent failures, failed previous fixes or deep reviews, "
                "call run_investigation instead. This selects a workflow, not a research checklist.", {
            "question": {"type": "string", "description": "The specific lookup question, not a broad debugging goal."},
        }),
        _schema(FINISH, "The only answer-delivery action. Return the user-facing conclusion, not deliberation. "
                "Name any remaining in-scope gaps. Completion status is derived by the runtime from actual outcomes. "
                "The runtime attaches provenance automatically; do not copy internal evidence IDs.", {
            "answer": {"type": "string"},
            "unresolved_questions": {"type": "array", "items": {"type": "string"}},
        }),
    ]


@dataclass
class LightCoordinator:
    mode: str = "coordinate"
    question: str = ""
    sources: set[str] = field(default_factory=set)
    # Repeating the same delegated task replaces its outcome, not unrelated failures.
    delegated: dict[tuple[str, str], tuple[str, bool]] = field(default_factory=dict)
    completion: dict = field(default_factory=dict)
    implementation_required: bool = False
    implementation_completed: bool = False
    delegate_gaps: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    def schemas(self, available: list[dict]) -> list[dict]:
        controls = control_schemas()
        if self.mode != "coordinate":
            controls = [s for s in controls if s["function"]["name"] != LOOKUP]
        return controls + [s for s in available
                           if self.mode == "lookup" or s["function"]["name"] in DELEGATES]

    def validate_batch(self, calls: list[dict], available: list[dict]) -> None:
        allowed = {s["function"]["name"] for s in self.schemas(available)} | {"load_skill"}
        names = [(call.get("function") or {}).get("name") for call in calls]
        ids = [call.get("id") for call in calls]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("Light actions require unique tool call IDs")
        if any(name not in allowed for name in names):
            raise ValueError(f"Tool not available in Light workflow {self.mode}")
        if len(calls) > 1 and any(name in {LOOKUP, FINISH} | DELEGATES for name in names):
            raise ValueError("Workflow transitions, delegation and answer delivery must be separate actions")
        # Validate the whole batch before any side effect, including delegated writes.
        for call in calls:
            raw = call["function"].get("arguments")
            arguments = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object")

    def begin_lookup(self, arguments: dict) -> dict:
        question = arguments.get("question")
        if self.mode != "coordinate" or not isinstance(question, str) or not question.strip():
            raise ValueError("Local lookup requires a single explicit question before delegation")
        self.mode, self.question = "lookup", question.strip()
        return {"mode": self.mode, "question": self.question,
                "instruction": "Gather evidence for this question, then finish or delegate unresolved investigation."}

    def observe(self, call: dict, output: str) -> None:
        name = call["function"]["name"]
        call_id = call["id"]
        if name == "load_skill":
            return
        successful = not output.startswith("[error]")
        if successful:
            self.sources.add(call_id)
        if name not in DELEGATES:
            return
        self.mode = "delegated"
        raw = call["function"]["arguments"]
        args = json.loads(raw) if isinstance(raw, str) else raw
        key = (name, str(args.get("message") or args.get("task") or ""))
        try:
            result = json.loads(output)
        except (TypeError, ValueError):
            result = {}
        complete = successful and isinstance(result, dict) and _delegate_complete(name, result)
        if isinstance(result, dict):
            analysis = result.get("analysis")
            if isinstance(analysis, dict) and analysis.get("execution_mode") == "implement":
                self.implementation_required = True
        if name in {"run_write_loop", "run_full_pipeline"}:
            self.implementation_required = True
            self.implementation_completed = complete
        self.delegated[key] = (call_id, complete)
        investigation = result.get("investigation", {}) if isinstance(result, dict) else {}
        gaps = []
        if isinstance(investigation, dict) and not complete:
            gaps = [str(n.get("question")) for n in investigation.get("unknowns", [])
                    if isinstance(n, dict) and n.get("question")]
            gaps.extend(str(q) for q in investigation.get("open_questions", []) if q)
            if investigation.get("recovery_reason"):
                gaps.append(str(investigation["recovery_reason"]))
        self.delegate_gaps[key] = gaps or ([] if complete else [f"{name}: delegated work is incomplete"])

    def finish(self, arguments: dict) -> str:
        answer = arguments.get("answer")
        sources = sorted(self.sources)
        gaps = arguments.get("unresolved_questions")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Answer delivery requires a nonempty answer")
        if not isinstance(gaps, list) or any(not isinstance(i, str) or not i.strip() for i in gaps):
            raise ValueError("unresolved_questions must be an array of nonempty questions")
        gaps = list(gaps)
        for key, (_, done) in self.delegated.items():
            if not done:
                gaps.extend(self.delegate_gaps.get(key, ["Delegated work is incomplete"]))
        if self.mode == "lookup" and not sources:
            gaps.append("Local lookup has no observed tool evidence")
        if self.implementation_required and not self.implementation_completed:
            gaps.append("Requested implementation has not completed validation")
        gaps = list(dict.fromkeys(gaps))
        status = "partial" if gaps else "complete"
        self.completion = {"status": status, "source_ids": sources, "unresolved_questions": gaps}
        if gaps:
            answer = answer.strip() + "\n\n---\n本次尚未完成的事项：\n" + "\n".join(f"- {gap}" for gap in gaps)
        return answer.strip()


def _delegate_complete(name: str, result: dict) -> bool:
    if result.get("error"):
        return False
    if name == "run_investigation":
        investigation = result.get("investigation") or {}
        if not isinstance(investigation, dict):
            return False
        audit = investigation.get("audit_result") or {}
        unknowns = investigation.get("unknowns", [])
        return bool(isinstance(audit, dict) and audit.get("complete")
                    and all(isinstance(audit.get(d), dict) and audit[d].get("complete")
                            for d in ("requirement", "solution"))
                    and not investigation.get("open_questions")
                    and not investigation.get("user_decisions_required")
                    and not investigation.get("runtime_failure")
                    and isinstance(unknowns, list)
                    and not unknowns)
    if name in {"run_write_loop", "run_full_pipeline"}:
        validation = result.get("validation_result")
        return bool(isinstance(validation, dict) and validation.get("verdict") == "passed"
                    and (name != "run_write_loop" or result.get("state") == "completed"))
    return result.get("completed") is True
