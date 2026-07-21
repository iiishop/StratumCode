from __future__ import annotations

import json
import logging
import re
from itertools import count

from .. import app_settings, model_settings, prompt
from ..agent_runtime import call_model as _runtime_call_model, content_text as _runtime_content_text
from .task_contract import _ensure_task_contract
from .session_memory import _session_context_lines

LOGGER = logging.getLogger(__name__)

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
DEFAULT_TASK_SLOT_ATTEMPTS = 3


def _message_requests_implementation(message: str) -> bool:
    lowered = " ".join(str(message or "").split()).casefold()
    return any(word in lowered for word in (
        "\u5b9e\u73b0", "\u6dfb\u52a0", "\u589e\u52a0", "\u4fee\u6539", "\u4fee\u590d", "\u652f\u6301", "\u8c03\u6574", "\u6539\u6210", "\u53d8\u6210", "\u52a0\u4e0a", "\u8ba9", "\u4e0d\u8981",
        "\u5220\u9664", "\u79fb\u9664", "\u6e05\u7406", "\u66ff\u6362", "\u4f18\u5316", "\u8fdb\u884c\u4fee\u590d",
        "create", "add", "implement", "change", "update", "adjust", "make", "set", "do not", "don't",
    ))

def analyze_task(message: str, context: list[str], workspace_dir: str, session_context: dict | None = None, *, call_model=_runtime_call_model, content_text=_runtime_content_text, resolve_model=model_settings.resolve) -> dict:
    setting = (
        resolve_model(model_settings.DEFAULT_STAGE)
        or resolve_model(model_settings.EVIDENCE_STAGE)
    )
    if setting is None:
        raise ValueError(
            "No model configured for task analysis. Configure a default or evidence model in Providers."
        )

    provider = setting["provider"]
    model = setting["model_id"]
    analyzer_errors = []
    slot_context = context + _session_context_lines(session_context)
    system = {"role": "system", "content": prompt.build_task_analyzer(app_settings.get_output_language())}
    intent_slot, errors = _task_slot_json(
        provider,
        model,
        call_model,
        content_text,
        [
            system,
            {"role": "user", "content": prompt.build_task_intent_slot_user(
                message=message,
                directory=workspace_dir,
                context=slot_context,
            )},
        ],
        "intent_scope",
    )
    analyzer_errors.extend(errors)
    acceptance_slot, errors = _task_slot_json(
        provider,
        model,
        call_model,
        content_text,
        [
            system,
            {"role": "user", "content": prompt.build_task_acceptance_slot_user(
                message=message,
                directory=workspace_dir,
                context=slot_context,
                intent_slot=intent_slot,
            )},
        ],
        "acceptance_contract",
    )
    analyzer_errors.extend(errors)
    acceptance_slots = _runtime_acceptance_slots(acceptance_slot, message, context)
    unknowns_slot, errors = _task_slot_json(
        provider,
        model,
        call_model,
        content_text,
        [
            system,
            {"role": "user", "content": prompt.build_task_unknowns_slot_user(
                message=message,
                directory=workspace_dir,
                context=slot_context,
                intent_slot=intent_slot,
                acceptance_slots=acceptance_slots,
            )},
        ],
        "unknowns",
    )
    analyzer_errors.extend(errors)
    try:
        analysis = _validate_task_analysis(_analysis_from_slots(
            message,
            context,
            intent_slot or {},
            acceptance_slot or {},
            unknowns_slot or {},
        ))
    except ValueError as exc:
        analyzer_errors.append(str(exc))
        analysis = _minimal_task_analysis(message, context)
    analysis["model"] = model
    analysis["provider"] = provider["name"]
    analysis["analyzer_attempts"] = len([item for item in (intent_slot, acceptance_slot, unknowns_slot) if item])
    if analyzer_errors:
        analysis["analyzer_errors"] = analyzer_errors
        analysis["recovered_from_partial_analyzer_output"] = True
        analysis["analyzer_error"] = "minimal recovery: task analyzer slot recovery used runtime defaults for invalid or missing content"
    analysis["evidence_hypothesis"] = _analysis_hypothesis(message, analysis)
    return analysis


def _task_slot_json(provider: dict, model: str, call_model, content_text, messages: list[dict], label: str) -> tuple[dict | None, list[str]]:
    errors = []
    last_invalid_key = ""
    repeated_invalid = 0
    attempts = app_settings.get_round_limit("task_analyzer_attempts") or DEFAULT_TASK_SLOT_ATTEMPTS
    for attempt in _attempt_indexes(attempts):
        assistant = call_model(provider, model, messages, tools=[])
        raw = content_text(assistant.get("content"))
        try:
            if assistant.get("tool_calls"):
                raise ValueError("tool calls are not allowed")
            return _json_object(raw), errors
        except ValueError as exc:
            error = f"{label}: {exc}"
            errors.append(error)
            LOGGER.warning(
                "task analyzer slot failed",
                extra={
                    "slot": label,
                    "attempt": attempt,
                    "error": str(exc),
                    "finish_reason": assistant.get("finish_reason"),
                    "raw_excerpt": raw[:4000],
                },
            )
            invalid_key = f"{raw[:1000]}::{exc}"
            repeated_invalid = repeated_invalid + 1 if invalid_key == last_invalid_key else 1
            last_invalid_key = invalid_key
            messages.extend([
                {"role": "assistant", "content": raw[:4000]},
                {"role": "user", "content": (
                    "The previous response was not valid slot JSON: "
                    f"{exc}. Return only the requested output_contract JSON. "
                    "Do not write final task-analysis ids, schema wrappers, or Markdown."
                )},
            ])
            if repeated_invalid >= DEFAULT_TASK_SLOT_ATTEMPTS:
                return None, errors
    return None, errors


