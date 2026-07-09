from __future__ import annotations

import asyncio
import json
import platform
import re
from collections.abc import Iterator
from dataclasses import asdict
from uuid import uuid4

from . import app_settings, model_settings, prompt, providers
from .agent import EvidencePolicy, EvidenceRun, RunState
from .agent.policy import DISCOVERY_TOOLS, EvidencePhase
from .agent.tools import agent_tools
from .agent_runtime import (
    add_usage as _add_usage,
    call_model as _call_model,
    content_text as _content_text,
    empty_usage as _empty_usage,
    start_event,
    tool_error_json,
    usage_delta as _usage_delta,
)
from .tools import registry

MAX_AGENT_ROUNDS = 14
MAX_EMPTY_TOOL_ROUNDS = 2


def _execute_tool(name: str, params: dict, workspace_dir: str):
    tool = registry.get(name)
    if not tool:
        raise ValueError(f"unknown tool: {name}")
    return tool, asyncio.run(tool.execute(params, {"directory": workspace_dir}))


def evidence_stream(
    hypothesis: str,
    context: list[str],
    workspace_dir: str,
    max_rounds: int | None = None,
) -> Iterator[dict]:
    setting = model_settings.resolve(model_settings.EVIDENCE_STAGE)
    if setting is None:
        raise ValueError(
            "No model configured for evidence gathering. Configure a default or evidence model in Providers."
        )

    provider = setting["provider"]
    model = setting["model_id"]
    pricing_rules = providers.get_model_pricing(provider["id"], model)
    run = EvidenceRun(hypothesis)
    run.transition(RunState.GATHERING)
    discovery_tools = _discovery_tools(hypothesis, context)
    policy = EvidencePolicy(
        discovery_tools=discovery_tools,
        max_rounds=max_rounds or MAX_AGENT_ROUNDS,
    )
    run_id = uuid4().hex[:10]
    stage_id = f"{run_id}-stage"
    hypothesis_id = f"{run_id}-hypothesis"
    yield start_event(stage_id, "stage", {
        "name": "evidence",
        "label": "Gather evidence",
        "state": run.state.value,
        "phase": policy.phase.value,
        "model": model,
        "context_length": providers.model_context_length(provider["base_url"], provider["api_key"], model),
        "provider": provider["name"],
        "inherited": setting["inherited"],
    })
    yield start_event(hypothesis_id, "hypothesis", {
        "text": hypothesis,
        "confidence": run.confidence,
        "status": run.state.value,
    })

    messages = [
        {
            "role": "system",
            "content": prompt.build_evidence_static(app_settings.get_output_language()),
        },
        {
            "role": "system",
            "content": prompt.build_evidence_context(
                hypothesis=hypothesis,
                directory=workspace_dir,
                platform=platform.system(),
                model=model,
                context=context,
                max_rounds=policy.max_rounds,
            ),
        },
        {"role": "user", "content": hypothesis},
    ]
    observed_calls: dict[str, dict] = {}
    empty_tool_rounds = 0
    usage_total = _empty_usage(pricing_rules)

    for round_index in range(policy.max_rounds):
        allowed_tools, _, _ = policy.next_request(run)
        phase_before = policy.phase
        step_result = None
        thinking_id = f"{run_id}-thinking-{round_index}"
        yield start_event(thinking_id, "thinking", {
            "text": "",
            "done": False,
            "open": True,
        })
        assistant = _call_model(
            provider,
            model,
            messages,
            tools=agent_tools(discovery_tools),
        )
        if usage := _usage_delta(pricing_rules, assistant.pop("_usage", {})):
            _add_usage(usage_total, usage)
            yield start_event(f"{run_id}-usage-{round_index}", "usage", {
                "delta": usage,
                "total": usage_total,
            })
        content = _content_text(assistant.get("content"))
        tool_calls = assistant.get("tool_calls") or []
        messages.append({
            "role": "assistant",
            "content": assistant.get("content"),
            **({"tool_calls": tool_calls} if tool_calls else {}),
        })

        if content:
            yield {"op": "update", "id": thinking_id, "patch": {
                "text": content,
                "done": True,
                "open": bool(tool_calls),
            }}
        else:
            yield {"op": "update", "id": thinking_id, "patch": {
                "done": True,
            }}

        if not tool_calls:
            empty_tool_rounds += 1
            if empty_tool_rounds >= MAX_EMPTY_TOOL_ROUNDS:
                yield start_event(f"{run_id}-safety-empty-{round_index}", "safety_stop", {
                    "reason": "empty_tool_rounds",
                    "message": "Safety net triggered: the agent stopped producing tool calls.",
                })
                policy.request_checkpoint()
                if not run.evidence:
                    break
                continue
            if policy.phase != EvidencePhase.EVALUATE and (run.evidence or observed_calls):
                continue
            if not run.evidence and observed_calls:
                policy.request_checkpoint()
                continue
            break

        empty_tool_rounds = 0
        concluded = False
        compaction_messages: list[dict] = []
        for raw_call in tool_calls:
            call_id = raw_call.get("id") or f"call-{uuid4().hex[:8]}"
            function = raw_call.get("function") or {}
            name = function.get("name", "")
            maybe_step = None
            try:
                arguments = json.loads(function.get("arguments") or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
                if name not in set(allowed_tools):
                    raise ValueError(f"unknown agent tool: {name or 'tool'}")
                gen = _handle_agent_tool(
                    name=name,
                    call_id=call_id,
                    arguments=arguments,
                    run=run,
                    observed_calls=observed_calls,
                    workspace_dir=workspace_dir,
                    hypothesis_id=hypothesis_id,
                    stage_id=stage_id,
                    run_id=run_id,
                    policy=policy,
                )
                try:
                    while True:
                        yield next(gen)
                except StopIteration as e:
                    output, concluded, maybe_step = e.value
                if maybe_step is not None:
                    step_result = maybe_step
                if name == "record_evidence":
                    if compact_message := _compact_tool_message(arguments):
                        compaction_messages.append(compact_message)
                if policy.phase != phase_before:
                    yield {"op": "update", "id": stage_id, "patch": {
                        "phase": policy.phase.value,
                    }}
                    phase_before = policy.phase
            except Exception as exc:
                output = tool_error_json(exc, name)
                if name == "record_evidence":
                    policy.note_checkpoint_failure()
                yield start_event(call_id, "tool", {
                    "name": name or "invalid",
                    "description": (
                        registry.get(name).description
                        if registry.get(name)
                        else "Agent control tool"
                    ),
                    "status": "error",
                    "open": False,
                    "input": function.get("arguments") or "{}",
                    "output": output,
                })
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": output,
            })
            if concluded:
                break
        messages.extend(compaction_messages)
        if concluded:
            break
        if step_result is not None:
            next_step = step_result["next_step"]
            if next_step == "continue_investigation":
                continue
            break

    if run.state == RunState.GATHERING and len(run.step_results) and run.step_results[-1].next_step == "continue_investigation":
        yield start_event(f"{run_id}-safety-round-limit", "safety_stop", {
            "reason": "max_rounds",
            "message": "Safety net triggered: the agent still wanted to continue when max_rounds was reached.",
        })

    if run.state == RunState.GATHERING:
        if run.evidence:
            if policy.phase != EvidencePhase.EVALUATE:
                policy.phase = EvidencePhase.EVALUATE
                yield {"op": "update", "id": stage_id, "patch": {
                    "phase": policy.phase.value,
                }}
            summary = _fallback_summary(run)
        else:
            summary = _no_evidence_summary(observed_calls)
        run.conclude(summary)
        yield {"op": "update", "id": stage_id, "patch": {"state": run.state.value}}
        yield {"op": "update", "id": hypothesis_id, "patch": {
            "confidence": run.confidence,
            "status": run.state.value,
        }}
        yield start_event(f"{run_id}-verdict", "verdict", {
            "verdict": run.state.value,
            "confidence": run.confidence,
            "summary": run.summary,
            "findings": _build_findings(run),
            "support_count": sum(1 for e in run.evidence.values() if e.stance == "support"),
            "oppose_count": sum(1 for e in run.evidence.values() if e.stance == "oppose"),
        })

    if run.summary.strip():
        yield start_event(f"{run_id}-output", "output", {
            "content": run.summary,
            "streaming": False,
        })
    yield {"op": "done", "run": run.snapshot()}


