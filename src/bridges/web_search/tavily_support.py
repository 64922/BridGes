"""Tavily 响应模型、解析和错误投影。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from bridges.web_search.client import WebSearchError, _unsafe_url_code
from bridges.web_search.contracts import (
    WebSearchHealthStatus,
    WebSearchPageClassification,
    WebSearchResult,
    WebSearchVerification,
)

TAVILY_SEARCH_PROVIDER = "tavily"
TAVILY_SEARCH_PROVIDER_VERSION = "tavily-search-api-v1"
_SOURCE_SUMMARY_MAX_CHARS = 4_000


class TavilySearchResults(list[WebSearchResult]):
    """带结构化响应分类的兼容结果列表。"""

    def __init__(
        self,
        results: list[WebSearchResult],
        *,
        page_classification: WebSearchPageClassification,
        http_status_category: str | None = None,
    ) -> None:
        super().__init__(results)
        self.page_classification = page_classification
        self.http_status_category = http_status_category


def parse_tavily_results(
    body: bytes,
    status_category: str,
    max_results: int,
) -> TavilySearchResults:
    """解析 Tavily 结构化响应；契约不符单独成类。"""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebSearchError(
            "web_search_contract",
            "搜索结果结构不符合已登记合同（Tavily），无法解析。",
            page_classification=WebSearchPageClassification.INVALID,
            http_status_category=status_category,
            retryable=False,
        ) from exc
    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        raise WebSearchError(
            "web_search_contract",
            "搜索结果结构不符合已登记合同（Tavily），无法解析。",
            page_classification=WebSearchPageClassification.INVALID,
            http_status_category=status_category,
            retryable=False,
        )
    if not raw_results:
        return TavilySearchResults(
            [],
            page_classification=WebSearchPageClassification.NORMAL_EMPTY,
            http_status_category=status_category,
        )

    accessed_at = datetime.now(UTC)
    parsed: list[WebSearchResult] = []
    seen_urls: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = clean_text(item.get("title"))
        url = item.get("url")
        if not title or not isinstance(url, str) or _unsafe_url_code(url) is not None:
            continue
        normalized_url = url.strip()
        if normalized_url in seen_urls:
            continue
        host = (urlparse(normalized_url).netloc or "").lower().removeprefix("www.")
        if not host:
            continue
        seen_urls.add(normalized_url)
        content = clean_text(item.get("content"))
        parsed.append(
            WebSearchResult(
                result_id=f"web-{len(parsed) + 1}",
                title=title,
                site=host,
                url=normalized_url,
                snippet=content,
                content_summary=content[:_SOURCE_SUMMARY_MAX_CHARS],
                accessed_at=accessed_at,
                verification=WebSearchVerification.STRUCTURED,
                provider=TAVILY_SEARCH_PROVIDER,
                provider_version=TAVILY_SEARCH_PROVIDER_VERSION,
            )
        )
    if not parsed:
        raise WebSearchError(
            "web_search_contract",
            "搜索结果未返回可安全引用的来源（Tavily）。",
            page_classification=WebSearchPageClassification.INVALID,
            http_status_category=status_category,
            retryable=False,
        )
    return TavilySearchResults(
        parsed[:max_results],
        page_classification=WebSearchPageClassification.NORMAL_RESULTS,
        http_status_category=status_category,
    )


def clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def bounded_text(value: str, max_chars: int) -> str:
    return value[:max_chars]


def health_status(code: str) -> WebSearchHealthStatus:
    if code == "web_search_credentials":
        return WebSearchHealthStatus.AUTH_ERROR
    if code.endswith("_dns"):
        return WebSearchHealthStatus.DNS_ERROR
    if code.endswith("_connect") or code.endswith("_offline"):
        return WebSearchHealthStatus.CONNECT_ERROR
    if code.endswith("_configuration") or code.endswith("_permission"):
        return WebSearchHealthStatus.AUTH_ERROR
    if code.endswith("_rate_limit"):
        return WebSearchHealthStatus.RATE_LIMITED
    if code.endswith("_quota"):
        # 用量上限与瞬时限流分开分类，页面文案据此区分重试建议。
        return WebSearchHealthStatus.RATE_LIMITED
    return WebSearchHealthStatus.UPSTREAM_ERROR


def raise_request_denial(denial: str) -> None:
    message = (
        "本轮运行预算已用尽，未发起新的公网搜索请求。"
        if denial == "run_budget_exhausted"
        else "任务已停止或发生变化，未发起新的公网搜索请求。"
    )
    raise WebSearchError(denial, message, retryable=False)
