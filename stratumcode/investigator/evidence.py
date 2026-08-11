from __future__ import annotations

import re
import sys
from collections.abc import Collection, Iterator

from ..tools import registry
from .constants import OBSERVATION_EVIDENCE_CHARS, PROJECT_EVIDENCE_CAPABILITY
from .domain import (
    _belief_status,
    _belief_text,
    _observation_reference_map,
    _observation_refs,
    _reference_list,
)
from .util import _dedupe_strings


class _ProjectEvidenceTools(set[str]):
    def _active(self) -> Collection[str]:
        package = sys.modules.get(__package__)
        if package is None:
            return self
        value = getattr(package, "PROJECT_EVIDENCE_TOOLS", self)
        if not isinstance(value, Collection):
            return self
        return self if value is self else value

    def __contains__(self, item: object) -> bool:
        active = self._active()
        if active is self:
            return super().__contains__(item)
        return item in active

    def __iter__(self) -> Iterator[str]:
        active = self._active()
        if active is self:
            return super().__iter__()
        return iter(active)


# Compatibility hook for integrations that patched this set before tool capabilities existed.
PROJECT_EVIDENCE_TOOLS: set[str] = _ProjectEvidenceTools()


def _alias_beliefs(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for raw in value:
        if isinstance(raw, str):
            statement = raw.strip()
            evidence = []
            status = "supported"
        elif isinstance(raw, dict):
            statement = _belief_text(raw) or _alias_statement(raw)
            evidence = _reference_list(raw.get("evidence") or raw.get("source") or raw.get("sources"))
            status = _belief_status(raw, default="supported")
        else:
            continue
        if statement:
            item = {"statement": statement, "status": status, "evidence": evidence}
            if isinstance(raw, dict) and str(raw.get("id") or "").strip():
                item["id"] = str(raw["id"]).strip()
            items.append(item)
    return items


def _alias_statement(raw: dict) -> str:
    label = str(raw.get("label") or raw.get("title") or "").strip()
    evidence = str(raw.get("evidence") or raw.get("source") or "").strip()
    if label and evidence:
        return f"{label}: {evidence}"
    return label or evidence


def _positive_project_observation(item: dict) -> bool:
    if str(item.get("tool") or "") == "lsp_tool":
        return False
    tool = registry.get(str(item.get("tool") or ""))
    if (
        item.get("tool") not in PROJECT_EVIDENCE_TOOLS
        and (tool is None or PROJECT_EVIDENCE_CAPABILITY not in tool.capabilities)
    ):
        return False
    if not item.get("target_unknown_ids"):
        return False
    return True


def _supporting_belief(item: dict) -> bool:
    if not _belief_text(item):
        return False
    status = str(item.get("status") or "").strip()
    return status in {"", "supported", "strongly_supported", "runtime_confirmed"}


def _observation_evidence_excerpt(value) -> str:
    text = str(value or "")
    if len(text) <= OBSERVATION_EVIDENCE_CHARS:
        return text
    half = OBSERVATION_EVIDENCE_CHARS // 2
    return f"{text[:half]}\n...\n{text[-half:]}"


def _observation_ref_by_id(observations: list[dict]) -> dict[str, str]:
    return {
        observation_id: ref
        for ref, observation_id in _observation_reference_map(observations).items()
    }


def _canonical_evidence_id(
    evidence_id: str,
    known_ids: set[str],
    observation_refs: dict[str, str] | None = None,
) -> str:
    if observation_refs and evidence_id in observation_refs:
        return observation_refs[evidence_id]
    if evidence_id in known_ids:
        return evidence_id
    # Cross-task prefix mapping: a resolution from an earlier analysis pass
    # (task-2f536378:call_...) may reference an observation that this pass
    # knows under the current task prefix (task-e092c71c:call_...). Match by
    # the call-id tail when the mapping is unambiguous.
    tail = evidence_id.rsplit(":", 1)[-1]
    if tail and tail != evidence_id:
        matches = [item for item in known_ids if item.endswith(f":{tail}")]
        if len(matches) == 1:
            return matches[0]
    matches = [item for item in known_ids if item.endswith(f":{evidence_id}")]
    return matches[0] if len(matches) == 1 else ""


def _normalize_evidence_refs(
    item: dict,
    known_ids: set[str],
    observation_refs: dict[str, str] | None = None,
) -> list[str]:
    normalized = []
    missing = []
    for evidence_id in _observation_refs(item):
        canonical = _canonical_evidence_id(evidence_id, known_ids, observation_refs)
        if canonical:
            normalized.append(canonical)
        else:
            missing.append(evidence_id)
    item["evidence"] = _dedupe_strings(normalized)
    return missing


def _validate_belief_refs(beliefs: list[dict], observations: list[dict]) -> None:
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    observation_refs = _observation_reference_map(observations)
    for belief in beliefs:
        missing = _normalize_evidence_refs(belief, evidence_ids, observation_refs)
        if missing:
            raise ValueError(
                f"belief {belief['id']} references unknown evidence ids: "
                + ", ".join(missing)
            )


def _drop_invalid_belief_refs(
    beliefs: list[dict],
    observations: list[dict],
    repairs: list[str],
) -> list[dict]:
    evidence_ids = {
        str(item.get("id") or "").strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    observation_refs = _observation_reference_map(observations)
    changed = False
    for belief in beliefs:
        original = belief.get("evidence", [])
        _normalize_evidence_refs(belief, evidence_ids, observation_refs)
        evidence = belief.get("evidence", [])
        if evidence != original:
            changed = True
            if not evidence:
                belief["status"] = "unverified"
    if changed:
        repairs.append("Dropped invalid belief evidence references during finalization repair")
    return beliefs


def _file_ref_matches(path: str, ref: str) -> bool:
    basename = ref.rsplit("/", 1)[-1]
    return path == ref or path.endswith("/" + ref) or path.endswith("/" + basename)


def _ref_is_existential(answer: str, match) -> bool:
    """True when a file mention is existential, not a behavioral claim.

    A mention is existential when the file name is not followed by a symbol
    or a behavior verb ("App.vue 是根组件", "the store lives in
    useSessions.js"). A behavioral claim attaches a symbol ("sessions.py 的
    generate_title") or a verb of effect ("HomePage.vue 调用
    generateSessionTitle"), which needs real reading.
    """
    tail = answer[match.end():match.end() + 80]
    if re.match(r"^\s*(?:的|中|里|内|文件|:)?\s*[A-Za-z_][A-Za-z0-9_]*\s*(?:\(\))?", tail):
        return False
    if re.search(
        r"调用|写入|返回|更新|修改|执行|处理|定义|实现|触发|发送|接收|渲染|"
        r"绑定|监听|创建|删除|设置|声明|初始化|导入|导出|请求|响应|加载|刷新|"
        r"显示|切换|注册|订阅|update|set|get|call|invoke|emit|handle|apply|"
        r"resolve|finish|render|write|return|send|receive|create|delete",
        tail,
        re.IGNORECASE,
    ):
        return False
    return True