def _analysis_from_slots(message: str, context: list[str], intent_slot: dict, acceptance_slot: dict, unknowns_slot: dict) -> dict:
    fallback = _fallback_task_analysis(message, context)
    intent_data = intent_slot.get("intent") if isinstance(intent_slot.get("intent"), dict) else intent_slot
    acceptance_data = acceptance_slot or intent_slot
    unknown_data = unknowns_slot or intent_slot
    intent_type = str(intent_data.get("intent_type") or intent_data.get("type") or fallback["intent"]["type"]).strip().casefold()
    if intent_type not in TASK_INTENT_TYPES:
        intent_type = fallback["intent"]["type"]
    summary = str(intent_data.get("summary") or fallback["intent"]["summary"]).strip()
    acceptance = _runtime_acceptance_slots(acceptance_data, message, context)
    data = {
        "intent": {"type": intent_type, "summary": summary},
        "acceptance_criteria": acceptance,
        "behavior_contract": _slot_behavior_contract(acceptance_data, fallback),
        "constraints": _slot_string_list(intent_data.get("constraints"), fallback["constraints"]),
        "scope": _slot_scope(acceptance_data, fallback),
        "hypotheses": _slot_hypotheses(intent_data.get("hypotheses")),
        "clues": _unique_clues(_slot_clues(intent_data.get("clues")) + fallback["clues"]),
        "unknowns": _runtime_unknowns(unknown_data, acceptance, fallback),
    }
    return data


def _runtime_acceptance_slots(data: dict | None, message: str, context: list[str]) -> list[dict]:
    fallback = _fallback_task_analysis(message, context)["acceptance_criteria"]
    value = (data or {}).get("acceptance_criteria")
    if value is None:
        value = (data or {}).get("acceptance")
    items = []
    if isinstance(value, list):
        for raw in value:
            text = str(raw.get("text") or raw.get("description") or "") if isinstance(raw, dict) else str(raw)
            text = text.strip()
            if text:
                items.append({"id": f"AC{len(items) + 1}", "text": text})
    return items or fallback


def _runtime_unknowns(data: dict, acceptance: list[dict], fallback: dict) -> list[dict]:
    raw = data.get("unknown_content")
    if raw is None:
        raw = data.get("unknowns")
    if not isinstance(raw, list):
        return fallback["unknowns"]
    criteria_ids = [item["id"] for item in acceptance]
    items = []
    for raw_item in raw[:5]:
        if isinstance(raw_item, dict):
            question = str(raw_item.get("question") or raw_item.get("text") or raw_item.get("description") or "").strip()
            unknown_type = str(raw_item.get("type") or "code_fact").strip().casefold()
            strategy = str(raw_item.get("resolution_strategy") or "investigate_project").strip().casefold()
            why = str(raw_item.get("why") or raw_item.get("reason") or "").strip()
            accepted_ids = _slot_ids(raw_item.get("acceptance_slots"), criteria_ids)
            blocking = bool(raw_item.get("blocking", True))
        else:
            question = str(raw_item).strip()
            unknown_type = "code_fact"
            strategy = "investigate_project"
            why = ""
            accepted_ids = criteria_ids
            blocking = True
        if not question:
            continue
        unknown_type = TASK_UNKNOWN_TYPE_ALIASES.get(unknown_type, unknown_type)
        if unknown_type not in TASK_UNKNOWN_TYPES:
            unknown_type = "code_fact"
        if strategy not in TASK_UNKNOWN_STRATEGIES:
            strategy = "investigate_project"
        unknown_type, strategy, blocking = _normalize_unknown_policy(unknown_type, strategy, blocking)
        items.append({
            "id": f"U{len(items) + 1}",
            "question": question,
            "blocking": blocking,
            "type": unknown_type,
            "why": why,
            "resolution_strategy": strategy,
            "acceptance_criteria_ids": accepted_ids or criteria_ids,
        })
    return items or fallback["unknowns"]


