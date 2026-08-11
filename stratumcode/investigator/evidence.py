from __future__ import annotations

import os
import re
import sys
from collections.abc import Collection, Iterator

from ..tools import registry
from .constants import (
    GROUNDING_LITERAL_SPAN_MAX_ITEMS,
    LSP_DEFINITION_NOT_FOUND,
    OBSERVATION_EVIDENCE_CHARS,
    PROJECT_EVIDENCE_CAPABILITY,
    _FRAMEWORK_ROOTS,
    _NEGATIVE_CLAIM_RE,
    _PYTHON_STDLIB_MODULES,
)
from .domain import (
    _belief_status,
    _belief_text,
    _observation_grounding_literal_spans,
    _observation_reference_map,
    _observation_refs,
    _reference_list,
)
from .findings import _grounding_observation_text
from .ids import _normalize_unknown_id
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


def _lsp_definition_file_typed(path: str, symbol: str, workspace_dir: str) -> str:
    from . import _lsp_definition_file_typed as resolve

    return resolve(path, symbol, workspace_dir)


def _resolution_evidence_lines(unknown_ids: list[str], recorded: dict, observations: list[dict]) -> list[str]:
    wanted = set(unknown_ids)
    lines = []
    ref_by_id = _observation_ref_by_id(observations)
    for observation in observations:
        targets = {_normalize_unknown_id(item) for item in observation.get("target_unknown_ids", [])}
        if targets & wanted and _positive_project_observation(observation):
            observation_id = str(observation.get("id") or "").strip()
            ref = ref_by_id.get(observation_id, observation_id)
            lines.append(
                f"- observation ref {ref} (id {observation_id}): "
                f"{observation.get('title') or observation.get('summary')}"
            )
    for belief in recorded.get("beliefs", []):
        if isinstance(belief, dict) and _supporting_belief(belief):
            lines.append(f"- belief: {_belief_text(belief)}")
    return lines[-12:]


def _resolution_is_absence_claim(resolution: dict, observations: list[dict]) -> bool:
    """否定性结论（absence）判定：答案声称某物"不存在/未找到/未定义"，
    且引用了至少一条观察（grep/glob 无命中、读取文件确认缺失等）。

    absence 无法作为代码字面量被观察引用，质量门若仍要求字面量逐字命中，
    这类结论会永远判缺、触发 REPAIR 死循环（见 U2 类"预期未定义"问题）。
    """
    answer = str(resolution.get("answer") or "")
    if not _NEGATIVE_CLAIM_RE.search(answer):
        return False
    evidence_ids = set(_reference_list(resolution.get("evidence")))
    return any(
        isinstance(item, dict)
        and str(item.get("id") or "") in evidence_ids
        for item in observations or []
    )


def _grounding_unsupported_for_resolution(
    resolution: dict,
    recorded: dict,
    observations: list[dict],
) -> list[str]:
    """计算 resolution 未获观察支撑的代码字面量，带两类豁免：

    - derived_inference：推断本来就是模型的综合，不要求逐字引源码；
    - absence（否定性结论）：声称"不存在"的字面量无法在源码里被观察到。
    """
    unsupported = _unsupported_grounding_literals(resolution, recorded, observations or [])
    if unsupported and (
        str(resolution.get("kind") or "") == "derived_inference"
        or _resolution_is_absence_claim(resolution, observations or [])
    ):
        return []
    return unsupported


def _is_python_stdlib_module(name: str) -> bool:
    """标准库判断：优先用运行时权威列表（sys.stdlib_module_names，
    Python 3.10+ 内置、自动跟随版本），手写列表只作兜底。"""
    return name in _PYTHON_STDLIB_MODULES or name in getattr(sys, "stdlib_module_names", ())


def _is_framework_module(name: str) -> bool:
    """非 Python 语言/框架级根命名空间判断。"""
    return name in _FRAMEWORK_ROOTS


