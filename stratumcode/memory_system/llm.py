from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from .. import model_settings
from ..agent_runtime import (
    assistant_visible_text,
    call_model,
    content_text,
    start_event,
    tool_arguments,
)

EventSink = Callable[[dict], None]
_EVENT_SINK: ContextVar[EventSink | None] = ContextVar("memory_event_sink", default=None)
_TOOL_NAME = "submit_memory_result"


@contextmanager
def event_sink(sink: EventSink) -> Iterator[None]:
    token = _EVENT_SINK.set(sink)
    try:
        yield
    finally:
        _EVENT_SINK.reset(token)


def call_memory_json(operation: str, payload: dict) -> dict | None:
    setting = model_settings.resolve(model_settings.MEMORY_STAGE)
    event_id = f"memory-{operation}-{uuid4().hex[:8]}"
    if setting is None:
        _emit_status(event_id, operation, "error", "No memory model configured.")
        return None
    _emit_status(event_id, operation, "running", _operation_detail(operation, payload), setting=setting)
    try:
        parsed = _call_structured(setting, operation, payload, event_id)
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
        _emit_update(
            event_id,
            "error",
            f"Memory model did not return usable structured output: {type(exc).__name__}: {exc}",
        )
        return None
    _emit_update(event_id, "done", _done_detail(operation, parsed), result=_counts(parsed))
    return parsed


def _call_structured(setting: dict, operation: str, payload: dict, event_id: str) -> dict:
    assistant = _ask_memory_model(setting, operation, payload)
    try:
        parsed = _structured_result(assistant)
        if parsed is not None:
            return parsed
        repair_detail = _invalid_detail(assistant)
    except (json.JSONDecodeError, TypeError) as exc:
        repair_detail = f"invalid structured output: {type(exc).__name__}: {exc}"
    _emit_update(event_id, "running", f"Repairing memory model output: {repair_detail}")
    repaired = _ask_memory_model(
        setting,
        operation,
        payload,
        repair_detail=repair_detail,
    )
    parsed = _structured_result(repaired)
    if parsed is None:
        raise ValueError(_invalid_detail(repaired))
    return parsed


def _ask_memory_model(setting: dict, operation: str, payload: dict, *, repair_detail: str = "") -> dict:
    messages = [
        {
            "role": "system",
            "content": (
                "You are StratumCode's semantic memory worker. "
                f"Call {_TOOL_NAME} exactly once with the requested JSON object. "
                "Do not answer in prose."
            ),
        },
        {
            "role": "user",
            "content": _repair_prompt(operation, payload, repair_detail)
            if repair_detail
            else _prompt(operation, payload),
        },
    ]
    assistant = call_model(
        setting["provider"],
        setting["model_id"],
        messages,
        tools=[_tool_schema(operation)],
        tool_choice={"type": "function", "function": {"name": _TOOL_NAME}},
        use_skills=False,
    )
    if not isinstance(assistant, dict):
        raise TypeError("provider returned no assistant message object")
    return assistant


def _structured_result(assistant: dict) -> dict | None:
    for call in assistant.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else {}
        if not isinstance(function, dict) or function.get("name") != _TOOL_NAME:
            continue
        arguments = tool_arguments(function.get("arguments"))
        if arguments:
            return arguments
    text = _json_text(assistant_visible_text(assistant) or content_text(assistant.get("content") or ""))
    if not text:
        return None
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise TypeError("memory JSON root is not an object")
    return parsed


def _invalid_detail(assistant: dict) -> str:
    finish_reason = str(assistant.get("finish_reason") or "").strip()
    tool_names = [
        str(((call.get("function") or {}).get("name")) or "")
        for call in assistant.get("tool_calls") or []
        if isinstance(call, dict)
    ]
    text = assistant_visible_text(assistant)
    if text:
        return f"non-JSON text output, finish_reason={finish_reason or 'unknown'}"
    if tool_names:
        return f"missing {_TOOL_NAME} tool call, got={', '.join(tool_names)}"
    return f"empty structured output, finish_reason={finish_reason or 'unknown'}"


