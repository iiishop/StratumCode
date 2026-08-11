from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Collection, Iterator
from functools import lru_cache
from pathlib import Path

from ..tools import registry
from .constants import (
    CLEARIFY_RESOLUTION_REASON,
    GROUNDING_LITERAL_SPAN_MAX_ITEMS,
    LSP_DEFINITION_NOT_FOUND,
    LSP_DEFINITION_UNAVAILABLE,
    OBSERVATION_EVIDENCE_CHARS,
    PROJECT_EVIDENCE_CAPABILITY,
    PROJECT_FILE_SCAN_LIMIT,
    _DEF_READ_NOISE_SYMBOLS,
    _FILE_REF_RE,
    _FILE_SYMBOL_RE,
    _FRAMEWORK_ROOTS,
    _NEGATIVE_CLAIM_RE,
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
from .ids import _find_by_unknown_id, _normalize_unknown_id, _same_unknown_id
from .util import _dedupe_strings, _extension_language, _normalize_path


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


@lru_cache(maxsize=8)
def _workspace_file_catalog(workspace_dir: str) -> tuple[frozenset[str], frozenset[str]]:
    if not workspace_dir:
        return frozenset(), frozenset()
    try:
        from ..tools.builtin.common import _ignored

        root = Path(workspace_dir).resolve()
        if not root.is_dir():
            return frozenset(), frozenset()
        files: set[str] = set()
        stack = [root]
        while stack and len(files) < PROJECT_FILE_SCAN_LIMIT:
            current = stack.pop()
            try:
                children = list(current.iterdir())
            except OSError:
                continue
            for child in children:
                if _ignored(child, root):
                    continue
                if child.is_dir():
                    stack.append(child)
                    continue
                if not child.is_file():
                    continue
                rel = _normalize_path(child.relative_to(root).as_posix())
                if rel:
                    files.add(rel)
                if len(files) >= PROJECT_FILE_SCAN_LIMIT:
                    break
        basenames = {item.rsplit("/", 1)[-1] for item in files}
        return frozenset(files), frozenset(basenames)
    except Exception:
        return frozenset(), frozenset()


def _observed_file_paths(observations: list[dict]) -> set[str]:
    files = {
        _normalize_path(item.get("path") or "")
        for item in observations
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    }
    return {item for item in files if item}


def _project_file_ref(
    ref_raw: str,
    observations: list[dict],
    workspace_dir: str = "",
) -> str:
    if "://" in str(ref_raw):
        return ""
    ref = _normalize_path(ref_raw)
    if not ref or ref.endswith(".pyc"):
        return ""
    observed = _observed_file_paths(observations) | _hit_files_from_observations(observations)
    for path in sorted(observed, key=len):
        if _file_ref_matches(path, ref):
            return path
    files, _ = _workspace_file_catalog(str(workspace_dir or ""))
    for path in sorted(files, key=len):
        if _file_ref_matches(path, ref):
            return path
    return ""


def _hit_files_from_observations(observations: list[dict]) -> set[str]:
    """Files confirmed to exist / contain a match via grep/glob observations.

    A grep hit line has the form ``path:line:content`` and a glob result is a
    bare path. A file listed there is *known to exist* (and to contain the
    searched symbol), which is enough for existential mentions ("App.vue is
    the root component") even though its full behavior was never read.
    """
    files: set[str] = set()
    for item in observations:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "")
        if tool not in {"grep", "glob"}:
            continue
        evidence = item.get("_grounding_evidence") or item.get("output") or ""
        if not isinstance(evidence, str):
            continue
        for line in evidence.splitlines():
            line = line.strip()
            if not line:
                continue
            if tool == "glob":
                files.add(_normalize_path(line))
                continue
            # grep: "path:line:content" — path may itself contain colons (Windows)
            head = line.split(":", 2)[0] if line.count(":") >= 2 else line
            if head:
                files.add(_normalize_path(head))
    return files


def _require_file_reads(
    resolutions: list[dict],
    observations: list[dict],
    workspace_dir: str = "",
) -> list[str]:
    """Return issues when a resolution claims a project file's behavior without
    any observation covering it.

    The answer text is scanned for references that resolve to real project
    files (sessions.py, frontend/.../HomePage.vue, config schemas, etc.).
    References that carry a symbol or a
    behavioral claim ("sessions.py's generate_title writes the name field")
    require an observation of that file: a ``read``, or a ``code_nav`` /
    ``lsp_tool`` query whose result locates the file, or a grep/glob hit.
    A bare existential mention ("App.vue is the root component") only requires
    the file to be known -- grep/glob hits are sufficient evidence the file
    exists and contains the searched symbol. Candidate paths are filtered
    through observed paths and the workspace file catalog, so dotted
    identifiers such as ``props.sessions`` are not treated as files.
    """
    observed_files: set[str] = set()
    for item in observations:
        if not isinstance(item, dict):
            continue
        if str(item.get("tool") or "") not in {"read", "code_nav", "lsp_tool", "grep", "glob"}:
            continue
        path = _normalize_path(item.get("path") or "")
        if path:
            observed_files.add(path)
    if not observed_files:
        return []
    hit_files = observed_files | _hit_files_from_observations(observations)
    issues: list[str] = []
    for resolution in resolutions:
        if not isinstance(resolution, dict):
            continue
        unknown_id = str(resolution.get("unknown_id") or "?")
        answer = " ".join(str(resolution.get(field) or "") for field in ("answer", "reason"))
        for match in _FILE_REF_RE.finditer(answer):
            ref_raw = match.group(1)
            ref = _project_file_ref(ref_raw, observations, workspace_dir)
            if not ref:
                continue
            observed_matched = any(
                _file_ref_matches(rf, ref)
                for rf in observed_files
            )
            if observed_matched:
                continue
            if _ref_is_existential(answer, match) and any(
                _file_ref_matches(rf, ref)
                for rf in hit_files
            ):
                continue
            issues.append(
                f"resolution {unknown_id} references file {ref_raw} "
                "but no read observation covers it"
            )
    return issues


