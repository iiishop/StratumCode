from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator
from urllib.request import Request, urlopen
from uuid import uuid4

from . import app_settings, design_planner, hypothesis_verifier, implementation_runner, investigator, model_settings, patch_planner, prompt, providers, sessions
from .agent_runtime import (
    MAX_MODEL_OUTPUT_TOKENS,
    call_model as _call_model,
    content_text as _content_text,
    start_event,
    usage_delta as _usage_delta,
)
from .tools import registry

MAX_AGENT_ROUNDS = hypothesis_verifier.MAX_AGENT_ROUNDS
MAX_EMPTY_TOOL_ROUNDS = hypothesis_verifier.MAX_EMPTY_TOOL_ROUNDS
MAX_ANALYZER_ATTEMPTS = 3
TASK_INTENT_TYPES = {"feature", "bugfix", "refactor", "question", "investigation", "other"}
TASK_CERTAINTIES = {"certain", "uncertain", "guess"}
TASK_CLUE_KINDS = {"file", "line", "symbol", "route", "other"}
TASK_UNKNOWN_TYPES = {
    "code_fact",
    "doc_fact",
    "runtime_fact",
    "product_decision",
    "engineering_decision",
    "risk",
    "deferred",
}
TASK_UNKNOWN_TYPE_ALIASES = {
    "codebase_fact": "code_fact",
    "user_decision": "product_decision",
}
TASK_UNKNOWN_STRATEGIES = {"investigate_project", "ask_user", "deferred"}
IMPLEMENTATION_INTENT_TYPES = {"feature", "bugfix", "refactor"}


def analyze_task(message: str, context: list[str], workspace_dir: str, session_context: dict | None = None) -> dict:
    setting = (
        model_settings.resolve(model_settings.DEFAULT_STAGE)
        or model_settings.resolve(model_settings.EVIDENCE_STAGE)
    )
    if setting is None:
        raise ValueError(
            "No model configured for task analysis. Configure a default or evidence model in Providers."
        )

    provider = setting["provider"]
    model = setting["model_id"]
    last_error = ""
    best_partial = None
    for _ in range(MAX_ANALYZER_ATTEMPTS):
        messages = [
            {"role": "system", "content": prompt.build_task_analyzer(app_settings.get_output_language())},
            {
                "role": "user",
                "content": prompt.build_task_analyzer_user(
                    message=message,
                    directory=workspace_dir,
                    context=context + _session_context_lines(session_context),
                    error=last_error,
                ),
            },
        ]
        assistant = _call_model(
            provider,
            model,
            messages,
            tools=[],
        )
        try:
            if assistant.get("tool_calls"):
                raise ValueError("tool calls are not allowed")
            analysis = _validate_task_analysis(_json_object(_content_text(assistant.get("content"))))
            analysis["model"] = model
            analysis["provider"] = provider["name"]
            analysis["suggested_first_tools"] = _suggested_first_tools(analysis)
            analysis["evidence_hypothesis"] = _analysis_hypothesis(message, analysis)
            return analysis
        except ValueError as exc:
            last_error = str(exc)
            best_partial = _partial_task_analysis(_content_text(assistant.get("content")), message, context, last_error) or best_partial
    analysis = best_partial or _fallback_task_analysis(message, context)
    analysis["model"] = model
    analysis["provider"] = provider["name"]
    analysis["analyzer_error"] = (
        f"task analyzer partial recovery after invalid JSON: {last_error}"
        if best_partial else
        f"task analyzer fallback after invalid JSON: {last_error}"
    )
    analysis["suggested_first_tools"] = _suggested_first_tools(analysis)
    analysis["evidence_hypothesis"] = _analysis_hypothesis(message, analysis)
    return analysis


def _json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    if not text.startswith("{"):
        start = text.find("{")
        if start == -1:
            raise ValueError("response is not a JSON object")
        text = text[start:]
    try:
        data, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    return data


def _partial_task_analysis(raw: str, message: str, context: list[str], error: str) -> dict | None:
    try:
        data = _json_object(raw)
    except ValueError:
        return None
    fallback = _fallback_task_analysis(message, context)
    result = dict(fallback)

    intent = data.get("intent")
    if isinstance(intent, dict):
        summary = str(intent.get("summary") or "").strip()
        intent_type = str(intent.get("type") or fallback["intent"]["type"]).strip().casefold()
        if intent_type not in TASK_INTENT_TYPES:
            intent_type = fallback["intent"]["type"]
        if summary:
            result["intent"] = {"type": intent_type, "summary": summary}

    for key, parser in (
        ("acceptance_criteria", _acceptance_criteria),
        ("constraints", lambda value: _string_list(value, "constraints")),
        ("hypotheses", _hypotheses),
    ):
        if key in data:
            try:
                result[key] = parser(data.get(key))
            except ValueError:
                pass

    if "behavior_contract" in data:
        try:
            parsed = _behavior_contract(data.get("behavior_contract"))
            result["behavior_contract"] = {
                key: parsed.get(key) or fallback["behavior_contract"].get(key) or []
                for key in fallback["behavior_contract"]
            }
        except ValueError:
            pass

    if "scope" in data:
        try:
            parsed = _scope(data.get("scope"))
            result["scope"] = {
                key: parsed.get(key) or fallback["scope"].get(key) or []
                for key in fallback["scope"]
            }
        except ValueError:
            pass

    if "clues" in data:
        try:
            parsed = _clues(data.get("clues"))
            result["clues"] = _unique_clues(parsed + fallback["clues"])
        except ValueError:
            pass

    if "unknowns" in data:
        try:
            result["unknowns"] = _limited_unknowns(data.get("unknowns"), result.get("acceptance_criteria"))
        except ValueError:
            pass

    result["recovered_from_partial_analyzer_output"] = True
    result["partial_recovery_error"] = error
    return result


