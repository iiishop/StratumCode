"""LSP server management tool for the agent.

Allows the agent to check whether the mason environment is available and, when
it is, install the most suitable LSP server for a programming language. When
mason is unavailable the tool reports that LSP support is skipped entirely --
no npm fallback, no bootstrap attempt (the UI owns the mason bootstrap flow).
"""

from __future__ import annotations

import json

from ... import lsp
from ..spec import ToolDef, ToolResult

_STATUS_FIELDS = ("name", "display_name", "languages", "categories", "installed", "enabled", "available", "status", "install_version")


def _summary(server: dict) -> dict:
    return {field: server.get(field) for field in _STATUS_FIELDS if field in server}


def _mason_available() -> bool:
    try:
        return bool(lsp.mason_status().get("available"))
    except Exception:
        return False


def _pick_server(language: str) -> dict | None:
    """Choose the best LSP server for a language.

    Prefers an already installed/enabled server, then the first catalog entry
    for the language, then the first server whose languages list contains the
    language as a prefix match.
    """
    try:
        servers = lsp.list_all(language=language)
    except Exception:
        servers = []
    if not servers:
        try:
            servers = [
                server
                for server in lsp.list_all()
                if language.casefold() in (lang.casefold() for lang in server.get("languages", []))
            ]
        except Exception:
            servers = []
    if not servers:
        return None
    for server in servers:
        if server.get("status") == "ready":
            return server
    for server in servers:
        if server.get("installed"):
            return server
    return servers[0]


async def _lsp_tool(params: dict, ctx: dict) -> ToolResult:
    action = str(params.get("action") or "").strip()
    if action not in {"status", "install"}:
        return ToolResult.err("lsp_tool", f"unsupported action: {action}", action=action)

    if action == "status":
        payload = {
            "mason": {
                "available": _mason_available(),
                "note": "LSP definition lookups are skipped when mason is unavailable.",
            },
            "languages": [],
            "servers": [],
        }
        try:
            payload["languages"] = lsp.languages()
        except Exception:
            pass
        language = str(params.get("language") or "").strip()
        if language:
            try:
                payload["servers"] = [_summary(s) for s in lsp.list_all(language=language)]
            except Exception:
                payload["servers"] = []
        return ToolResult.ok(
            "lsp_tool status",
            json.dumps(payload, ensure_ascii=False, indent=2),
            action="status",
        )

    # action == "install"
    language = str(params.get("language") or "").strip()
    server_name = str(params.get("server") or "").strip()
    if not _mason_available():
        payload = {
            "ok": False,
            "skipped": True,
            "reason": "mason environment is unavailable; LSP support is skipped (no install attempted).",
            "mason": lsp.mason_status(),
        }
        return ToolResult.ok(
            "lsp_tool install skipped (no mason)",
            json.dumps(payload, ensure_ascii=False, indent=2),
            action="install",
            skipped=True,
        )
    if not server_name:
        if not language:
            return ToolResult.err("lsp_tool", "install requires either 'server' or 'language'", action="install")
        picked = _pick_server(language)
        if picked is None:
            return ToolResult.err(
                "lsp_tool",
                f"no LSP server found for language: {language}",
                action="install",
                language=language,
            )
        server_name = picked.get("name", "")
    try:
        existing = lsp.get(server_name) if hasattr(lsp, "get") else None
    except Exception:
        existing = None
    if isinstance(existing, dict) and existing.get("status") == "ready":
        return ToolResult.ok(
            f"lsp_tool install {server_name} (already ready)",
            json.dumps({"ok": True, "already_ready": True, "server": _summary(existing)}, ensure_ascii=False, indent=2),
            action="install",
            server=server_name,
            language=language,
        )
    try:
        server = lsp.install(server_name)
    except Exception as exc:
        return ToolResult.err(
            "lsp_tool",
            f"failed to install LSP server {server_name}: {exc}",
            action="install",
            server=server_name,
            language=language,
        )
    return ToolResult.ok(
        f"lsp_tool install {server_name}",
        json.dumps({"ok": True, "server": _summary(server)}, ensure_ascii=False, indent=2),
        action="install",
        server=server_name,
        language=language,
    )


lsp_tool_tool = ToolDef(
    name="lsp_tool",
    description=(
        "Manage LSP (language server) availability for semantic code navigation. "
        "Use operation 'status' to check whether the mason environment is available and which "
        "LSP servers exist for a language. Use operation 'install' to install the most suitable "
        "LSP server for a language (or a specific server by name). When mason is unavailable, "
        "install is skipped and LSP is simply not used."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "install"],
                "description": "status: check mason availability and list servers for a language. install: install the best LSP server for a language or a named server.",
            },
            "language": {
                "type": "string",
                "description": "Programming language (e.g. python, typescript, javascript, vue, css, html, json, bash).",
            },
            "server": {
                "type": "string",
                "description": "Explicit LSP server name to install (e.g. pyright, typescript-language-server). When omitted, the best server for 'language' is chosen.",
            },
        },
        "required": ["action"],
    },
    execute=_lsp_tool,
    capabilities=("investigation", "investigation.project_evidence"),
    event_type="lsp_tool",
)


TOOL = lsp_tool_tool
