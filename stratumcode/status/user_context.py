from __future__ import annotations

import os

from .. import app_settings
from .task_updates import _scoped_id

def _design_plan_with_answer(design_plan: dict, answer: dict) -> dict:
    plan = dict(design_plan)
    gap_id = str(answer.get("unknown_id") or answer.get("question_id") or "design-gap")
    response = str(answer.get("response") or answer.get("selected_option_label") or "").strip()
    question = str(answer.get("question") or gap_id).strip()
    plan["decision_gaps"] = [
        gap for gap in plan.get("decision_gaps", [])
        if not isinstance(gap, dict) or str(gap.get("id") or "") != gap_id
    ]
    if response:
        decisions = [item for item in plan.get("design_decisions", []) if isinstance(item, dict)]
        decisions.append({
            "id": f"USER-{gap_id}",
            "decision": response,
            "because": [f"User answered: {question}"],
        })
        plan["design_decisions"] = decisions
    return plan


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


def _answered_task_update(analysis_id: str, answer: dict | None) -> dict | None:
    if not answer:
        return None
    question_id = str(answer.get("question_id") or answer.get("unknown_id") or "").strip()
    response = str(answer.get("response") or answer.get("text") or "").strip()
    if not question_id or not response:
        return None
    question = str(answer.get("question") or question_id).strip()
    return {
        "id": _scoped_id(analysis_id, question_id),
        "kind": "unknown",
        "text": question,
        "status": "known",
        "reason": f"User answered: {response}",
        "answers": [{
            "source": "user",
            "text": response,
            "reason": "User answered this blocking question.",
            "trace": [],
        }],
    }


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
