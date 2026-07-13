from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from . import db

AUTHORIZATION_TTL_SECONDS = 60 * 60 * 6


SCHEMA = """
CREATE TABLE IF NOT EXISTS patch_authorizations (
    id TEXT PRIMARY KEY,
    workspace_root TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    allowed_steps_json TEXT NOT NULL,
    active INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL DEFAULT 0,
    consumed_steps_json TEXT NOT NULL DEFAULT '[]',
    step_states_json TEXT NOT NULL DEFAULT '{}',
    applied_attempts_json TEXT NOT NULL DEFAULT '{}'
)
"""


def create_authorization(plan: dict, workspace_dir: str) -> dict:
    _init()
    root = Path(workspace_dir or ".").resolve()
    plan_hash = hash_plan(plan)
    auth_id = "EA-" + hashlib.sha256(f"{root}|{plan_hash}|{time.time()}".encode("utf-8")).hexdigest()[:12]
    allowed_steps = _allowed_steps(plan, root)
    now = time.time()
    auth = {
        "authorization_id": auth_id,
        "plan_hash": plan_hash,
        "workspace_root": str(root),
        "allowed_steps": allowed_steps,
        "expires_after_state_change": True,
        "expires_at": now + AUTHORIZATION_TTL_SECONDS,
    }
    with db.db_session() as conn:
        conn.execute(
            """
            INSERT INTO patch_authorizations (
                id, workspace_root, plan_hash, allowed_steps_json, active, created_at, expires_at, consumed_steps_json, step_states_json, applied_attempts_json
            )
            VALUES (?, ?, ?, ?, 1, ?, ?, '[]', ?, '{}')
            """,
            (
                auth_id,
                _root_key(root),
                plan_hash,
                json.dumps(allowed_steps, ensure_ascii=False),
                now,
                auth["expires_at"],
                json.dumps({step_id: "pending" for step_id in allowed_steps}, ensure_ascii=False),
            ),
        )
    return auth


def validate_request(request: dict, root: Path, changed_bytes: int = 0) -> None:
    auth_id = str(request.get("authorization_id") or "").strip()
    if not auth_id:
        raise AuthorizationError("AUTHORIZATION_NOT_FOUND", "authorization_id is required")
    row = _get(auth_id)
    if not row or not row.get("active"):
        raise AuthorizationError("AUTHORIZATION_NOT_FOUND", auth_id)
    if float(row.get("expires_at") or 0) and time.time() > float(row["expires_at"]):
        raise AuthorizationError("AUTHORIZATION_EXPIRED", "authorization expired")
    if row["workspace_root"] != _root_key(root):
        raise AuthorizationError("AUTHORIZATION_EXPIRED", "authorization belongs to a different workspace")
    if str(request.get("plan_hash") or "").strip() != row["plan_hash"]:
        raise AuthorizationError("PLAN_HASH_MISMATCH", "patch request plan_hash does not match authorization")
    step_id = str(request.get("step_id") or "").strip()
    steps = json.loads(row["allowed_steps_json"])
    step = steps.get(step_id)
    if not step:
        raise AuthorizationError("STEP_NOT_AUTHORIZED", step_id)
    attempts = _loads(row.get("applied_attempts_json"), {})
    attempt_id = str(request.get("attempt_id") or request.get("patch_id") or "").strip()
    if attempt_id and attempt_id in set(attempts.get(step_id) or []):
        raise AuthorizationError("PATCH_ATTEMPT_ALREADY_APPLIED", attempt_id)
    requested_acceptance = set(_strings(request.get("acceptance_ids")))
    allowed_acceptance = set(step.get("acceptance_ids") or [])
    if requested_acceptance and not requested_acceptance <= allowed_acceptance:
        raise AuthorizationError("ACCEPTANCE_NOT_AUTHORIZED", ", ".join(sorted(requested_acceptance - allowed_acceptance)))
    files = request.get("files") or []
    allowed_files = {_path_key(path) for path in step.get("files") or []}
    capabilities = set(step.get("capabilities") or [])
    for file_patch in files:
        rel = str(file_patch.get("path") or "").strip()
        if _path_key(rel) not in allowed_files:
            allowed = ", ".join(step.get("files") or [])
            raise AuthorizationError("PATH_NOT_AUTHORIZED", f"{rel} is not authorized for step {step_id}. Allowed files: {allowed}")
        mode = str(file_patch.get("mode") or "modify")
        if mode == "create" and "create_file" not in capabilities:
            raise AuthorizationError("OPERATION_NOT_AUTHORIZED", "create_file")
        if mode != "create" and "modify_existing" not in capabilities:
            raise AuthorizationError("OPERATION_NOT_AUTHORIZED", f"modify_existing is not authorized for step {step_id}")
    if changed_bytes > int(step.get("max_changed_bytes") or 0):
        raise AuthorizationError("PATCH_TOO_LARGE", "changed bytes exceed step authorization")