def _fallback_task_analysis(message: str, context: list[str]) -> dict:
    text = " ".join(str(message or "").split()).strip()
    lowered = text.casefold()
    intent_type = "question"
    if any(word in lowered for word in ("修复", "fix", "bug", "报错", "错误")):
        intent_type = "bugfix"
    elif any(word in lowered for word in ("实现", "添加", "增加", "修改", "支持", "create", "add", "implement", "change")):
        intent_type = "feature"
    clues = []
    for index, item in enumerate(context or [], start=1):
        value = str(item).strip()
        if value:
            clues.append({"kind": "file", "value": value, "path": value, "line": 0, "symbol": "", "note": "user-provided context"})
    return {
        "intent": {"type": intent_type, "summary": text[:160] or "Handle the user request."},
        "acceptance_criteria": [
            {"id": "AC1", "text": text[:220] or "The requested behavior is completed."},
        ],
        "behavior_contract": {
            "inputs": ["User request"],
            "outputs": ["Updated behavior or answer matching the request"],
            "success_behaviors": [text[:220] or "The request is satisfied."],
            "failure_behaviors": [],
            "boundaries": ["Do not add unrelated behavior."],
        },
        "constraints": [],
        "scope": {"in": [text[:220] or "Requested work"], "out": ["Unrelated changes"], "undecided": []},
        "hypotheses": [],
        "clues": clues,
        "unknowns": [
            {
                "id": "U1",
                "question": "Which existing files and project conventions are relevant to this request?",
                "blocking": True,
                "type": "code_fact",
                "why": "Implementation or answer must be grounded in the current workspace.",
                "resolution_strategy": "investigate_project",
                "acceptance_criteria_ids": ["AC1"],
            }
        ],
    }


def _validate_task_analysis(data: dict) -> dict:
    intent = data.get("intent")
    if not isinstance(intent, dict):
        raise ValueError("intent must be an object")
    intent_type = str(intent.get("type") or "other").strip().casefold()
    summary = str(intent.get("summary") or "").strip()
    if intent_type not in TASK_INTENT_TYPES:
        intent_type = "other"
    if not summary:
        raise ValueError("intent.summary is required")

    return {
        "intent": {"type": intent_type, "summary": summary},
        "acceptance_criteria": _acceptance_criteria(data.get("acceptance_criteria")),
        "behavior_contract": _behavior_contract(data.get("behavior_contract")),
        "constraints": _string_list(data.get("constraints"), "constraints"),
        "scope": _scope(data.get("scope")),
        "hypotheses": _hypotheses(data.get("hypotheses")),
        "clues": _clues(data.get("clues")),
        "unknowns": _limited_unknowns(data.get("unknowns"), data.get("acceptance_criteria")),
    }