def _unsupported_grounding_literals(
    resolution: dict,
    recorded: dict,
    observations: list[dict],
) -> list[str]:
    evidence_ids = set(_reference_list(resolution.get("evidence")))
    belief_ids = set(_reference_list(resolution.get("belief_ids")))
    for belief in recorded.get("beliefs", []):
        if isinstance(belief, dict) and str(belief.get("id") or "") in belief_ids:
            evidence_ids.update(_reference_list(belief.get("evidence")))
    evidence_obs = [
        item
        for item in observations
        if isinstance(item, dict) and str(item.get("id") or "") in evidence_ids
    ]
    evidence = "\n".join(_grounding_observation_text(item) for item in evidence_obs)
    normalized_evidence = re.sub(r"\s+", "", evidence)
    literals = _grounding_code_literals(str(resolution.get("answer") or ""))
    unsupported = []
    for literal in literals:
        if not _is_grounding_code_literal(literal):
            continue
        normalized_literal = re.sub(r"\s+", "", literal)
        if normalized_literal in normalized_evidence:
            continue
        # 模块引用写法（useSessions.open / model_settings.resolve）豁免：
        # 源码里是 `export async function open` / `def resolve`，点链字符串
        # 本身不会出现在观察文本里。只要观察覆盖了对应源文件（path 命中
        # 模块名），就认为该引用已 grounded——否则模型永远补不出这个字面量，
        # REPAIR 死循环（useSessions.open 类根因）。
        if "." in literal:
            module = literal.split(".")[0]
            # 先查项目内同名源文件：项目真有这个模块（如项目自己的 utils.py
            # 或 System.cs）→ 模块文件豁免（类5），不进入框架判断。
            if _observation_covers_module(evidence_obs, module):
                continue
            # 语言/框架级根引用（cmath.sqrt / System.Math.Sqrt /
            # UnityEngine.Debug.Log / java.lang.Math.sqrt / console.log）：
            # 语言环境的一部分，项目里没有对应源文件，grep/read 永远
            # 产生不了这个字面量。要求观察证据毫无意义，直接放行。
            if _is_python_stdlib_module(module) or _is_framework_module(module):
                continue
            # LSP 兜底：框架列表没覆盖的（冷门语言/新框架），问 LSP 符号
            # 在项目里有没有定义。只有 server 正常返回"查不到"（NOT_FOUND）
            # 才按框架/外部引用豁免——这是可信信号。UNAVAILABLE（自动安装+
            # 退避重试后仍不可用）不豁免：宁可要求证据等环境修复，也不能把
            # 未观察的项目代码引用误放行（同名文件层只兜底被观察过的文件）。
            lsp_anchor = next(
                (item for item in evidence_obs if item.get("path")),
                None,
            )
            if lsp_anchor is not None:
                workspace = _infer_workspace_root(str(lsp_anchor.get("path") or ""))
                if workspace:
                    status = _lsp_definition_file_typed(
                        str(lsp_anchor.get("path")),
                        module,
                        workspace,
                    )
                    if status == LSP_DEFINITION_NOT_FOUND:
                        continue
        unsupported.append(literal)
    return _dedupe_strings(unsupported)


def _infer_workspace_root(path: str) -> str:
    """从文件路径向上找项目根（含 .git 或常见项目标记文件的目录）。"""
    current = os.path.dirname(os.path.abspath(path))
    markers = (".git", "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
               "*.csproj", "*.sln", "pom.xml", "build.gradle", "composer.json",
               "Gemfile", "mix.exs")
    while True:
        if any(
            os.path.exists(os.path.join(current, marker))
            or (marker.startswith("*.") and any(
                os.path.exists(os.path.join(current, name))
                for name in os.listdir(current)
                if name.endswith(marker[1:])
            ))
            for marker in markers
        ):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return ""
        current = parent


def _observation_covers_module(observations: list[dict], module: str) -> bool:
    """观察的 path 是否覆盖了给定模块（useSessions -> useSessions.js）。"""
    if not module:
        return False
    for item in observations:
        path = str(item.get("path") or "")
        if not path:
            continue
        base = os.path.basename(path)
        stem = os.path.splitext(base)[0]
        if module in (stem, base):
            return True
    return False