def _slot_ids(value, ids: list[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for raw in value:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= index <= len(ids):
            result.append(ids[index - 1])
    return result


def _slot_behavior_contract(data: dict, fallback: dict) -> dict:
    if "behavior_contract" not in data:
        return fallback["behavior_contract"]
    try:
        parsed = _behavior_contract(data.get("behavior_contract"))
    except ValueError:
        return _behavior_contract({})
    return parsed or fallback["behavior_contract"]


def _slot_scope(data: dict, fallback: dict) -> dict:
    try:
        parsed = _scope(data.get("scope"))
    except ValueError:
        return fallback["scope"]
    return parsed or fallback["scope"]


def _slot_string_list(value, fallback: list[str]) -> list[str]:
    try:
        return _string_list(value, "slot")
    except ValueError:
        return fallback


def _slot_hypotheses(value) -> list[dict]:
    try:
        return _hypotheses(value)
    except ValueError:
        return []


def _slot_clues(value) -> list[dict]:
    try:
        return _clues(value)
    except ValueError:
        return []


def _attempt_indexes(limit: int, start: int = 1):
    limit = int(limit or 0)
    return count(start) if limit <= 0 else range(start, start + limit)


def _json_object(raw: str) -> dict:
    text = (raw or "").strip()
    candidates = _json_candidates(text)
    if not candidates:
        raise ValueError("response is not a JSON object")
    errors = []
    for candidate in candidates:
        for body in (candidate, _repair_jsonish(candidate)):
            try:
                data, _ = json.JSONDecoder().raw_decode(body)
                if not isinstance(data, dict):
                    raise ValueError("top-level JSON must be an object")
                return data
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append(str(exc))
    raise ValueError(f"invalid JSON: {errors[-1]}")


def _json_candidates(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    candidates = []
    candidates.extend(match.group(1).strip() for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE))
    candidates.append(text)
    for index, char in enumerate(text):
        if char == "{":
            candidates.append(text[index:])
    result = []
    seen = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result


def _repair_jsonish(text: str) -> str:
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = re.sub(r":\s*([,}\]])", r": null\1", text)
    return text


def _minimal_task_analysis(message: str, context: list[str], raw: str = "") -> dict:
    result = _fallback_task_analysis(message, context)
    request = " ".join(str(message or "").split()).strip()
    raw_text = " ".join(str(raw or "").split()).strip()
    summary = _sentence_from_raw(raw_text) or request[:160] or result["intent"]["summary"]
    result["intent"] = {
        "type": "feature" if _message_requests_implementation(request) else result["intent"]["type"],
        "summary": summary,
    }
    result["acceptance_criteria"] = [{"id": "AC1", "text": request[:220] or summary}]
    result["unknowns"] = [{
        "id": "U1",
        "question": _implementation_unknown(request or summary),
        "blocking": True,
        "type": "code_fact",
        "why": "Patch planning needs the exact code path and project convention for this requested behavior.",
        "resolution_strategy": "investigate_project",
        "acceptance_criteria_ids": ["AC1"],
    }]
    result["recovered_from_minimal_analyzer_output"] = True
    return result


def _sentence_from_raw(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[{}\[\]\"']", " ", text)
    parts = [part.strip(" :-") for part in re.split(r"[\u3002.!?\n\r]+", text) if part.strip(" :-")]
    return next((part[:160] for part in parts if len(part) >= 8), "")


def _implementation_unknown(request: str) -> str:
    target = request[:120] or "the requested behavior"
    return f"Which existing code path controls this behavior: {target}?"


def _fallback_task_analysis(message: str, context: list[str]) -> dict:
    text = " ".join(str(message or "").split()).strip()
    lowered = text.casefold()
    intent_type = "question"
    if any(word in lowered for word in ("\u4fee\u590d", "fix", "bug", "\u62a5\u9519", "\u9519\u8bef")):
        intent_type = "bugfix"
    elif _message_requests_implementation(text):
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
                "question": _implementation_unknown(text),
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

    acceptance = _optional_field(lambda: _acceptance_criteria(data.get("acceptance_criteria")), [])
    result = {
        "intent": {"type": intent_type, "summary": summary},
        "acceptance_criteria": acceptance,
        "behavior_contract": _optional_field(lambda: _behavior_contract(data.get("behavior_contract")), {}),
        "constraints": _optional_field(lambda: _string_list(data.get("constraints"), "constraints"), []),
        "scope": _optional_field(lambda: _scope(data.get("scope")), {}),
        "hypotheses": _optional_field(lambda: _hypotheses(data.get("hypotheses")), []),
        "clues": _optional_field(lambda: _clues(data.get("clues")), []),
        "unknowns": _limited_unknowns(data.get("unknowns"), acceptance),
    }
    return _ensure_task_contract(result)


def _optional_field(parser, fallback):
    try:
        return parser()
    except ValueError:
        return fallback


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
        unknown_type, strategy, blocking = _normalize_unknown_policy(unknown_type, strategy, blocking)
        if criteria_ids:
            accepted_ids = [item for item in accepted_ids if item in criteria_ids] or criteria_ids
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


def _normalize_unknown_policy(unknown_type: str, strategy: str, blocking: bool) -> tuple[str, str, bool]:
    if unknown_type == "deferred" or strategy == "deferred" or not blocking:
        return "deferred", "deferred", False
    if strategy == "ask_user":
        if unknown_type != "product_decision":
            return unknown_type, "investigate_project", True
        return unknown_type, strategy, True
    return unknown_type, strategy, bool(blocking)


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