def mark_step_applied(auth_id: str, step_id: str, attempt_id: str = "") -> None:
    row = _get(auth_id)
    if not row:
        raise AuthorizationError("AUTHORIZATION_NOT_FOUND", auth_id)
    consumed = set(_loads(row.get("consumed_steps_json"), []))
    states = _loads(row.get("step_states_json"), {})
    attempts = _loads(row.get("applied_attempts_json"), {})
    step_id = str(step_id)
    attempt_id = str(attempt_id or "")
    consumed.add(step_id)
    states[step_id] = "validation_required"
    if attempt_id:
        values = set(attempts.get(step_id) or [])
        values.add(attempt_id)
        attempts[step_id] = sorted(values)
    with db.db_session() as conn:
        conn.execute(
            "UPDATE patch_authorizations SET consumed_steps_json = ?, step_states_json = ?, applied_attempts_json = ? WHERE id = ?",
            (
                json.dumps(sorted(consumed), ensure_ascii=False),
                json.dumps(states, ensure_ascii=False),
                json.dumps(attempts, ensure_ascii=False),
                auth_id,
            ),
        )


def mark_step_rolled_back(auth_id: str, step_id: str, attempt_id: str = "") -> None:
    row = _get(auth_id)
    if not row:
        return
    consumed = set(_loads(row.get("consumed_steps_json"), []))
    states = _loads(row.get("step_states_json"), {})
    attempts = _loads(row.get("applied_attempts_json"), {})
    step_id = str(step_id)
    attempt_id = str(attempt_id or "")
    consumed.discard(step_id)
    states[step_id] = "rolled_back"
    if attempt_id:
        values = set(attempts.get(step_id) or [])
        values.discard(attempt_id)
        attempts[step_id] = sorted(values)
    with db.db_session() as conn:
        conn.execute(
            "UPDATE patch_authorizations SET consumed_steps_json = ?, step_states_json = ?, applied_attempts_json = ? WHERE id = ?",
            (
                json.dumps(sorted(consumed), ensure_ascii=False),
                json.dumps(states, ensure_ascii=False),
                json.dumps(attempts, ensure_ascii=False),
                auth_id,
            ),
        )


def step_states(auth_id: str) -> dict:
    row = _get(auth_id)
    return _loads(row.get("step_states_json"), {}) if row else {}


def hash_plan(plan: dict) -> str:
    payload = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuthorizationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _allowed_steps(plan: dict, root: Path) -> dict:
    acceptance_by_step: dict[str, set[str]] = {}
    for item in plan.get("acceptance_mapping") or []:
        acceptance_id = str(item.get("acceptance_id") or "").strip()
        for step_id in item.get("covered_by") or []:
            acceptance_by_step.setdefault(str(step_id), set()).add(acceptance_id)
    steps = {}
    for item in plan.get("implementation_steps") or []:
        step_id = str(item.get("id") or "").strip()
        rels = _strings(item.get("files")) or [str(item.get("file") or "").strip()]
        rels = [rel for rel in rels if rel]
        if not step_id or not rels:
            continue
        capabilities = set()
        for rel in rels:
            target = (root / rel).resolve()
            capabilities.add("modify_existing" if target.exists() else "create_file")
        steps[step_id] = {
            "files": rels,
            "purpose": str(item.get("purpose") or "").strip(),
            "target": str(item.get("target") or "").strip(),
            "action": str(item.get("action") or "").strip(),
            "acceptance_ids": sorted(set(_strings(item.get("acceptance_ids"))) | (acceptance_by_step.get(step_id) or set())),
            "decision_ids": _strings(item.get("decision_ids")),
            "project_fact_ids": _strings(item.get("project_fact_ids")),
            "required_behavior_if_removed": str(item.get("required_behavior_if_removed") or "").strip(),
            "completion_conditions": _strings(item.get("completion_conditions")),
            "out_of_scope": _strings(item.get("out_of_scope")),
            "capabilities": sorted(capabilities),
            "max_changed_bytes": 20_000,
        }
    return steps


def _get(auth_id: str) -> dict | None:
    _init()
    with db.db_session() as conn:
        row = conn.execute("SELECT * FROM patch_authorizations WHERE id = ?", (auth_id,)).fetchone()
    return dict(row) if row else None


def _init() -> None:
    with db.db_session() as conn:
        conn.execute(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(patch_authorizations)").fetchall()}
        if "expires_at" not in columns:
            conn.execute("ALTER TABLE patch_authorizations ADD COLUMN expires_at REAL NOT NULL DEFAULT 0")
        if "consumed_steps_json" not in columns:
            conn.execute("ALTER TABLE patch_authorizations ADD COLUMN consumed_steps_json TEXT NOT NULL DEFAULT '[]'")
        if "step_states_json" not in columns:
            conn.execute("ALTER TABLE patch_authorizations ADD COLUMN step_states_json TEXT NOT NULL DEFAULT '{}'")
        if "applied_attempts_json" not in columns:
            conn.execute("ALTER TABLE patch_authorizations ADD COLUMN applied_attempts_json TEXT NOT NULL DEFAULT '{}'")


def _root_key(root: Path) -> str:
    return os.path.normcase(str(root.resolve()))


def _path_key(path: str) -> str:
    return os.path.normcase(str(path).replace("\\", "/"))


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for raw in value if (item := str(raw).strip())]


def _loads(value, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback
