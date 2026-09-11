"""Workspace request collection and bounded HTTP observations, backed by HTTPX."""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from uuid import uuid4

import httpx

from . import db

_lock = threading.RLock()
_history = OrderedDict()
_sensitive = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"}
METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def _root(workspace):
    return os.path.normcase(str(Path(workspace).resolve()))


def _init(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS http_requests (id TEXT PRIMARY KEY, workspace TEXT NOT NULL, payload TEXT NOT NULL)")


def _pairs(value, field):
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f"{field} must contain at most 100 name/value pairs")
    for p in value:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not isinstance(p.get("value"), str):
            raise ValueError(f"{field} entries need string name and value")
        if not p["name"] or any(c in p["name"] + p["value"] for c in "\r\n\0"):
            raise ValueError(f"Invalid {field} pair")
    return [{"name": p["name"], "value": p["value"]} for p in value]


def normalize(value):
    if not isinstance(value, dict):
        raise ValueError("request must be an object")
    p = {key: copy.deepcopy(value.get(key, default)) for key, default in {
        "name": "Untitled request", "method": "GET", "url": "", "headers": [], "query": [],
        "body_type": "none", "body": "", "timeout_seconds": 20, "max_response_bytes": 32768,
        "bearer_env": "", "assertions": [],
    }.items()}
    if any(not isinstance(p[k], str) for k in ("name", "method", "url", "body_type", "body", "bearer_env")):
        raise ValueError("Request text fields must be strings")
    p["method"] = p["method"].upper()
    if p["method"] not in METHODS or p["body_type"] not in {"none", "text", "json", "form"}:
        raise ValueError("Unsupported method or body type")
    url = httpx.URL(p["url"])
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.fragment:
        raise ValueError("Use an absolute HTTP(S) URL without embedded credentials or fragments")
    for key, low, high in (("timeout_seconds", 1, 120), ("max_response_bytes", 256, 262144)):
        if type(p[key]) is not int or not low <= p[key] <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    if len(p["body"].encode("utf-8")) > 262144 or len(json.dumps(p)) > 400000:
        raise ValueError("Request is too large")
    p["headers"] = _pairs(p["headers"], "headers")
    p["query"] = _pairs(p["query"], "query")
    reserved = {"host", "content-length", "transfer-encoding", "connection", "accept-encoding"}
    if any(h["name"].lower() in reserved for h in p["headers"]):
        raise ValueError("Transport/framing headers are managed by HTTPX")
    if p["bearer_env"] and any(h["name"].lower() == "authorization" for h in p["headers"]):
        raise ValueError("Choose bearer_env or Authorization header, not both")
    if p["body_type"] == "json":
        json.loads(p["body"])
    if p["body_type"] == "form":
        _pairs(json.loads(p["body"]), "form")
    checks = p["assertions"]
    if not isinstance(checks, list) or len(checks) > 30:
        raise ValueError("At most 30 assertions are allowed")
    for c in checks:
        if not isinstance(c, dict) or c.get("kind") not in {"status", "header", "body_contains", "json_pointer"} or "expected" not in c:
            raise ValueError("Each assertion needs kind and expected")
        if c["kind"] == "status" and (type(c["expected"]) is not int or not 100 <= c["expected"] <= 599):
            raise ValueError("Status expectation must be an HTTP status integer")
        if c["kind"] in {"header", "body_contains"} and not isinstance(c["expected"], str):
            raise ValueError("Header/body expectation must be a string")
        if c["kind"] == "header" and (not isinstance(c.get("name"), str) or not c["name"]):
            raise ValueError("Header assertion needs name")
        if c["kind"] == "json_pointer" and (not isinstance(c.get("pointer"), str) or c["pointer"] and not c["pointer"].startswith("/")):
            raise ValueError("JSON pointer must be empty (whole document) or start with /")
        if c["kind"] == "json_pointer":
            pointer = c["pointer"]
            if any(pointer[i] == "~" and (i + 1 == len(pointer) or pointer[i + 1] not in "01") for i in range(len(pointer))):
                raise ValueError("JSON pointer escapes must be ~0 or ~1")
    return p


def snapshot(workspace):
    with db.db_session() as conn:
        _init(conn)
        saved = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM http_requests WHERE workspace=? ORDER BY id", (_root(workspace),))]
    with _lock:
        runs = [{k: r[k] for k in ("id", "name", "method", "created_at", "status_code", "error", "assertions_passed", "elapsed_ms")}
                for root, r in reversed(list(_history.values())) if root == _root(workspace)]
    return {"workspace": str(Path(workspace).resolve()), "requests": saved, "history": runs}


def save(workspace, value, request_id=""):
    p = normalize(value)
    if any(h["name"].lower() in _sensitive for h in p["headers"]):
        raise ValueError("Do not persist credential headers; use bearer_env or send without saving")
    with _lock, db.db_session() as conn:
        _init(conn)
        if request_id and not conn.execute("SELECT 1 FROM http_requests WHERE id=? AND workspace=?", (request_id, _root(workspace))).fetchone():
            raise ValueError("Unknown saved request in this workspace")
        p["id"] = request_id or "req-" + uuid4().hex[:12]
        conn.execute("INSERT OR REPLACE INTO http_requests VALUES (?, ?, ?)", (p["id"], _root(workspace), json.dumps(p)))
    return p