def _repair_prompt(operation: str, payload: dict, repair_detail: str) -> str:
    return json.dumps({
        "role": "memory_system_llm_repair",
        "failure": repair_detail,
        "instruction": f"Return the {operation} result by calling {_TOOL_NAME}. Do not use prose.",
        "original_request": json.loads(_prompt(operation, payload)),
    }, ensure_ascii=False, indent=2)


def _tool_schema(operation: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": _TOOL_NAME,
            "description": "Return the structured result for one StratumCode memory operation.",
            "parameters": _operation_schema(operation),
        },
    }


def _operation_schema(operation: str) -> dict:
    schemas = {
        "extract_delta": _extract_delta_schema(),
        "resolve_references": {
            "type": "object",
            "properties": {
                "references": {"type": "array", "items": _object_schema()},
                "needs_clarification": {"type": "boolean"},
                "question": {"type": "string"},
            },
            "required": ["references"],
            "additionalProperties": False,
        },
        "select_records": {
            "type": "object",
            "properties": {
                "selected_record_ids": _string_array_schema(),
                "stale_record_ids": _string_array_schema(),
                "conflict_record_ids": _string_array_schema(),
                "summary_record_ids": _string_array_schema(),
                "reason": {"type": "string"},
            },
            "required": [
                "selected_record_ids",
                "stale_record_ids",
                "conflict_record_ids",
                "summary_record_ids",
            ],
            "additionalProperties": False,
        },
        "compress_records": {
            "type": "object",
            "properties": {"summaries": {"type": "array", "items": _object_schema()}},
            "required": ["summaries"],
            "additionalProperties": False,
        },
        "detect_conflict": {
            "type": "object",
            "properties": {
                "conflict_id": {"type": "string"},
                "relation": {"type": "string", "enum": ["conflicts", "supersedes", "none"]},
                "reason": {"type": "string"},
            },
            "required": ["conflict_id", "relation", "reason"],
            "additionalProperties": False,
        },
    }
    if operation not in schemas:
        raise ValueError(f"unknown memory operation: {operation}")
    return schemas[operation]


def _extract_delta_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "records": {"type": "array", "items": _object_schema()},
            "evidence": {"type": "array", "items": _object_schema()},
            "links": {"type": "array", "items": _object_schema()},
            "refs": {"type": "array", "items": _object_schema()},
        },
        "required": ["records", "evidence", "links", "refs"],
        "additionalProperties": False,
    }


def _object_schema() -> dict:
    return {"type": "object", "additionalProperties": True}


def _string_array_schema() -> dict:
    return {"type": "array", "items": {"type": "string"}}