def _require_lsp_definition_reads(
    resolutions: list[dict],
    observations: list[dict],
    workspace_dir: str,
    max_queries: int = 6,
) -> list[str]:
    """Use LSP to resolve the true definition file of symbols the answer
    claims, then require a read observation covering that file.

    A plain file-level check (``_require_file_reads``) only catches answers
    that name a file which was never read. It misses the more common failure:
    the model names the file it *did* read (e.g. investigating.py) while the
    symbol's actual definition lives elsewhere (e.g. task_updates.py). LSP
    definition lookup follows the import and points at the real definition
    file, so the check enforces that the model read the file where the symbol
    is actually defined, not just where it was mentioned.

    The check is best-effort: LSP is a subprocess, so every failure (server
    not installed, timeout, unknown symbol) silently skips that symbol.
    """
    read_files: set[str] = set()
    for item in observations:
        if not isinstance(item, dict) or str(item.get("tool") or "") != "read":
            continue
        path = _normalize_path(item.get("path") or "")
        if path:
            read_files.add(path)
    if not read_files:
        return []
    issues: list[str] = []
    queried = 0
    for resolution in resolutions:
        if not isinstance(resolution, dict):
            continue
        unknown_id = str(resolution.get("unknown_id") or "?")
        answer = " ".join(str(resolution.get(field) or "") for field in ("answer", "reason"))
        for match in _FILE_SYMBOL_RE.finditer(answer):
            if queried >= max_queries:
                return issues
            ref_raw, symbol = match.group(1), match.group(2)
            ref = _project_file_ref(ref_raw, observations, workspace_dir)
            if not ref or ref.endswith(".pyc") or symbol in _DEF_READ_NOISE_SYMBOLS:
                continue
            definition_file = _lsp_definition_file(ref, symbol, workspace_dir)
            queried += 1
            if not definition_file:
                continue
            # 标准库/typeshed/第三方依赖的定义文件不在工作区内——这类符号
            # （math、numpy、PyPI 包…）不可能也不应该被 read 过，直接跳过。
            ws_files, _ = _workspace_file_catalog(workspace_dir)
            if not ws_files:
                continue
            try:
                rel_def = _normalize_path(
                    Path(definition_file).resolve().relative_to(
                        Path(workspace_dir).resolve()
                    ).as_posix()
                )
            except (ValueError, OSError):
                continue  # 不在工作区内
            if rel_def not in ws_files:
                continue
            norm_def = _normalize_path(definition_file)
            matched = any(
                _file_ref_matches(rf, norm_def)
                for rf in read_files
            )
            if not matched:
                issues.append(
                    f"resolution {unknown_id} claims behavior of {symbol}() "
                    f"(defined in {definition_file}) but that file was never read"
                )
    return issues


def _lsp_definition_file(path: str, symbol: str, workspace_dir: str) -> str | None:
    """Resolve the file where ``symbol`` is defined via the code_nav LSP tool.

    Returns the absolute definition file path, or None when LSP is unavailable
    or the symbol cannot be resolved (best-effort). 兼容旧调用方：
    路径 与 三态中的 NOT_FOUND/UNAVAILABLE 统一折叠为 None。
    """
    status = _lsp_definition_file_typed(path, symbol, workspace_dir)
    if status in (LSP_DEFINITION_NOT_FOUND, LSP_DEFINITION_UNAVAILABLE):
        return None
    return status if isinstance(status, str) else None


@lru_cache(maxsize=256)
def _lsp_definition_file_typed(path: str, symbol: str, workspace_dir: str) -> str:
    """三态 LSP 定义查询：定义文件路径 / NOT_FOUND / UNAVAILABLE。

    - 查到定义文件：符号是项目代码（不豁免，且定义文件可作为读证据要求）。
    - NOT_FOUND：server 正常但符号无定义 —— 框架/外部引用的强信号（豁免）。
    - UNAVAILABLE：server 没装/无法启动 —— 自动安装（lsp_tool install by
      language）并指数退避重试（1s/2s/4s）；仍不可用返回 UNAVAILABLE。
      调用方按"框架引用"豁免（同名文件层已兜底项目代码，不会误放行）。
    """
    for attempt in range(3):
        result = _lsp_definition_file_once(path, symbol, workspace_dir)
        if result is not LSP_DEFINITION_UNAVAILABLE:
            return result
        if attempt < 2:
            _try_install_lsp_for_path(path, workspace_dir)
            time.sleep(1.0 * (2 ** attempt))  # 1s, 2s
    return LSP_DEFINITION_UNAVAILABLE