def _fallback_summary(run: EvidenceRun) -> str:
    findings = []
    for item in run.evidence.values():
        stance = "supports" if item.stance == "support" else "opposes"
        findings.append(f"{item.id} {stance}: {item.claim} ({item.source_uri})")
    return "Verdict computed from recorded evidence: " + "; ".join(findings)


def _build_findings(run: EvidenceRun) -> list[dict]:
    return [
        {
            "id": item.id,
            "stance": item.stance,
            "claim": item.claim,
            "weight": round(item.strength * 100),
            "source_type": item.source_type,
            "source_uri": item.source_uri,
            "excerpt": item.excerpt[:200],
        }
        for item in run.evidence.values()
    ]


def _discovery_tools(hypothesis: str = "", context: list[str] | None = None) -> tuple[str, ...]:
    mcp_tools = tuple(tool.name for tool in registry.list_all() if tool.name.startswith("mcp_"))
    return DISCOVERY_TOOLS + tuple(name for name in mcp_tools if name not in DISCOVERY_TOOLS)


def _compact_tool_message(evidence_args: dict) -> dict | None:
    source_call_id = evidence_args.get("source_tool_call_id")
    if not source_call_id:
        return None
    compact = {
        "tool_call_id": source_call_id,
        "compacted": True,
        "summary": (
            "The referenced tool output has been recorded as evidence. "
            "For future reasoning, use the recorded excerpt instead of re-reading the full output."
        ),
        "recorded_evidence": {
            "id": evidence_args.get("evidence_id"),
            "source_uri": evidence_args.get("source_uri"),
            "excerpt": evidence_args.get("excerpt"),
        },
    }
    return {
        "role": "user",
        "content": json.dumps(compact, ensure_ascii=False),
    }