def _assertions(checks, response, text, complete):
    results = []
    for c in checks:
        state, actual = "passed", None
        try:
            if c["kind"] == "status":
                actual = response.status_code
            elif c["kind"] == "header":
                actual = response.headers.get(c["name"])
            elif not complete:
                state = "not_evaluated"
            elif c["kind"] == "body_contains":
                actual = c["expected"] in text
                state = "passed" if actual else "failed"
            else:
                actual = json.loads(text)
                for part in c["pointer"].split("/")[1:] if c["pointer"] else []:
                    part = part.replace("~1", "/").replace("~0", "~")
                    if isinstance(actual, list):
                        if not part.isdecimal() or (len(part) > 1 and part.startswith("0")):
                            raise ValueError("Invalid array index")
                        actual = actual[int(part)]
                    else:
                        actual = actual[part]
            if state != "not_evaluated" and c["kind"] != "body_contains":
                state = "passed" if json.dumps(actual, sort_keys=True) == json.dumps(c["expected"], sort_keys=True) else "failed"
        except (ValueError, KeyError, IndexError, TypeError):
            state = "failed"
        # Do not duplicate possibly sensitive response fragments into assertion history.
        results.append({"kind": c["kind"], "target": c.get("name", c.get("pointer", "")), "state": state})
    return results


async def send(workspace, value):
    p = normalize(value)
    headers = [(h["name"], h["value"]) for h in p["headers"]]
    headers.append(("Accept-Encoding", "identity"))
    if p["bearer_env"]:
        token = os.environ.get(p["bearer_env"])
        if not token:
            raise ValueError("Bearer environment variable is missing or empty")
        headers.append(("Authorization", "Bearer " + token))
    url = httpx.URL(p["url"])
    if p["query"]:
        # Preserve existing escapes/order, including signed URLs; append only new pairs.
        extra = str(httpx.QueryParams([(q["name"], q["value"]) for q in p["query"]])).encode("ascii")
        url = url.copy_with(query=url.query + (b"&" if url.query else b"") + extra)
    content = None
    if p["body_type"] != "none":
        content = p["body"].encode("utf-8")
        mime = {"json": "application/json", "text": "text/plain; charset=utf-8", "form": "application/x-www-form-urlencoded"}[p["body_type"]]
        if p["body_type"] == "form":
            content = str(httpx.QueryParams([(x["name"], x["value"]) for x in json.loads(p["body"])] )).encode("ascii")
        if not any(k.lower() == "content-type" for k, _ in headers):
            headers.append(("Content-Type", mime))
    result = {"id": "http-" + uuid4().hex[:12], "name": p["name"], "method": p["method"], "request_url": str(url),
              "created_at": time.time(), "status_code": None, "error": "", "headers": [], "body": "",
              "truncated": False, "assertions": [], "assertions_passed": None, "validation_observation": False}
    start = time.monotonic()
    try:
        # One isolated client per execution: no credential/cookie bleed across requests.
        async with asyncio.timeout(p["timeout_seconds"]):
            async with httpx.AsyncClient(timeout=p["timeout_seconds"], trust_env=False, follow_redirects=False) as client:
                async with client.stream(p["method"], url, headers=headers, content=content) as response:
                    result["status_code"] = response.status_code
                    result["validation_observation"] = True
                    result["headers"] = [{"name": k, "value": "[redacted]" if k.lower() in _sensitive else v} for k, v in response.headers.multi_items()]
                    data = bytearray()
                    # Bound raw bytes, not decompressed bytes: no decompression bombs or endless SSE.
                    async for chunk in response.aiter_raw():
                        remaining = p["max_response_bytes"] + 1 - len(data)
                        data.extend(chunk[:remaining])
                        if len(data) > p["max_response_bytes"]:
                            result["truncated"] = True
                            break
                    data = bytes(data[:p["max_response_bytes"]])
                    encoded = response.headers.get("content-encoding", "identity").lower() not in {"identity", ""}
                    try:
                        text = data.decode("utf-8") if not encoded else None
                    except UnicodeDecodeError:
                        text = None
                    result["body_encoding"] = "utf-8" if text is not None else "base64"
                    result["body"] = text if text is not None else base64.b64encode(data).decode("ascii")
                    result["captured_bytes"] = len(data)
                    result["assertions"] = _assertions(p["assertions"], response, text, text is not None and not result["truncated"])
                    if p["assertions"]:
                        result["assertions_passed"] = all(c["state"] == "passed" for c in result["assertions"])
    except (httpx.HTTPError, TimeoutError) as exc:
        result["error"] = type(exc).__name__ + ": request failed or deadline exceeded; no automatic retry"
        result["assertions"] = [{"kind": c["kind"], "state": "not_evaluated"} for c in p["assertions"]]
        if p["assertions"]:
            result["assertions_passed"] = False
    result["elapsed_ms"] = round((time.monotonic() - start) * 1000)
    with _lock:
        _history[result["id"]] = (_root(workspace), copy.deepcopy(result))
        while len(_history) > 100:
            _history.popitem(last=False)
    return result


async def action(workspace, params):
    action_name = params.get("action")
    if action_name == "list":
        return snapshot(workspace)
    if action_name == "save":
        return save(workspace, params.get("request"), params.get("request_id", ""))
    if action_name == "delete":
        with db.db_session() as conn:
            _init(conn)
            if not conn.execute("DELETE FROM http_requests WHERE id=? AND workspace=?", (params.get("request_id"), _root(workspace))).rowcount:
                raise ValueError("Unknown saved request in this workspace")
        return {"deleted": True}
    if action_name == "read":
        with _lock:
            entry = _history.get(params.get("run_id"))
            if not entry or entry[0] != _root(workspace):
                raise ValueError("Unknown response in this workspace (history lasts for this host process)")
            return copy.deepcopy(entry[1])
    if action_name == "send":
        value = params.get("request")
        if params.get("request_id"):
            if value is not None:
                raise ValueError("Choose request or request_id, not both")
            value = next((p for p in snapshot(workspace)["requests"] if p["id"] == params["request_id"]), None)
        return await send(workspace, value)
    raise ValueError("Unknown HTTP action")
