from __future__ import annotations

import asyncio
import base64
import html
import urllib.request
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from ..spec import ToolDef, ToolResult


class _DuckDuckGoParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture = ""

    def _push_current(self) -> None:
        if self._current is None:
            return
        self._current = {key: value.strip() for key, value in self._current.items()}
        if self._current["title"] and self._current["url"]:
            self.results.append(self._current)
        self._current = None
        self._capture = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "a" and "result__a" in classes:
            self._push_current()
            self._current = {
                "title": "",
                "url": _decode_ddg_url(attrs.get("href", "")),
                "snippet": "",
            }
            self._capture = "title"
        elif self._current is not None and "result__snippet" in classes:
            self._capture = "snippet"

    def handle_endtag(self, tag):
        if tag == "a" and self._capture == "title":
            self._capture = ""
        elif self._current is not None and self._capture == "snippet" and tag in {"a", "div"}:
            self._push_current()

    def close(self):
        self._push_current()
        super().close()

    def handle_data(self, data):
        if self._current is not None and self._capture:
            self._current[self._capture] += html.unescape(data)


def _decode_ddg_url(url: str) -> str:
    query = parse_qs(urlsplit(url).query)
    return unquote(query.get("uddg", [url])[0])


def _decode_bing_url(url: str) -> str:
    query = parse_qs(urlsplit(url).query)
    target = query.get("u", [""])[0]
    if target.startswith("a1"):
        encoded = target[2:] + "=" * (-len(target[2:]) % 4)
        try:
            return base64.urlsafe_b64decode(encoded).decode("utf-8", errors="replace")
        except Exception:
            pass
    return url


class _BingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture = ""

    def _push_current(self) -> None:
        if self._current is None:
            return
        current = {key: " ".join(value.split()) for key, value in self._current.items()}
        if current["title"] and current["url"]:
            self.results.append(current)
        self._current = None
        self._capture = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "li" and "b_algo" in classes:
            self._push_current()
            self._current = {"title": "", "url": "", "snippet": ""}
            return
        if self._current is None:
            return
        if tag == "h2":
            self._capture = "title"
        elif tag == "a" and self._capture == "title" and not self._current["url"]:
            self._current["url"] = _decode_bing_url(html.unescape(attrs.get("href", "")))
        elif tag == "p" and not self._current["snippet"]:
            self._capture = "snippet"

    def handle_endtag(self, tag):
        if self._current is None:
            return
        if tag in {"h2", "p"}:
            self._capture = ""

    def handle_data(self, data):
        if self._current is not None and self._capture:
            self._current[self._capture] += html.unescape(data)

    def close(self):
        self._push_current()
        super().close()


async def _websearch(params: dict, ctx: dict) -> ToolResult:
    query = (params.get("query") or "").strip()
    limit = min(8, max(1, int(params.get("limit", 5))))
    if not query:
        return ToolResult.err("websearch", "query is required")
    failure = []
    try:
        results = await asyncio.to_thread(_search_ddg, query, limit)
    except Exception as exc:
        results = []
        failure.append(f"duckduckgo: {exc}")
    if not results:
        try:
            results = await asyncio.to_thread(_search_bing, query, limit)
        except Exception as exc:
            failure.append(f"bing: {exc}")
    if not results:
        return ToolResult.err(
            f"search {query}",
            "; ".join(failure) or "search provider returned no results",
        )
    output = "\n\n".join(
        f"{index}. {item['title']}\n{item['url']}\n{item['snippet']}"
        for index, item in enumerate(results, 1)
    )
    return ToolResult.ok(f"search {query}", output, count=len(results), query=query)


websearch_tool = ToolDef(
    name="websearch",
    description="Search the public web and return result titles, URLs, and snippets.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8,
                "description": "Maximum results",
            },
        },
        "required": ["query"],
    },
    execute=_websearch,
)

TOOL = websearch_tool


def _search_ddg(query: str, limit: int) -> list[dict[str, str]]:
    url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html",
            "User-Agent": "Mozilla/5.0 (compatible; StratumCode/0.1)",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read(500_000).decode("utf-8", errors="replace")
    parser = _DuckDuckGoParser()
    parser.feed(body)
    parser.close()
    return parser.results[:limit]


def _search_bing(query: str, limit: int) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?" + urlencode({"q": query})
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html",
            "User-Agent": "Mozilla/5.0 (compatible; StratumCode/0.1)",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read(500_000).decode("utf-8", errors="replace")
    parser = _BingParser()
    parser.feed(body)
    parser.close()
    return parser.results[:limit]
