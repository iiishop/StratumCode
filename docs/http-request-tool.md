# HTTP request workbench

The API Inspector and `http_request` validation tool call the same workspace-scoped
service in `stratumcode/http_client.py`. The main validation prompt stays generic;
the tool schema describes its use, evidence limits, lifecycle and side effects.

## Reuse instead of another HTTP stack

HTTPX implements HTTP, TLS, request encoding and async transport. See
https://www.python-httpx.org/async/ and https://www.python-httpx.org/advanced/timeouts/.
StratumCode only supplies collection storage, evidence capture, bounded declarative
assertions and its existing Vue UI. A full separate Postman-like application would
duplicate workspace, permissions and model integration. No external UI is embedded.

## Capabilities

- GET/HEAD/POST/PUT/PATCH/DELETE/OPTIONS; duplicate query/header names.
- No body, UTF-8 text, JSON, or form URL encoding (name/value JSON array).
- Exact status/header assertions, literal body containment, JSON Pointer equality.
- Saved request definitions survive restart, scoped by workspace in SQLite.
- Send inline requests or saved IDs. Read previous observations without resending.
- Last 100 response captures across workspaces stay in host memory, not on disk.
- Agent observations appear in the same Inspector history as manual executions.

## Boundaries

TLS verification stays on. Redirects, retries, ambient proxy configuration and
cross-request cookies stay off. Each send uses an isolated HTTPX client. Only
HTTP(S) is allowed, including localhost for project API testing. This is not a
network sandbox: callers must authorize the endpoint and its data effects.
Timeout after a write does not mean the server rolled it back; inspect state
before retrying. No automatic cleanup requests are made.

HTTPX inactivity timeouts are combined with a 1-120 second total deadline.
Raw capture defaults to 32 KiB, maximum 256 KiB. Compressed, binary and non-UTF8
bodies return Base64; no decompression or response HTML/script execution occurs.
Body assertions on truncated or non-text captures are not evaluated. HTTP 4xx/5xx
are valid observations, not transport errors. No assertions means no pass verdict;
passed assertions only establish the stated checks, never the entire task.

Credential headers are redacted in responses; common credential headers cannot
be persisted in request definitions. Bearer auth references a process environment
variable by name. Other headers, URLs, query/body and response body may still hold
secrets: do not save such requests or expose such results to the model. Saved
definitions are plain text in the app DB. There is no secret vault in this version.

Not included: OAuth browser flows, cookie sessions, multipart file upload, response
downloads, WebSocket/SSE clients, assertion scripts, OpenAPI/Postman collection
import, or scheduled collection runs. No production API is contacted by tests.
