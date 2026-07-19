from __future__ import annotations

import time

from . import db

LANGUAGES = {
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
}
DEFAULT_OUTPUT_LANGUAGE = "zh"
DEFAULT_FONT_SCALE = 1.0
ROUND_LIMITS = {
    "task_analyzer_attempts": {
        "label": "Task analyzer attempts",
        "description": "Retry count for turning the user request into structured task JSON.",
    },
    "model_request_attempts": {
        "label": "Model request attempts",
        "description": "Retry count for transient provider HTTP failures.",
    },
    "investigation_rounds": {
        "label": "Investigation rounds",
        "description": "Model/tool rounds used while reading and understanding the workspace.",
    },
    "investigation_finalization_attempts": {
        "label": "Investigation finalization attempts",
        "description": "Retry count for producing the final structured investigation result.",
    },
    "evidence_rounds": {
        "label": "Evidence rounds",
        "description": "Model/tool rounds used by the hypothesis verifier.",
    },
    "evidence_empty_tool_rounds": {
        "label": "Evidence empty-tool rounds",
        "description": "Consecutive verifier rounds without tool calls before stopping.",
    },
    "implementation_rounds": {
        "label": "Implementation rounds",
        "description": "Model/tool rounds used while applying an authorized patch.",
    },
    "implementation_tool_error_rounds": {
        "label": "Implementation error rounds",
        "description": "Consecutive implementation rounds with only tool errors before asking.",
    },
    "validation_rounds": {
        "label": "Validation rounds",
        "description": "Model/tool rounds used by semantic validation.",
    },
    "design_json_attempts": {
        "label": "Design JSON attempts",
        "description": "Retry count for design planner JSON output.",
    },
    "patch_json_attempts": {
        "label": "Patch JSON attempts",
        "description": "Retry count for patch planner JSON output and repair.",
    },
    "installer_rounds": {
        "label": "Installer rounds",
        "description": "Model/tool rounds used by the MCP installer subagent.",
    },
}
TEXT = {
    "en": {
        "answer_context": "User answered a pending agent question.",
        "answered_question_id": "Answered question id: {id}",
        "question": "Question: {question}",
        "selected_option": "Selected option: {option}",
        "user_answer": "User answer: {answer}",
        "ready_patch": "Investigation is ready for patch planning.",
        "not_ready_patch": "Investigation did not reach patch planning readiness.",
        "belief_is": "Belief is {status}.",
        "resolves_unknown": "This answer resolves blocking unknown {id} before patch planning.",
        "resolves_open": "This answer resolves an open decision before patch planning.",
        "summary_beliefs": "Beliefs:",
        "summary_open_questions": "Open questions:",
        "summary_patch_context": "Patch planning context:",
        "any_key_label": "Any key",
        "any_key_value": "Allow users to set any keyboard key as the trigger.",
        "modifiers_only_label": "Modifiers only",
        "modifiers_only_value": "Only allow modifier keys such as Alt/Ctrl/Shift/Win as triggers.",
        "research_more_label": "Research more",
        "research_hotkey_value": "Continue investigating the existing hotkey design before deciding.",
        "pyobjc_label": "Use PyObjC/Quartz",
        "pyobjc_value": "Use PyObjC/Quartz Accessibility API for macOS window manipulation.",
        "applescript_label": "Use AppleScript",
        "applescript_value": "Use AppleScript/osascript for macOS window manipulation.",
        "research_window_value": "Continue researching macOS window manipulation before editing code.",
        "hash_passwords_label": "Hash passwords",
        "hash_passwords_value": "Passwords must be hashed; implement the project default securely.",
        "prototype_only_label": "Prototype only",
        "prototype_only_value": "This is only a prototype; do not add password hashing yet.",
        "ask_details_label": "Ask me details",
        "ask_details_value": "Ask for more password storage and authentication policy details first.",
        "best_practice_label": "Use best practice",
        "best_practice_value": "Use standard engineering best practice for this decision: {question}",
        "research_decision_value": "Continue investigating this decision before choosing: {question}",
    },
    "zh": {
        "answer_context": "用户回答了一个待处理的 agent 问题。",
        "answered_question_id": "已回答问题 id：{id}",
        "question": "问题：{question}",
        "selected_option": "选择的选项：{option}",
        "user_answer": "用户回答：{answer}",
        "ready_patch": "调查结果已经足够进入 patch planning。",
        "not_ready_patch": "调查还没有达到进入 patch planning 的条件。",
        "belief_is": "该判断的状态是 {status}。",
        "resolves_unknown": "这个回答会在 patch planning 前解决阻塞未知点 {id}。",
        "resolves_open": "这个回答会在 patch planning 前解决一个开放决策。",
        "summary_beliefs": "判断：",
        "summary_open_questions": "开放问题：",
        "summary_patch_context": "Patch planning 上下文：",
        "any_key_label": "任意键",
        "any_key_value": "允许用户设置任意键盘按键作为触发键。",
        "modifiers_only_label": "仅修饰键",
        "modifiers_only_value": "只允许 Alt/Ctrl/Shift/Win 这类修饰键作为触发键。",
        "research_more_label": "继续调查",
        "research_hotkey_value": "先继续调查项目现有快捷键设计，再决定触发键范围。",
        "pyobjc_label": "使用 PyObjC/Quartz",
        "pyobjc_value": "使用 PyObjC/Quartz Accessibility API 处理 macOS 窗口操作。",
        "applescript_label": "使用 AppleScript",
        "applescript_value": "使用 AppleScript/osascript 处理 macOS 窗口操作。",
        "research_window_value": "先继续调查 macOS 窗口操作方案，不要进入代码修改。",
        "hash_passwords_label": "哈希密码",
        "hash_passwords_value": "密码必须哈希存储，并按项目默认安全方式实现。",
        "prototype_only_label": "仅原型",
        "prototype_only_value": "这只是原型，暂时不要加入密码哈希。",
        "ask_details_label": "继续询问",
        "ask_details_value": "先询问更多密码存储和认证策略细节。",
        "best_practice_label": "采用最佳实践",
        "best_practice_value": "对这个决策采用工程最佳实践：{question}",
        "research_decision_value": "继续调查这个决策，不要先做选择：{question}",
    },
    "ja": {
        "answer_context": "ユーザーが保留中の agent の質問に回答しました。",
        "answered_question_id": "回答済み質問 id: {id}",
        "question": "質問: {question}",
        "selected_option": "選択した項目: {option}",
        "user_answer": "ユーザー回答: {answer}",
        "ready_patch": "調査結果は patch planning に進むのに十分です。",
        "not_ready_patch": "調査はまだ patch planning に進める状態ではありません。",
        "belief_is": "この判断の状態は {status} です。",
        "resolves_unknown": "この回答は patch planning の前にブロッキング unknown {id} を解決します。",
        "resolves_open": "この回答は patch planning の前に未決定事項を解決します。",
        "summary_beliefs": "判断:",
        "summary_open_questions": "未解決の質問:",
        "summary_patch_context": "Patch planning コンテキスト:",
        "any_key_label": "任意のキー",
        "any_key_value": "任意のキーボードキーをトリガーとして設定できるようにします。",
        "modifiers_only_label": "修飾キーのみ",
        "modifiers_only_value": "Alt/Ctrl/Shift/Win などの修飾キーだけをトリガーとして許可します。",
        "research_more_label": "さらに調査",
        "research_hotkey_value": "既存のホットキー設計をさらに調査してから決定します。",
        "pyobjc_label": "PyObjC/Quartz を使う",
        "pyobjc_value": "macOS のウィンドウ操作に PyObjC/Quartz Accessibility API を使います。",
        "applescript_label": "AppleScript を使う",
        "applescript_value": "macOS のウィンドウ操作に AppleScript/osascript を使います。",
        "research_window_value": "コード変更前に macOS のウィンドウ操作方式をさらに調査します。",
        "hash_passwords_label": "パスワードをハッシュ化",
        "hash_passwords_value": "パスワードはハッシュ化し、プロジェクト標準の安全な方法で実装します。",
        "prototype_only_label": "プロトタイプのみ",
        "prototype_only_value": "これはプロトタイプなので、まだパスワードハッシュ化は追加しません。",
        "ask_details_label": "詳細を確認",
        "ask_details_value": "先にパスワード保存と認証ポリシーの詳細を確認します。",
        "best_practice_label": "ベストプラクティス",
        "best_practice_value": "この判断には一般的なエンジニアリングのベストプラクティスを使います: {question}",
        "research_decision_value": "選択する前にこの判断をさらに調査します: {question}",
    },
}