def _no_evidence_summary(observed_calls: dict[str, dict]) -> str:
    summary = (
        "Evidence gathering ended without a valid evidence record. "
        "Tool observations were not converted into grounded evidence."
    )
    observations = _observed_call_summary(observed_calls)
    return summary + ("\n\nRecent observations:\n" + observations if observations else "")


def _observed_call_summary(observed_calls: dict[str, dict]) -> str:
    lines = []
    for call_id, observed in list(observed_calls.items())[-4:]:
        result = observed["result"]
        excerpt = next(
            (line.strip() for line in result.output.splitlines() if _useful_excerpt_candidate(line)),
            result.output.strip(),
        )[:200]
        lines.append(f"- {call_id} {observed['name']}: {result.title} :: {excerpt}")
    return "\n".join(line for line in lines if line.strip())


def _useful_excerpt_candidate(line: str) -> bool:
    text = line.strip()
    if len(text) < 8:
        return False
    lowered = text.casefold()
    if lowered in {"(no matches)", "(no results)", "<!doctype html>"}:
        return False
    return any(ch.isalnum() for ch in text)


def _best_excerpt_candidate(output: str, claim: str) -> str:
    claim_tokens = _excerpt_tokens(claim)
    best = ("", 0)
    for line in output.splitlines():
        candidate = line.strip()
        if not _useful_excerpt_candidate(candidate):
            continue
        score = len(claim_tokens & _excerpt_tokens(candidate))
        if score > best[1]:
            best = (candidate, score)
    return best[0] if best[1] > 0 else ""


def _excerpt_tokens(value: str) -> set[str]:
    return set(re.findall(
        r"[a-zA-Z_][a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]{2,}",
        value.casefold(),
    ))