def _grounding_code_literals(value: str) -> list[str]:
    quoted = [
        literal
        for literal in re.findall(r"`([^`\r\n]+)`", value)
        if len(literal) <= 80
        and "..." not in literal
        and not literal.lstrip().startswith("<")
    ]
    return [
        literal
        for literal in _dedupe_strings([
            *quoted,
            *re.findall(
                r"""\bv-(?:if|show|for|else-if|else|bind|on|model|html|text|slot|pre|cloak|once|memo)"""
                r"""(?:\s*=\s*(?:"[^"\r\n]*"|'[^'\r\n]*'))?""",
                value,
            ),
            *re.findall(
                r"(?<![-A-Za-z0-9_$])[a-z_$][A-Za-z0-9_$]*"
                r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+"
                r"(?![-A-Za-z0-9_$])",
                value,
            ),
        ])
        if not _code_literal_is_negated(value, literal)
    ]


def _code_literal_is_negated(value: str, literal: str) -> bool:
    positions = [match.start() for match in re.finditer(re.escape(literal), value)]
    return bool(positions) and all(
        re.search(
            r"(?:非|无|没有|不存在|\bnot|\bno)\s*[\(（]?\s*$",
            value[max(0, index - 12):index].casefold(),
        )
        for index in positions
    )


def _bind_grounding_evidence(recorded: dict, observations: list[dict]) -> dict:
    for field, text_field in (("beliefs", "statement"), ("resolutions", "answer")):
        for item in recorded.get(field, []):
            if not isinstance(item, dict):
                continue
            supporting_ids = _supporting_observation_ids(
                _grounding_code_literals(str(item.get(text_field) or "")),
                observations,
            )
            if supporting_ids:
                item["evidence"] = _dedupe_strings([
                    *_reference_list(item.get("evidence")),
                    *supporting_ids,
                ])
    return recorded


def _supporting_observation_ids(
    literals: list[str],
    observations: list[dict],
) -> list[str]:
    if not literals:
        return []
    result = []
    for literal in literals:
        normalized = re.sub(r"\s+", "", literal)
        matches = [
            str(item.get("id") or "")
            for item in observations
            if isinstance(item, dict)
            and str(item.get("id") or "")
            and normalized in re.sub(
                r"\s+",
                "",
                _grounding_observation_text(item),
            )
        ]
        if not matches:
            return []
        result.extend(matches)
    return _dedupe_strings(result)


def _resolution_grounding_evidence_spans(
    resolution: dict,
    recorded: dict,
    observations: list[dict],
) -> list[dict]:
    literals = [
        literal
        for literal in _grounding_code_literals(str(resolution.get("answer") or ""))
        if _is_grounding_code_literal(literal)
    ]
    if not literals:
        return []
    evidence_ids = set(_reference_list(resolution.get("evidence")))
    belief_ids = set(_reference_list(resolution.get("belief_ids")))
    for belief in recorded.get("beliefs", []):
        if isinstance(belief, dict) and str(belief.get("id") or "") in belief_ids:
            evidence_ids.update(_reference_list(belief.get("evidence")))
    spans = [
        span
        for observation in observations
        if isinstance(observation, dict)
        and str(observation.get("id") or "") in evidence_ids
        for span in _observation_grounding_literal_spans(observation, literals)
    ]
    return spans[:GROUNDING_LITERAL_SPAN_MAX_ITEMS]


def _is_grounding_code_literal(value: str) -> bool:
    if value.casefold().endswith((
        ".cfg", ".css", ".html", ".ini", ".js", ".json", ".jsx", ".lock",
        ".md", ".py", ".toml", ".ts", ".tsx", ".txt", ".vue", ".yaml", ".yml",
    )):
        return False
    return value.startswith(("v-if", "v-show", "@")) or any(
        marker in value
        for marker in ("=", "<", ">", "(", ")", "{", "}", "[", "]", "/", "\\")
    ) or bool(
        re.fullmatch(
            r"[a-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+",
            value,
        )
    )