def _init() -> None:
    with db.db_session() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )


def get_output_language() -> str:
    value = _get("output_language", DEFAULT_OUTPUT_LANGUAGE)
    return value if value in LANGUAGES else DEFAULT_OUTPUT_LANGUAGE


def save_output_language(language: str) -> str:
    value = str(language or "").strip().casefold()
    if value not in LANGUAGES:
        raise ValueError("output_language must be en, zh, or ja")
    _save("output_language", value)
    return value


def get_font_scale() -> float:
    try:
        value = float(_get("font_scale", str(DEFAULT_FONT_SCALE)))
    except (TypeError, ValueError):
        value = DEFAULT_FONT_SCALE
    return min(1.3, max(0.8, value))


def save_font_scale(value) -> float:
    try:
        scale = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("font_scale must be a number") from exc
    scale = min(1.3, max(0.8, scale))
    _save("font_scale", str(scale))
    return scale


def get_round_limit(key: str) -> int:
    if key not in ROUND_LIMITS:
        raise ValueError(f"unknown round limit setting: {key}")
    try:
        return max(0, int(_get(key, "0")))
    except (TypeError, ValueError):
        return 0


def save_round_limit(key: str, value) -> int:
    if key not in ROUND_LIMITS:
        raise ValueError(f"unknown round limit setting: {key}")
    try:
        limit = max(0, int(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a non-negative integer") from exc
    _save(key, str(limit))
    return limit


def to_json() -> dict:
    language = get_output_language()
    return {
        "output_language": language,
        "font_scale": get_font_scale(),
        "languages": [
            {"id": key, "label": label}
            for key, label in LANGUAGES.items()
        ],
        "round_limits": [
            {
                "key": key,
                "label": meta["label"],
                "description": meta["description"],
                "value": get_round_limit(key),
            }
            for key, meta in ROUND_LIMITS.items()
        ],
    }


def text(key: str, **kwargs) -> str:
    language = get_output_language()
    template = TEXT.get(language, TEXT[DEFAULT_OUTPUT_LANGUAGE]).get(key, TEXT["en"].get(key, key))
    return template.format(**kwargs)


def _get(key: str, default: str) -> str:
    _init()
    with db.db_session() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
    return row["value"] if row else default


def _save(key: str, value: str) -> None:
    _init()
    with db.db_session() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, time.time()),
        )