def _handle_agent_tool(
    *,
    name: str,
    call_id: str,
    arguments: dict,
    run: EvidenceRun,
    observed_calls: dict[str, dict],
    workspace_dir: str,
    hypothesis_id: str,
    stage_id: str,
    run_id: str,
    policy: EvidencePolicy | None = None,
):
    """Yields stream packets directly. Returns (model_output, concluded, step_result) via StopIteration."""
    discovery_tools = policy.available_discovery_tools() if policy else DISCOVERY_TOOLS
    if name in discovery_tools:
        if policy:
            arguments = policy.prepare_discovery(name, arguments)
        tool = registry.get(name)
        yield start_event(call_id, "tool", {
            "name": name,
            "description": tool.description,
            "status": "running",
            "open": False,
            "input": json.dumps(arguments, ensure_ascii=False, indent=2),
            "output": "",
        })
        _, result = _execute_tool(name, arguments, workspace_dir)
        observed_calls[call_id] = {
            "name": name,
            "arguments": arguments,
            "result": result,
        }
        run.add_observation(
            observation_id=call_id,
            tool=name,
            title=result.title,
            summary=_best_observation_summary(result.output),
        )
        if policy:
            useful = not result.title.startswith("[error]")
            policy.note_discovery(name, arguments, useful=useful)
        yield {"op": "update", "id": call_id, "patch": {
            "status": "error" if result.title.startswith("[error]") else "done",
            "title": result.title,
            "output": result.output,
            "metadata": result.metadata,
        }}
        model_output = {
            "tool_call_id": call_id,
            "title": result.title,
            "output": result.output,
            "metadata": result.metadata,
        }
        return json.dumps(model_output, ensure_ascii=False), False, None

    if name == "record_evidence":
        source_call_id = arguments.get("source_tool_call_id")
        observed = observed_calls.get(source_call_id)
        if observed is None:
            raise ValueError("source_tool_call_id must reference a completed discovery tool")
        record_args = dict(arguments)
        record_args.pop("source_tool_call_id", None)
        excerpt = arguments.get("excerpt", "")
        if _normalized(excerpt) in {"(no matches)", "(no results)"}:
            raise ValueError("empty search results cannot be recorded as evidence excerpt")
        observed_output = observed["result"].output
        if not excerpt or _normalized(excerpt) not in _normalized(observed_output):
            replacement = _best_excerpt_candidate(
                observed_output,
                arguments.get("claim", ""),
            )
            if replacement:
                arguments["excerpt"] = replacement
                record_args["excerpt"] = replacement
            else:
                sample = next(
                    (
                        line.strip()
                        for line in observed_output.splitlines()
                        if _useful_excerpt_candidate(line)
                    ),
                    observed_output.strip(),
                )[:300]
                raise ValueError(
                    "evidence excerpt must be copied from the cited tool output "
                    f"(whitespace differences are allowed). Try an exact excerpt like: {sample!r}"
                )
        item = run.add_evidence(**record_args, tool_call_id=source_call_id)
        if policy:
            policy.note_evidence(item.stance)
        data = {
            **asdict(item),
            "confidence": run.confidence,
        }
        yield start_event(f"{run_id}-evidence-{item.id}", "evidence", data)
        yield {"op": "update", "id": hypothesis_id, "patch": {
            "confidence": run.confidence,
        }}
        return json.dumps(data, ensure_ascii=False), False, None

    if name == "link_evidence":
        if "relation" not in arguments and "relationship" in arguments:
            arguments["relation"] = arguments.pop("relationship")
        edge = run.link_evidence(**arguments)
        if policy:
            policy.note_audit()
        data = {**asdict(edge), "confidence": run.confidence}
        yield start_event(
            f"{run_id}-relation-{len(run.relations)}",
            "evidence_relation",
            data,
        )
        yield {"op": "update", "id": hypothesis_id, "patch": {
            "confidence": run.confidence,
        }}
        return json.dumps(data, ensure_ascii=False), False, None

    if name == "report_step":
        for item in arguments.get("unknowns") or []:
            run.upsert_unknown(
                unknown_id=str(item.get("id") or ""),
                question=str(item.get("question") or ""),
                blocking=bool(item.get("blocking")),
                resolution_strategy=str(item.get("resolution_strategy") or ""),
                resolved_by_evidence_ids=_string_list(item.get("resolved_by_evidence_ids")),
                resolved_by_belief_ids=_string_list(item.get("resolved_by_belief_ids")),
            )
        for item in arguments.get("beliefs") or []:
            run.upsert_belief(
                belief_id=str(item.get("id") or ""),
                statement=str(item.get("statement") or ""),
                status=str(item.get("status") or ""),
                evidence_ids=_string_list(item.get("evidence_ids")),
                observation_ids=_string_list(item.get("observation_ids")),
                resolves_unknown_ids=_string_list(item.get("resolves_unknown_ids")),
            )
        step = run.report_step(
            next_step=str(arguments.get("next_step") or ""),
            continue_reason=str(arguments.get("continue_reason") or ""),
            target_unknown_ids=_string_list(arguments.get("target_unknown_ids")),
        )
        data = asdict(step)
        yield start_event(f"{run_id}-step-{len(run.step_results)}", "step_result", data)
        return json.dumps(data, ensure_ascii=False), step.next_step != "continue_investigation", data

    if name == "conclude":
        if not run.evidence:
            raise ValueError("at least one grounded evidence item is required before concluding")
        verdict = run.conclude(arguments["summary"])
        yield {"op": "update", "id": stage_id, "patch": {
            "state": verdict.value,
        }}
        yield {"op": "update", "id": hypothesis_id, "patch": {
            "confidence": run.confidence,
            "status": verdict.value,
        }}
        data = {
            "verdict": verdict.value,
            "confidence": run.confidence,
            "summary": run.summary,
            "findings": _build_findings(run),
            "support_count": sum(1 for e in run.evidence.values() if e.stance == "support"),
            "oppose_count": sum(1 for e in run.evidence.values() if e.stance == "oppose"),
        }
        yield start_event(f"{run_id}-verdict", "verdict", data)
        return json.dumps(data, ensure_ascii=False), True, None

    raise ValueError(f"unknown agent tool: {name}")


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _best_observation_summary(output: str) -> str:
    return next(
        (line.strip() for line in output.splitlines() if _useful_excerpt_candidate(line)),
        output.strip(),
    )[:240]
