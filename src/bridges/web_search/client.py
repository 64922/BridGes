"""DuckDuckGo 公网搜索客户端。

客户端只发送查询规划器产出的公开、最小查询词，不读取账户配置、附件或
用户画像；固定供应商也意味着不需要 API Key 或 ``.env`` 配置。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from bridges.web_search.contracts import WebSearchResult

DUCKDUCKGO_ENDPOINT = "https://api.duckduckgo.com/"


class WebSearchError(Exception):
    """可向用户展示的搜索失败。"""

    def __init__(self, code: str, message: str, *, permission: bool = False) -> None:
        self.code = code
        self.message = message
        self.permission = permission
        super().__init__(message)


class DuckDuckGoClient:
    """DuckDuckGo Instant Answer API 的同步、可替换客户端。"""

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        timeout: float = 8.0,
    ) -> None:
        self._client = http_client or httpx.Client(timeout=timeout)

    def search(self, query: str) -> list[WebSearchResult]:
        try:
            response = self._client.get(
                DUCKDUCKGO_ENDPOINT,
                params={
                    "q": query,
                    "format": "json",
                    "no_html": "1",
                    "no_redirect": "1",
                    "skip_disambig": "1",
                },
            )
        except httpx.TimeoutException as exc:
            raise WebSearchError(
                "web_search_timeout", "联网搜索超时，请重试。"
            ) from exc
        except httpx.HTTPError as exc:
            raise WebSearchError(
                "web_search_offline", "当前无法连接公网搜索，请检查网络后重试。"
            ) from exc

        if response.status_code == 429:
            raise WebSearchError("web_search_rate_limit", "公网搜索请求过于频繁，请稍后重试。")
        if response.status_code in {401, 403}:
            raise WebSearchError(
                "web_search_permission",
                "当前网络未允许访问公网搜索，请检查网络权限后重试。",
                permission=True,
            )
        if response.status_code >= 500:
            raise WebSearchError(
                "web_search_offline", "公网搜索暂时不可用，请稍后重试。"
            )
        if response.status_code >= 400:
            raise WebSearchError(
                "web_search_request", "公网搜索请求未完成，请重试。"
            )

        try:
            payload: Any = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise WebSearchError("web_search_parse", "搜索结果暂时无法解析，请重试。") from exc
        if not isinstance(payload, dict):
            raise WebSearchError("web_search_parse", "搜索结果暂时无法解析，请重试。")
        return _parse_results(payload)


def _parse_results(payload: dict[str, Any]) -> list[WebSearchResult]:
    """读取 DDG 公开链接，拒绝无真实 URL 的摘要。"""
    raw_items: list[dict[str, Any]] = []
    results = payload.get("Results")
    if isinstance(results, list):
        raw_items.extend(item for item in results if isinstance(item, dict))

    def collect(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("Topics"), list):
                collect(item["Topics"])
            else:
                raw_items.append(item)

    collect(payload.get("RelatedTopics"))
    accessed_at = datetime.now(UTC)
    parsed: list[WebSearchResult] = []
    seen_urls: set[str] = set()
    for item in raw_items:
        url = item.get("FirstURL")
        if (
            not isinstance(url, str)
            or not url.startswith(("https://", "http://"))
            or url in seen_urls
        ):
            continue
        title = item.get("Heading") or item.get("Text") or "公开网页"
        snippet = item.get("Text") or item.get("AbstractText") or ""
        if not isinstance(title, str) or not isinstance(snippet, str):
            continue
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if not host:
            continue
        seen_urls.add(url)
        parsed.append(
            WebSearchResult(
                result_id=f"web-{len(parsed) + 1}",
                title=title.strip() or "公开网页",
                site=host,
                url=url,
                snippet=snippet.strip(),
                accessed_at=accessed_at,
            )
        )
        if len(parsed) >= 5:
            break
    return parsed