def _try_install_lsp_for_path(path: str, workspace_dir: str) -> bool:
    """按文件扩展名推断语言，自动安装最合适的 LSP server（mason）。

    失败静默（返回 False）——LSP 安装是 best-effort，装不上就走
    UNAVAILABLE 分支，由调用方按框架引用豁免兜底。
    """
    try:
        from .. import lsp

        language = _extension_language(path)
        if not language:
            return False
        candidates = lsp.list_all(language=language)
        if not candidates:
            return False
        picked = next(
            (item for item in candidates if item.get("available")),
            candidates[0],
        )
        name = str(picked.get("name") or "").strip()
        if not name:
            return False
        existing = lsp.get(name) if hasattr(lsp, "get") else None
        if isinstance(existing, dict) and existing.get("status") == "ready":
            return True
        result = lsp.install(name)
        return bool(isinstance(result, dict) and result.get("ok"))
    except Exception:
        return False


def _lsp_definition_file_once(path: str, symbol: str, workspace_dir: str) -> str:
    """单次 LSP 定义查询，返回三态（不重试不安装）。"""
    try:
        import asyncio

        from ..tools.builtin import code_nav
        from ..tools.spec import ToolResult

        result: ToolResult = asyncio.run(
            code_nav.code_nav_tool.execute(
                {"operation": "definition", "path": path, "symbol": symbol},
                {"directory": workspace_dir},
            )
        )
    except Exception:
        return LSP_DEFINITION_UNAVAILABLE
    if not isinstance(result, ToolResult) or result.title.startswith("[error]"):
        return LSP_DEFINITION_UNAVAILABLE
    try:
        payload = json.loads(result.output)
    except (json.JSONDecodeError, TypeError):
        return LSP_DEFINITION_UNAVAILABLE
    if not isinstance(payload, dict):
        return LSP_DEFINITION_UNAVAILABLE
    if not payload.get("ok"):
        message = str(payload.get("message") or "").casefold()
        unavailable = any(
            token in message
            for token in (
                "lsp server not found",
                "no lsp server",
                "no enabled available lsp server",
                "server not available",
                "executable is unavailable",
                "not installed",
                "no server",
                "could not start",
                "unable to connect",
                "not found",
                "is not installed",
            )
        )
        if unavailable or payload.get("kind") == "error":
            return LSP_DEFINITION_UNAVAILABLE
        return LSP_DEFINITION_NOT_FOUND
    result_val = payload.get("result")
    items = None
    if isinstance(result_val, dict):
        items = result_val.get("items")
    elif isinstance(result_val, list):
        items = result_val
    if isinstance(items, list) and items:
        loc = items[0]
        if isinstance(loc, dict):
            raw_path = loc.get("path") or loc.get("uri")
            if raw_path:
                raw = str(raw_path)
                if raw.startswith("file://"):
                    from urllib.parse import unquote, urlparse

                    parsed = urlparse(raw)
                    raw = unquote(parsed.path)
                    if re.match(r"^/[A-Za-z]:", raw):
                        raw = raw[1:]
                return raw.replace("/", "\\") if "\\" in str(__import__("pathlib").Path.cwd()) else raw
    return None



def _enforce_resolution_evidence(resolutions: list[dict], initial_unknowns: list[dict], *, strict: bool = True) -> list[dict]:
    if not strict:
        return resolutions
    for resolution in resolutions:
        source = _find_by_unknown_id(initial_unknowns, resolution["unknown_id"], id_field="id")
        if not source:
            continue
        if (
            source.get("blocking")
            and source.get("resolution_strategy") == "investigate_project"
            and resolution.get("status") == "resolved"
            and not (resolution.get("evidence") or resolution.get("belief_ids"))
            and not _is_user_product_decision(resolution, initial_unknowns)
        ):
            resolution["status"] = "partially_resolved"
            resolution["reason"] = resolution.get("reason") or "Resolved codebase facts require evidence or belief references."
    return resolutions


def _is_user_product_decision(
    resolution: dict,
    unknowns: list[dict],
) -> bool:
    if resolution.get("reason") != CLEARIFY_RESOLUTION_REASON:
        return False
    unknown_id = str(resolution.get("unknown_id") or "").strip()
    return any(
        _same_unknown_id(item.get("id"), unknown_id)
        and item.get("type") in ("product_decision", "engineering_decision")
        for item in unknowns
        if isinstance(item, dict)
    )


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
    return name in getattr(sys, "stdlib_module_names", ())


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