def _prompt(operation: str, payload: dict) -> str:
    return json.dumps({
        "role": "memory_system_llm",
        "operation": operation,
        "global_rules": [
            "Return strict JSON only.",
            "Do not invent facts, file paths, decisions, or references.",
            "Prefer not returning an item over guessing.",
            "Use concise statements, but do not truncate evidence needed for traceability.",
            "Mark verified only for validation results, actual project changes, or explicitly verified code facts.",
            "Mark recommendations, risks, and inferences as inferred or pending, not verified.",
        ],
        "schemas": {
            "extract_delta": {
                "records": [{
                    "scope": "turn|session|project",
                    "kind": "fact|observation|investigation|validation|change|decision|recommendation|risk|task|summary|knowledge",
                    "subject_kind": "project|file|symbol|task|change|decision|other",
                    "subject_key": "stable subject identifier",
                    "statement": "self-contained memory statement",
                    "confidence": "verified|inferred|uncertain",
                    "status": "accepted|pending",
                    "freshness": "fresh|unknown",
                    "source": "source label",
                    "source_record_ids": ["optional source ids"],
                    "payload": "optional object",
                }],
                "evidence": [{
                    "record_index": "1-based index into records",
                    "kind": "tool_observation|validation_file|file_snapshot|source_excerpt|other",
                    "path": "optional workspace path",
                    "excerpt": "short relevant evidence excerpt",
                    "payload": "optional object",
                }],
                "links": [{
                    "source_record_index": "1-based index into records",
                    "target_record_index": "1-based index into records",
                    "relation": "supports|depends_on|supersedes|conflicts|mentions",
                }],
                "refs": [{
                    "index": "integer, 1-based within the same kind/order",
                    "kind": "phase|item|risk|action|decision|section",
                    "label": "short user-facing label",
                    "content": "self-contained reference text",
                    "target_record_index": "optional 1-based record index",
                    "payload": "optional object",
                }],
            },
            "resolve_references": {
                "references": [{
                    "id": "conversation ref id from candidates",
                    "confidence": "high|medium|low",
                    "reason": "why this candidate matches the user query",
                }],
                "needs_clarification": "boolean",
                "question": "optional clarification question",
            },
            "select_records": {
                "selected_record_ids": ["record ids relevant enough to inject"],
                "stale_record_ids": ["related stale record ids that need revalidation"],
                "conflict_record_ids": ["selected records that should be shown as conflicts"],
                "summary_record_ids": ["selected records that are summaries"],
                "reason": "selection rationale",
            },
            "compress_records": {
                "summaries": [{
                    "subject_kind": "project|file|symbol|task|change|decision|other",
                    "subject_key": "stable subject identifier",
                    "statement": "summary preserving important facts and uncertainty",
                    "confidence": "verified|inferred|uncertain",
                    "source_record_ids": ["source record ids"],
                    "payload": "optional object",
                }],
            },
            "detect_conflict": {
                "conflict_id": "existing record id or empty string",
                "relation": "conflicts|supersedes|none",
                "reason": "short explanation",
            },
        },
        "payload": payload,
    }, ensure_ascii=False, indent=2)


def _json_text(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text


def _operation_detail(operation: str, payload: dict) -> str:
    if operation == "select_records":
        return f"Selecting from {len(payload.get('records', []))} memory record(s)."
    if operation == "resolve_references":
        return f"Resolving against {len(payload.get('refs', []))} conversation reference(s)."
    if operation == "compress_records":
        return f"Compressing {len(payload.get('records', []))} memory record(s)."
    if operation == "detect_conflict":
        return f"Checking {len(payload.get('candidates', []))} candidate conflict(s)."
    if operation == "extract_delta":
        return "Extracting memory records and references."
    return operation.replace("_", " ").title()


def _done_detail(operation: str, parsed: dict) -> str:
    counts = _counts(parsed)
    if operation == "extract_delta":
        return f"Extracted {counts.get('records', 0)} record(s) and {counts.get('refs', 0)} reference(s)."
    if operation == "select_records":
        return f"Selected {counts.get('selected_record_ids', 0)} memory record(s)."
    if operation == "resolve_references":
        return f"Resolved {counts.get('references', 0)} reference(s)."
    if operation == "compress_records":
        return f"Generated {counts.get('summaries', 0)} summary record(s)."
    if operation == "detect_conflict":
        return "Conflict check complete."
    return "Memory operation complete."


def _counts(parsed: dict) -> dict:
    result = {}
    for key, value in parsed.items():
        if isinstance(value, list):
            result[key] = len(value)
    return result


def _emit_status(event_id: str, operation: str, status: str, detail: str, *, setting: dict | None = None) -> None:
    data = {
        "operation": operation,
        "status": status,
        "summary": detail,
    }
    if setting is not None:
        data.update({
            "provider": setting.get("provider", {}).get("name", ""),
            "model": setting.get("model_id", ""),
            "inherited": bool(setting.get("inherited")),
        })
    _emit(start_event(event_id, "memory_status", data))


def _emit_update(event_id: str, status: str, summary: str, *, result: dict | None = None) -> None:
    patch = {"status": status, "summary": summary}
    if result is not None:
        patch["result"] = result
    _emit({"op": "update", "id": event_id, "patch": patch})


def _emit(event: dict) -> None:
    sink = _EVENT_SINK.get()
    if sink is not None:
        sink(event)
