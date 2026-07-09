from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlsplit

from ..spec import ToolDef, ToolResult


_WEBFETCH_LIMIT = 1_000_000
_WEBFETCH_OUTPUT_LIMIT = 8000
_SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "nav", "footer", "form", "button", "select"}
_BASE64_IMAGE_RE = re.compile(r"data:image/[^;]+;base64,[A-Za-z0-9+/=\s]+")


def _validate_web_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("webfetch only supports http and https URLs")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid webfetch URL")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve host: {parsed.hostname}") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        if not ip.is_global:
            raise PermissionError(f"webfetch blocks non-public address: {ip}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_web_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(html.unescape(data))


def _html_to_text(body: str) -> str:
    parser = _TextExtractor()
    parser.feed(body)
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _strip_base64_images(text: str) -> str:
    return _BASE64_IMAGE_RE.sub("[IMAGE]", text)


def _truncate_output(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    marker = f"\n\n... [truncated: showing head and tail of {len(text)} chars] ...\n\n"
    budget = max_chars - len(marker)
    if budget <= 0:
        return text[:max_chars], True
    head_chars = int(budget * 0.75)
    tail_chars = budget - head_chars
    return text[:head_chars].rstrip() + marker + text[-tail_chars:].lstrip(), True


def _fetch_raw(url: str) -> tuple[bytes, str]:
    _validate_web_url(url)
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/plain, text/markdown, text/html;q=0.8, */*;q=0.2",
            "User-Agent": "StratumCode/0.1",
        },
    )
    opener = urllib.request.build_opener(_SafeRedirectHandler)
    with opener.open(req, timeout=15) as resp:
        return resp.read(_WEBFETCH_LIMIT + 1), resp.headers.get("Content-Type", "")


async def _webfetch(params: dict, ctx: dict) -> ToolResult:
    url = params["url"]
    try:
        max_chars = min(20_000, max(500, int(params.get("max_chars") or _WEBFETCH_OUTPUT_LIMIT)))
        raw, content_type = await asyncio.to_thread(_fetch_raw, url)
    except Exception as e:
        return ToolResult.err("webfetch", str(e), error=e.__class__.__name__)
    if len(raw) > _WEBFETCH_LIMIT:
        return ToolResult.err("webfetch", f"response exceeds {_WEBFETCH_LIMIT} bytes", bytes=len(raw))
    body = raw.decode("utf-8", errors="replace")
    clean = _strip_base64_images(_html_to_text(body) if "html" in content_type.lower() else body)
    output, truncated = _truncate_output(clean, max_chars)
    return ToolResult.ok(
        f"fetch {url}",
        output,
        url=url,
        bytes=len(raw),
        chars=len(clean),
        output_chars=len(output),
        truncated=truncated,
        content_type=content_type,
    )


webfetch_tool = ToolDef(
    name="webfetch",
    description="Fetch content from a URL and return it as text.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "max_chars": {
                "type": "integer",
                "minimum": 500,
                "maximum": 20000,
                "description": "Maximum output characters",
            },
        },
        "required": ["url"],
    },
    execute=_webfetch,
)

TOOL = webfetch_tool