def _acceptance_criteria(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("acceptance_criteria must be an array")
    items = []
    for index, raw in enumerate(value, start=1):
        if isinstance(raw, dict):
            text = str(raw.get("text") or raw.get("description") or "").strip()
            item_id = str(raw.get("id") or f"AC{index}").strip()
        else:
            text = str(raw).strip()
            item_id = f"AC{index}"
        if text:
            items.append({"id": item_id or f"AC{index}", "text": text})
    return items


def _behavior_contract(value) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("behavior_contract must be an object")
    return {
        "inputs": _string_list(value.get("inputs"), "behavior_contract.inputs"),
        "outputs": _string_list(value.get("outputs"), "behavior_contract.outputs"),
        "success_behaviors": _string_list(value.get("success_behaviors"), "behavior_contract.success_behaviors"),
        "failure_behaviors": _string_list(value.get("failure_behaviors"), "behavior_contract.failure_behaviors"),
        "boundaries": _string_list(value.get("boundaries"), "behavior_contract.boundaries"),
    }


def _scope(value) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("scope must be an object")
    return {
        "in": _string_list(value.get("in"), "scope.in"),
        "out": _string_list(value.get("out"), "scope.out"),
        "undecided": _string_list(value.get("undecided"), "scope.undecided"),
    }


def _limited_unknowns(value, criteria=None) -> list[dict]:
    unknowns = _unknowns(value, criteria)
    if len(unknowns) > 5:
        raise ValueError("unknowns must contain at most 5 items")
    return unknowns


def _unknowns(value, criteria=None) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("unknowns must be an array")
    criteria_ids = [item["id"] for item in _acceptance_criteria(criteria)]
    items = []
    for index, raw in enumerate(value, start=1):
        if isinstance(raw, dict):
            question = str(raw.get("question") or raw.get("text") or raw.get("description") or "").strip()
            item_id = str(raw.get("id") or f"U{index}").strip()
            unknown_type = str(raw.get("type") or "code_fact").strip().casefold()
            strategy = str(raw.get("resolution_strategy") or "investigate_project").strip().casefold()
            accepted_ids = raw.get("acceptance_criteria_ids")
            if not isinstance(accepted_ids, list):
                accepted_ids = []
            accepted_ids = [str(item).strip() for item in accepted_ids if str(item).strip()]
            blocking = bool(raw.get("blocking", True))
            why = str(raw.get("why") or raw.get("reason") or "").strip()
        else:
            question = str(raw).strip()
            item_id = f"U{index}"
            unknown_type = "code_fact"
            strategy = "investigate_project"
            accepted_ids = criteria_ids
            blocking = True
            why = ""
        if not question:
            continue
        unknown_type = TASK_UNKNOWN_TYPE_ALIASES.get(unknown_type, unknown_type)
        if unknown_type not in TASK_UNKNOWN_TYPES:
            unknown_type = "code_fact"
        if strategy not in TASK_UNKNOWN_STRATEGIES:
            strategy = "investigate_project"
        items.append({
            "id": item_id or f"U{index}",
            "question": question,
            "blocking": blocking,
            "type": unknown_type,
            "why": why,
            "resolution_strategy": strategy,
            "acceptance_criteria_ids": accepted_ids,
        })
    return items


def _string_list(value, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return [str(item).strip() for item in value if str(item).strip()]


def _hypotheses(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("hypotheses must be an array")
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("hypotheses items must be objects")
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        certainty = str(raw.get("certainty") or "uncertain").strip().casefold()
        if certainty not in TASK_CERTAINTIES:
            certainty = "uncertain"
        items.append({"text": text, "certainty": certainty})
    return items


def _clues(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("clues must be an array")
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("clues items must be objects")
        value_text = str(raw.get("value") or raw.get("path") or raw.get("symbol") or "").strip()
        if not value_text:
            continue
        kind = str(raw.get("kind") or "other").strip().casefold()
        if kind not in TASK_CLUE_KINDS:
            kind = "other"
        line = raw.get("line")
        try:
            line = int(line) if line not in (None, "") else None
        except (TypeError, ValueError):
            line = None
        items.append({
            "kind": kind,
            "value": value_text,
            "path": str(raw.get("path") or "").strip(),
            "line": line if line and line > 0 else None,
            "symbol": str(raw.get("symbol") or "").strip(),
            "note": str(raw.get("note") or "").strip(),
        })
    return items


def _unique_clues(items: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for item in items:
        key = (
            str(item.get("kind") or ""),
            str(item.get("value") or ""),
            str(item.get("path") or ""),
            str(item.get("line") or ""),
            str(item.get("symbol") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _analysis_hypothesis(message: str, analysis: dict) -> str:
    if analysis["hypotheses"]:
        return analysis["hypotheses"][0]["text"]
    return ""


def _suggested_first_tools(analysis: dict) -> list[dict]:
    calls = []
    seen = set()
    for clue in analysis["clues"]:
        path = clue.get("path") or _path_from_text(clue.get("value", ""))
        if path:
            line = clue.get("line") or _line_from_text(clue.get("value", "")) or 1
            args = {
                "path": path,
                "start_line": max(1, line),
                "end_line": max(80, line + 80),
            }
            call = {"tool": "read", "arguments": args}
        else:
            pattern = clue.get("symbol") or clue.get("value", "")
            if not pattern:
                continue
            call = {"tool": "grep", "arguments": {"pattern": pattern}}
        key = json.dumps(call, sort_keys=True)
        if key not in seen:
            seen.add(key)
            calls.append(call)
        if len(calls) >= 4:
            break
    if not calls:
        calls.append({"tool": "glob", "arguments": {"pattern": "*"}})
    return calls


def _ensure_task_contract(analysis: dict) -> dict:
    analysis.setdefault("constraints", [])
    analysis.setdefault("hypotheses", [])
    analysis.setdefault("clues", [])
    analysis.setdefault("suggested_first_tools", [])
    analysis.setdefault("acceptance_criteria", [])
    analysis.setdefault("behavior_contract", {})
    analysis.setdefault("scope", {"in": [], "out": [], "undecided": []})
    analysis["acceptance_criteria"] = _acceptance_criteria(analysis.get("acceptance_criteria"))
    analysis["behavior_contract"] = _behavior_contract(analysis.get("behavior_contract"))
    analysis["scope"] = _scope(analysis.get("scope"))
    analysis["unknowns"] = _limited_unknowns(analysis.get("unknowns"), analysis.get("acceptance_criteria"))
    return analysis


def _path_from_text(value: str) -> str:
    match = re.search(r"[\w./\\-]+\.[A-Za-z0-9_]+", value or "")
    return match.group(0).replace("\\", "/") if match else ""


def _line_from_text(value: str) -> int | None:
    match = re.search(r"(?:line|L|:)\s*(\d+)", value or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def _analysis_context(analysis: dict) -> list[str]:
    analysis = _ensure_task_contract(analysis)
    lines = [f"Task intent ({analysis['intent']['type']}): {analysis['intent']['summary']}"]
    lines.extend(
        f"Acceptance criterion {item['id']}: {item['text']}"
        for item in analysis.get("acceptance_criteria", [])
    )
    behavior = analysis.get("behavior_contract", {})
    for key, label in (
        ("inputs", "Behavior input"),
        ("outputs", "Behavior output"),
        ("success_behaviors", "Success behavior"),
        ("failure_behaviors", "Failure behavior"),
        ("boundaries", "Boundary"),
    ):
        lines.extend(f"{label}: {item}" for item in behavior.get(key, []))
    lines.extend(f"Constraint: {item}" for item in analysis["constraints"])
    scope = analysis.get("scope", {})
    lines.extend(f"In scope: {item}" for item in scope.get("in", []))
    lines.extend(f"Out of scope: {item}" for item in scope.get("out", []))
    lines.extend(f"Undecided scope: {item}" for item in scope.get("undecided", []))
    lines.extend(
        f"Assumption to verify ({item['certainty']}): {item['text']}"
        for item in analysis["hypotheses"]
    )
    for clue in analysis["clues"]:
        parts = [clue["kind"], clue["value"]]
        if clue.get("path"):
            parts.append(f"path={clue['path']}")
        if clue.get("line"):
            parts.append(f"line={clue['line']}")
        if clue.get("symbol"):
            parts.append(f"symbol={clue['symbol']}")
        lines.append("Clue to verify: " + " ".join(str(part) for part in parts if part))
    for call in analysis["suggested_first_tools"]:
        lines.append("Suggested first tool call: " + json.dumps(call, ensure_ascii=False))
    lines.extend(
        "Initial unknown {id} [{type}, {strategy}, blocking={blocking}]: {question}".format(
            id=item.get("id", ""),
            type=item.get("type", ""),
            strategy=item.get("resolution_strategy", ""),
            blocking=bool(item.get("blocking")),
            question=item.get("question", ""),
        )
        for item in analysis["unknowns"]
    )
    return lines


def analyzed_stream(
    message: str,
    context: list[str],
    workspace_dir: str,
    max_rounds: int | None = None,
    answer: dict | None = None,
    analysis: dict | None = None,
    prior_analysis: dict | None = None,
    session_id: int | None = None,
) -> Iterator[dict]:
    try:
        state = sessions.get(session_id)["state"] if session_id else {}
    except ValueError:
        state = {}
    session_context = _session_context(state)
    analyzer_session_context = _select_session_memory(message, None, session_context)
    workspace_context = _workspace_snapshot(workspace_dir)
    if workspace_context:
        context = workspace_context + context
    answer_context = _answer_context(answer)
    if answer_context:
        context = context + answer_context
    if analysis is None:
        analyze_stage = f"task-analysis-{uuid4().hex[:8]}"
        yield start_event(analyze_stage, "stage", {
            "name": "task_analysis",
            "label": "Analyze task requirements",
            "state": "running",
            "phase": "analyzing",
            "model": "",
            "context_length": 0,
            "provider": "",
            "inherited": False,
        })
        analysis = analyze_task(message, context, workspace_dir, session_context=analyzer_session_context)
        analysis.setdefault("model", "")
        analysis.setdefault("provider", "")
        yield {"op": "update", "id": analyze_stage, "patch": {
            "model": analysis.get("model", ""),
            "provider": analysis.get("provider", ""),
            "state": "done",
            "phase": "analyzed",
        }}
    analysis = _ensure_task_contract(analysis)
    if answer:
        analysis = {**analysis, "suggested_first_tools": []}
    selected_session_context = _select_session_memory(message, analysis, session_context)
    analysis.setdefault("id", f"task-{uuid4().hex[:8]}")
    analysis.setdefault("origin_message", message)
    _attach_session_relationship(analysis, session_context.get("tasks", []))
    seeded_tasks = _seed_task_updates(analysis, session_context.get("tasks", []))
    analysis["task_updates"] = _normalize_task_updates(analysis["id"], analysis.get("task_updates", []) + seeded_tasks, session_context.get("tasks", []))
    if not answer:
        yield start_event(f"task-analysis-{uuid4().hex[:8]}", "task_analysis", analysis)
    findings = _prior_findings(prior_analysis, answer)
    continuation_context = _continuation_context(answer, selected_session_context)
    last_investigation = None
    for event in investigator.investigation_stream(
        message=message,
        analysis=analysis,
        context=context + _analysis_context(analysis) + continuation_context,
        workspace_dir=workspace_dir,
        max_rounds=max_rounds,
        findings=findings,
        previous_observations=selected_session_context.get("observations", []),
        previous_knowledge=selected_session_context.get("knowledge", []),
    ):
        if event.get("event") == "task_update":
            event["data"]["items"] = _normalize_task_updates(
                analysis["id"],
                analysis.get("task_updates", []) + event["data"].get("items", []),
                [],
            )
            analysis["task_updates"] = event["data"]["items"]
        if event.get("op") == "done" and isinstance(event.get("investigation"), dict):
            last_investigation = event["investigation"]
            last_investigation["task_updates"] = _normalize_task_updates(
                analysis["id"],
                analysis.get("task_updates", []) + last_investigation.get("task_updates", []),
                session_context.get("tasks", []),
            )
            last_investigation["task_updates"] = _finalize_task_statuses(last_investigation["task_updates"], last_investigation)
            last_investigation["observations"] = _scoped_items(analysis["id"], last_investigation.get("observations", []))
            analysis["task_updates"] = last_investigation["task_updates"]
            yield start_event(f"{analysis['id']}-task-final", "task_update", {
                "analysis_id": analysis["id"],
                "items": analysis["task_updates"],
            })
        yield event
    if last_investigation and _investigation_allows_patch(last_investigation) and _wants_implementation(analysis):
        design_plan = None
        for event in design_planner.design_planning_stream(
            message=message,
            analysis=analysis,
            investigation=last_investigation,
            workspace_dir=workspace_dir,
        ):
            if event.get("op") == "done" and isinstance(event.get("design_plan"), dict):
                design_plan = event["design_plan"]
            yield event
        if design_plan:
            gap = design_planner.blocking_gap(design_plan)
            if gap:
                yield start_event(f"{analysis['id']}-design-question", "user_question", design_planner.user_question(
                    gap,
                    analysis_id=analysis["id"],
                    origin_message=message,
                ))
            else:
                patch_plan = None
                for event in patch_planner.patch_planning_stream(
                    message=message,
                    analysis=analysis,
                    investigation=last_investigation,
                    design_plan=design_plan,
                    workspace_dir=workspace_dir,
                ):
                    if event.get("op") == "done" and isinstance(event.get("patch_plan"), dict):
                        patch_plan = event["patch_plan"]
                    yield event
                if patch_plan:
                    for event in implementation_runner.implementation_stream(
                        message=message,
                        analysis=analysis,
                        patch_plan=patch_plan,
                        workspace_dir=workspace_dir,
                    ):
                        yield event
    if session_id and last_investigation:
        sessions.merge_investigation(
            session_id,
            last_investigation.get("task_updates", []),
            last_investigation.get("observations", []),
            investigation={
                "id": analysis["id"],
                "request": message,
                "summary": last_investigation.get("summary", ""),
            },
            knowledge=_beliefs_as_knowledge(analysis["id"], last_investigation.get("beliefs", [])),
        )


def evidence_stream(
    hypothesis: str,
    context: list[str],
    workspace_dir: str,
    max_rounds: int | None = None,
) -> Iterator[dict]:
    original_call_model = hypothesis_verifier._call_model
    hypothesis_verifier._call_model = _call_model
    try:
        yield from hypothesis_verifier.evidence_stream(
            hypothesis,
            context,
            workspace_dir,
            max_rounds=max_rounds,
        )
    finally:
        hypothesis_verifier._call_model = original_call_model


def _wants_implementation(analysis: dict) -> bool:
    return (analysis.get("intent") or {}).get("type") in IMPLEMENTATION_INTENT_TYPES


def _investigation_allows_patch(investigation: dict) -> bool:
    step = investigation.get("step_result") if isinstance(investigation.get("step_result"), dict) else {}
    return bool(investigation.get("ready_for_patch_planning") or step.get("next_step") == "write_code")


_discovery_tools = hypothesis_verifier._discovery_tools
_execute_tool = hypothesis_verifier._execute_tool


def _handle_agent_tool(**kwargs):
    events = []
    gen = hypothesis_verifier._handle_agent_tool(**kwargs)
    try:
        while True:
            events.append(next(gen))
    except StopIteration as exc:
        output, concluded, _ = exc.value
    return output, events, concluded


def test_stream(
    message: str,
    context: list[str],
    workspace_dir: str = ".",
    delay: float = 0.04,
) -> Iterator[dict]:
    """Backend-owned deterministic stream used by protocol tests."""
    def pause(multiplier=1):
        if delay:
            time.sleep(delay * multiplier)

    thinking = "test-thinking"
    yield start_event(thinking, "thinking", {"text": "", "done": False, "open": True})
    thought = f"Inspecting {', '.join(context) or 'the workspace'} for: {message}"
    for index in range(0, len(thought), 12):
        pause()
        yield {"op": "delta", "id": thinking, "field": "text", "value": thought[index:index + 12]}
    yield {"op": "update", "id": thinking, "patch": {"done": True}}

    yield start_event("test-analysis", "task_analysis", {
        "intent": {"type": "investigation", "summary": "Inspect the server tools and chat integration points."},
        "constraints": [],
        "hypotheses": [{"text": message, "certainty": "uncertain"}],
        "clues": [{"kind": "file", "value": "stratumcode/server.py", "path": "stratumcode/server.py", "line": 1, "symbol": "", "note": ""}],
        "unknowns": ["Which backend routes connect tool execution to chat streaming."],
        "suggested_first_tools": [{"tool": "read", "arguments": {"path": "stratumcode/server.py", "start_line": 1, "end_line": 90}}],
        "evidence_hypothesis": message,
        "model": "test",
        "provider": "test",
    })

    for call_id, name, params in (
        ("test-read", "read", {"path": "stratumcode/server.py", "start_line": 1, "end_line": 90}),
        ("test-grep", "grep", {"pattern": "/api/tools|registry\\.", "include": "*.py"}),
    ):
        tool = registry.get(name)
        pause(2)
        yield start_event(call_id, "tool", {
            "name": name,
            "description": tool.description,
            "status": "running",
            "open": False,
            "input": json.dumps(params, indent=2),
            "output": "",
        })
        _, result = _execute_tool(name, params, workspace_dir)
        yield {"op": "update", "id": call_id, "patch": {
            "status": "error" if result.title.startswith("[error]") else "done",
            "output": result.output,
            "title": result.title,
            "metadata": result.metadata,
        }}

    yield start_event("test-agent", "subagent", {
        "name": "@explore",
        "task": "Map the server routes and chat integration points",
        "status": "done",
        "result": "Found the request handler and provider integration.",
        "open": True,
    })
    yield start_event("test-diff", "diff", {
        "path": "stratumcode/server.py",
        "hunks": [{"type": "add", "lines": ["+        elif path == \"/api/chat\":"]}],
        "accepted": None,
    })
    yield start_event("test-output", "output", {
        "content": "The backend chat stream is connected.",
        "streaming": False,
    })
    yield {"op": "done"}


def provider_stream(provider_id: int, model: str, message: str, context: list[str]) -> Iterator[dict]:
    """Legacy direct provider stream retained for API compatibility."""
    provider = providers.get_saved(provider_id)
    if not provider:
        raise ValueError(f"unknown provider: {provider_id}")
    output_id = "provider-output"
    started = False
    request = Request(
        f"{provider['base_url'].rstrip('/')}/v1/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": message}],
            "stream": True,
        }).encode(),
        headers={
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            data = json.loads(payload)
            content = data.get("choices", [{}])[0].get("delta", {}).get("content")
            if content:
                if not started:
                    yield start_event(output_id, "output", {
                        "content": "",
                        "streaming": True,
                    })
                    started = True
                yield {"op": "delta", "id": output_id, "field": "content", "value": content}
    if started:
        yield {"op": "update", "id": output_id, "patch": {"streaming": False}}
    yield {"op": "done"}


def stream(request: dict, workspace_dir: str = ".") -> Iterator[dict]:
    message = request.get("message", "").strip()
    if not message:
        raise ValueError("message is required")
    context = request.get("context", [])
    if not isinstance(context, list) or not all(isinstance(path, str) for path in context):
        raise ValueError("context must be an array of file paths")
    if request.get("mode") == "test":
        return test_stream(message, context, workspace_dir)
    max_rounds = request.get("max_rounds")
    if max_rounds is not None:
        max_rounds = min(50, max(1, int(max_rounds)))
    answer = request.get("answer") if isinstance(request.get("answer"), dict) else None
    prior_analysis = request.get("analysis") if answer and isinstance(request.get("analysis"), dict) else None
    analysis = prior_analysis if answer else None
    if answer and str(answer.get("origin_message") or "").strip():
        message = str(answer["origin_message"]).strip()
    return analyzed_stream(
        message,
        context,
        workspace_dir,
        max_rounds=max_rounds,
        answer=answer,
        analysis=analysis,
        prior_analysis=prior_analysis,
        session_id=request.get("session_id"),
    )


def _workspace_snapshot(workspace_dir: str) -> list[str]:
    root = os.path.abspath(workspace_dir or ".")
    if not os.path.isdir(root):
        return []
    ignored = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}
    files: list[tuple[str, int]] = []
    dirs = 0
    for current, names, filenames in os.walk(root):
        names[:] = [name for name in names if name not in ignored and not name.startswith(".")]
        dirs += len(names)
        for filename in filenames:
            if filename.startswith("."):
                continue
            path = os.path.join(current, filename)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            rel = os.path.relpath(path, root).replace("\\", "/")
            files.append((rel, size))
            if len(files) >= 41:
                break
        if len(files) >= 41:
            break
    visible = files[:40]
    lines = [
        "Workspace snapshot:",
        f"- root: {root}",
        f"- visible files: {len(files) if len(files) < 41 else '40+'}",
        f"- visible directories: {dirs}",
    ]
    if visible:
        lines.append("- files:")
        lines.extend(f"  - {path} ({size} bytes{' / empty' if size == 0 else ''})" for path, size in visible)
    else:
        lines.append("- files: (none)")
    return lines


def _answer_context(answer: dict | None) -> list[str]:
    if not answer:
        return []
    question_id = str(answer.get("question_id") or answer.get("unknown_id") or "").strip()
    question = str(answer.get("question") or "").strip()
    selected = str(answer.get("selected_option_id") or "").strip()
    response = str(answer.get("response") or answer.get("text") or "").strip()
    if not response:
        return []
    lines = [app_settings.text("answer_context")]
    if question_id:
        lines.append(app_settings.text("answered_question_id", id=question_id))
    if question:
        lines.append(app_settings.text("question", question=question))
    if selected:
        lines.append(app_settings.text("selected_option", option=selected))
    lines.append(app_settings.text("user_answer", answer=response))
    return lines


def _continuation_context(answer: dict | None, session_context: dict) -> list[str]:
    if not answer:
        return []
    lines = [
        "Continuation after a user answer. Do not restart discovery.",
        "Do not run suggested starter tools again. Use previous observations and only inspect narrower missing evidence.",
    ]
    if session_context.get("observations") or session_context.get("knowledge"):
        lines.append("Previous observations/knowledge are available in this prompt and should be reused before any broad glob/read/code_nav call.")
    return lines


def _prior_findings(prior_analysis: dict | None, answer: dict | None) -> list[str]:
    """Extract prior investigation findings so the follow-up doesn't repeat work."""
    if not prior_analysis:
        return []
    lines = ["PRIOR INVESTIGATION FINDINGS (do not repeat any of this work):"]
    updates = prior_analysis.get("task_updates") if isinstance(prior_analysis.get("task_updates"), list) else []
    known = [item for item in updates if item.get("status") == "known"]
    if known:
        lines.append("Already confirmed:")
        lines.extend(f"  - {item['text']}" for item in known[:10] if item.get("text"))
    blocked = [item for item in updates if item.get("status") == "blocked"]
    if blocked:
        lines.append("Previously unresolved:")
        lines.extend(f"  - {item['text']}" for item in blocked[:5] if item.get("text"))
    if prior_analysis.get("intent"):
        lines.append(f"Original intent: {prior_analysis['intent'].get('summary', '')}")
    return lines


def _seed_task_updates(analysis: dict, existing: list[dict] | None = None) -> list[dict]:
    analysis = _ensure_task_contract(analysis)
    task_id = analysis["id"]
    root_goal = analysis.get("root_goal_id") or f"{task_id}:goal"
    items = []
    if not analysis.get("root_goal_id"):
        items.append(_task_item(f"{task_id}:goal", "goal", analysis["intent"]["summary"], "active"))
    elif analysis["intent"].get("summary"):
        items.append(_task_item(f"{task_id}:work", "work", analysis["intent"]["summary"], "added", parent_id=root_goal, trace=analysis.get("reused_context_ids", [])))
    items.extend(
        _task_item(f"{task_id}:C{index}", "constraint", text, "known", parent_id=root_goal)
        for index, text in enumerate(analysis.get("constraints", []), start=1)
    )
    items.extend(
        _task_item(f"{task_id}:{item['id']}", "acceptance", item["text"], "pending", parent_id=root_goal)
        for item in analysis.get("acceptance_criteria", [])
    )
    behavior = analysis.get("behavior_contract", {})
    behavior_rows = [
        ("BI", "behavior", text)
        for text in behavior.get("inputs", [])
    ] + [
        ("BO", "behavior", text)
        for text in behavior.get("outputs", [])
    ] + [
        ("BS", "behavior", text)
        for text in behavior.get("success_behaviors", [])
    ] + [
        ("BF", "behavior", text)
        for text in behavior.get("failure_behaviors", [])
    ] + [
        ("BB", "boundary", text)
        for text in behavior.get("boundaries", [])
    ]
    items.extend(
        _task_item(f"{task_id}:{prefix}{index}", kind, text, "pending", parent_id=root_goal)
        for index, (prefix, kind, text) in enumerate(behavior_rows, start=1)
    )
    items.extend(
        _task_item(f"{task_id}:H{index}", "hypothesis", item["text"], "unknown", parent_id=root_goal)
        for index, item in enumerate(analysis.get("hypotheses", []), start=1)
    )
    items.extend(
        _task_item(f"{task_id}:L{index}", "clue", clue.get("path") or clue.get("value"), "pending", parent_id=root_goal)
        for index, clue in enumerate(analysis.get("clues", []), start=1)
    )
    parent = f"{task_id}:work" if analysis.get("root_goal_id") else root_goal
    items.extend(
        _task_item(f"{task_id}:{item.get('id') or f'U{index}'}", "unknown", item.get("question", ""), "unknown", parent_id=parent)
        for index, item in enumerate(analysis.get("unknowns", []), start=1)
    )
    return _normalize_task_updates(task_id, items, existing or [])


def _task_item(item_id: str, kind: str, text: str, status: str, *, parent_id: str = "", trace: list[str] | None = None) -> dict:
    return {
        "id": item_id,
        "kind": kind,
        "text": text,
        "status": status,
        "parent_id": parent_id,
        "reason": "",
        "trace": trace or [],
    }


def _normalize_task_updates(analysis_id: str, updates: list[dict], existing: list[dict] | None = None) -> list[dict]:
    result = []
    prior = [dict(item) for item in existing or [] if isinstance(item, dict)]
    for raw in updates or []:
        if not isinstance(raw, dict) or not str(raw.get("text") or "").strip():
            continue
        item = dict(raw)
        item["id"] = _scoped_id(analysis_id, str(item.get("id") or ""))
        item.setdefault("kind", "unknown")
        item.setdefault("status", "updated")
        item.setdefault("reason", "")
        item["trace"] = [str(entry) for entry in item.get("trace", [])] if isinstance(item.get("trace"), list) else []
        matched = next((row for row in prior + result if _same_task(row, item)), None)
        if matched:
            if matched.get("kind") == "goal":
                item = dict(matched)
                item["trace"] = [str(entry) for entry in item.get("trace", [])] if isinstance(item.get("trace"), list) else []
                result.append(item)
                continue
            item["id"] = matched.get("id") or item["id"]
        index = next((i for i, row in enumerate(result) if _same_task(row, item)), None)
        if index is None:
            result.append(item)
        elif result[index].get("kind") != "goal":
            result[index] = {**result[index], **item, "id": result[index].get("id") or item["id"]}
    return result


def _finalize_task_statuses(items: list[dict], investigation: dict) -> list[dict]:
    next_step = ((investigation.get("step_result") or {}).get("next_step") or "").strip()
    if next_step == "ask_user" or (next_step != "done" and not investigation.get("ready_for_patch_planning")):
        return items
    done_status = "known"
    final = []
    for item in items:
        status = item.get("status") or "unknown"
        if item.get("kind") in {"unknown", "hypothesis", "clue", "work"} and status in {"unknown", "pending", "added", "updated", "active"}:
            item = {
                **item,
                "status": done_status,
                "reason": item.get("reason") or "Investigation completed without a more specific task update.",
            }
        final.append(item)
    return final


def _same_task(left: dict, right: dict) -> bool:
    if _same_task_id(left.get("id"), right.get("id")):
        return True
    left_trace = left.get("trace") if isinstance(left.get("trace"), list) else []
    right_trace = right.get("trace") if isinstance(right.get("trace"), list) else []
    left_ids = [left.get("id"), *left_trace]
    right_ids = [right.get("id"), *right_trace]
    if any(_same_task_id(left_id, right_id) for left_id in left_ids for right_id in right_ids):
        return True
    a = _task_key(left.get("text"))
    b = _task_key(right.get("text"))
    return bool(a and b and (a == b or a in b or b in a))


def _task_key(value: str | None) -> str:
    return re.sub(r"\W+", "", re.sub(r"[（(][^）)]*[）)]", "", value or "")).casefold()


def _task_id_tail(value: str | None) -> str:
    return str(value or "").rsplit(":", 1)[-1]


def _same_task_id(left: str | None, right: str | None) -> bool:
    left = str(left or "")
    right = str(right or "")
    if not left or not right:
        return False
    if left == right:
        return True
    if ":" in left and ":" in right:
        return False
    return _task_id_tail(left) == _task_id_tail(right)


def _scoped_id(analysis_id: str, item_id: str) -> str:
    if not item_id:
        return ""
    return item_id if ":" in item_id else f"{analysis_id}:{item_id}"


def _scoped_items(analysis_id: str, items: list[dict]) -> list[dict]:
    scoped = []
    for item in items or []:
        if isinstance(item, dict):
            next_item = dict(item)
            next_item["id"] = _scoped_id(analysis_id, str(next_item.get("id") or ""))
            scoped.append(next_item)
    return scoped


def _attach_session_relationship(analysis: dict, existing_tasks: list[dict]) -> None:
    parent = str(analysis.get("parent_goal_id") or "").strip()
    if parent and any(item.get("id") == parent for item in existing_tasks):
        analysis["root_goal_id"] = parent


def _session_context(state: dict) -> dict:
    observations = _observations_freshness(state)
    knowledge = _knowledge_freshness({**state, "observations": observations})
    return {
        "tasks": state.get("taskItems", []),
        "goals": [item for item in state.get("taskItems", []) if item.get("kind") == "goal"],
        "recent_user_messages": [item.get("content", "") for item in state.get("messages", []) if item.get("role") == "user"][-5:],
        "observations": observations,
        "knowledge": knowledge,
        "investigations": state.get("investigations", []),
    }


def _select_session_memory(message: str, analysis: dict | None, session_context: dict) -> dict:
    if not session_context:
        return {}
    query = _memory_query(message, analysis)
    reuse_ids = set(analysis.get("reused_context_ids", [])) if isinstance(analysis, dict) else set()
    goals = _rank_memory_items(query, session_context.get("goals", []), ("text",), limit=3)
    if not goals:
        goals = [item for item in session_context.get("goals", []) if isinstance(item, dict)][:3]
    tasks = _rank_memory_items(query, session_context.get("tasks", []), ("text", "reason"), limit=12)
    knowledge = _rank_memory_items(
        query,
        [item for item in session_context.get("knowledge", []) if item.get("fresh", True)],
        ("statement", "id"),
        limit=10,
        pinned_ids=reuse_ids,
    )
    observation_ids = {
        obs_id
        for item in knowledge
        for obs_id in item.get("observation_ids", [])
    }
    observations = _rank_memory_items(
        query,
        [item for item in session_context.get("observations", []) if item.get("fresh")],
        ("summary", "title", "path", "tool", "id"),
        limit=12,
        pinned_ids=observation_ids | reuse_ids,
    )
    investigations = _rank_memory_items(
        query,
        session_context.get("investigations", []),
        ("request", "summary", "id"),
        limit=5,
        pinned_ids=reuse_ids,
    )
    return {
        "tasks": tasks,
        "goals": goals,
        "recent_user_messages": session_context.get("recent_user_messages", [])[-3:],
        "observations": observations,
        "knowledge": knowledge,
        "investigations": investigations,
    }


def _memory_query(message: str, analysis: dict | None) -> set[str]:
    parts = [message]
    if isinstance(analysis, dict):
        intent = analysis.get("intent") if isinstance(analysis.get("intent"), dict) else {}
        parts.append(intent.get("summary", ""))
        parts.extend(item.get("text", "") for item in analysis.get("acceptance_criteria", []) if isinstance(item, dict))
        parts.extend(item.get("question", "") for item in analysis.get("unknowns", []) if isinstance(item, dict))
        parts.extend(analysis.get("constraints", []) if isinstance(analysis.get("constraints"), list) else [])
    return _memory_terms(" ".join(str(part or "") for part in parts))


def _rank_memory_items(
    query: set[str],
    items: list[dict],
    fields: tuple[str, ...],
    *,
    limit: int,
    pinned_ids: set[str] | None = None,
) -> list[dict]:
    pinned_ids = pinned_ids or set()
    ranked = []
    for index, item in enumerate(item for item in items if isinstance(item, dict)):
        item_id = str(item.get("id") or "")
        text = " ".join(str(item.get(field) or "") for field in fields)
        score = len(query & _memory_terms(text))
        if item_id in pinned_ids:
            score += 100
        if score > 0:
            ranked.append((score, index, item))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [item for _, _, item in ranked[:limit]]


def _memory_terms(value: str) -> set[str]:
    text = str(value or "").casefold()
    terms = {match.group(0) for match in re.finditer(r"[a-z0-9_./:-]{2,}", text)}
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(chunk) == 1:
            terms.add(chunk)
        else:
            terms.update(chunk[index:index + 2] for index in range(len(chunk) - 1))
    return terms


def _session_context_lines(session_context: dict | None) -> list[str]:
    if not session_context:
        return []
    lines = []
    lines.extend(f"Session goal: {item.get('text')}" for item in session_context.get("goals", [])[:3])
    lines.extend(f"Known: {item.get('statement')}" for item in session_context.get("knowledge", [])[:8] if item.get("fresh", True))
    lines.extend(f"Prior investigation: {item.get('summary')}" for item in session_context.get("investigations", [])[:3] if item.get("summary"))
    return lines


def _observations_freshness(state: dict) -> list[dict]:
    items = []
    for raw in state.get("observations", []):
        item = dict(raw)
        path = item.get("path")
        fresh = not bool(path)
        if path and os.path.exists(path):
            stat = os.stat(path)
            fresh = item.get("mtime_ns") == stat.st_mtime_ns and item.get("size") == stat.st_size
        item["fresh"] = fresh
        items.append(item)
    return items


def _knowledge_freshness(state: dict) -> list[dict]:
    observations = state.get("observations", [])
    by_id = {item.get("id"): item for item in observations}
    by_call = {_call_key(item.get("id")): item for item in observations if _call_key(item.get("id"))}
    result = []
    for raw in state.get("knowledge", []):
        item = dict(raw)
        ids = []
        fresh = True
        for obs_id in item.get("observation_ids", []):
            observation = by_id.get(obs_id) or by_call.get(_call_key(obs_id))
            if observation:
                ids.append(observation["id"])
                fresh = fresh and observation.get("fresh", True)
            else:
                ids.append(obs_id)
                fresh = False
        item["observation_ids"] = ids
        item["fresh"] = fresh
        result.append(item)
    return result


def _call_key(value: str | None) -> str:
    text = str(value or "").split(":")[-1]
    return re.sub(r"call_\d+_", "call_", text)


def _beliefs_as_knowledge(analysis_id: str, beliefs: list[dict]) -> list[dict]:
    items = []
    for index, belief in enumerate(beliefs or [], start=1):
        if isinstance(belief, dict) and belief.get("statement"):
            evidence = belief.get("evidence") if isinstance(belief.get("evidence"), list) else []
            items.append({
                "id": f"{analysis_id}:B{index}",
                "turn_id": analysis_id,
                "statement": belief["statement"],
                "status": belief.get("status", "supported"),
                "observation_ids": [_scoped_id(analysis_id, str(item)) for item in evidence],
            })
    return items
