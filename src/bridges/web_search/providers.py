"""已登记的结构化公开搜索备用提供方。"""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from threading import Event
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from bridges.config import Settings
from bridges.web_search.client import (
    WebSearchError,
    _connection_error_code,
    _http_status_category,
    _read_bounded,
    _remaining_timeout,
    _unsafe_url_code,
    _user_cancelled,
)
from bridges.web_search.contracts import (
    WebSearchHealth,
    WebSearchHealthStatus,
    WebSearchPageClassification,
    WebSearchResult,
    WebSearchVerification,
)

BRAVE_SEARCH_PROVIDER = "brave_search"
BRAVE_SEARCH_PROVIDER_VERSION = "brave-search-api-v1"
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
BRAVE_SEARCH_MAX_RESULTS = 5
BRAVE_SEARCH_MAX_RESPONSE_BYTES = 1_000_000
BRAVE_SEARCH_REQUEST_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
}
REGISTERED_FALLBACK_PROVIDERS = frozenset({BRAVE_SEARCH_PROVIDER})


class FallbackProviderConfigurationError(ValueError):
    """备用提供方配置不满足注册表合同。"""


class StructuredSearchResults(list[WebSearchResult]):
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


class BraveSearchClient:
    """Brave Search API 的固定端点、结构化结果适配器。"""

    provider_name = BRAVE_SEARCH_PROVIDER
    provider_version = BRAVE_SEARCH_PROVIDER_VERSION

    def __init__(
        self,
        *,
        api_key: SecretStr,
        http_client: httpx.Client | None = None,
        timeout: float = 8.0,
        max_results: int = BRAVE_SEARCH_MAX_RESULTS,
        max_response_bytes: int = BRAVE_SEARCH_MAX_RESPONSE_BYTES,
    ) -> None:
        self._api_key = api_key
        self._client = http_client or httpx.Client(timeout=timeout)
        self._timeout = timeout
        self._max_results = max(1, min(max_results, BRAVE_SEARCH_MAX_RESULTS))
        self._max_response_bytes = max_response_bytes

    def search(
        self,
        query: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
        stop_event: Event | None = None,
    ) -> list[WebSearchResult]:
        if not query.strip():
            raise WebSearchError(
                "web_search_fallback_request",
                "备用公网搜索查询不能为空。",
                retryable=False,
            )
        if not self._api_key.get_secret_value().strip():
            raise WebSearchError(
                "web_search_fallback_credentials",
                "备用公网搜索缺少部署凭据。",
                retryable=False,
            )
        if _user_cancelled(stop_event):
            raise WebSearchError("web_search_cancelled", "已取消本轮联网搜索。")
        if timeout is not None:
            relative_deadline = _now_monotonic() + timeout
            deadline = (
                relative_deadline
                if deadline is None
                else min(deadline, relative_deadline)
            )
        if deadline is not None and deadline <= _now_monotonic():
            raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")

        headers = {
            **BRAVE_SEARCH_REQUEST_HEADERS,
            "X-Subscription-Token": self._api_key.get_secret_value(),
        }
        try:
            with self._client.stream(
                "GET",
                BRAVE_SEARCH_ENDPOINT,
                params={"q": query, "count": self._max_results},
                headers=headers,
                follow_redirects=False,
                timeout=(
                    _remaining_timeout(deadline)
                    if deadline is not None
                    else self._timeout
                ),
            ) as response:
                self._validate_response(response)
                body = _read_bounded(response, self._max_response_bytes)
                status_category = _http_status_category(response.status_code)
        except httpx.TimeoutException as exc:
            raise WebSearchError(
                "web_search_fallback_timeout", "备用公网搜索超时，请稍后重试。"
            ) from exc
        except httpx.ConnectError as exc:
            base_code = _connection_error_code(exc)
            code = {
                "web_search_dns": "web_search_fallback_dns",
                "web_search_offline": "web_search_fallback_offline",
            }.get(base_code, "web_search_fallback_connect")
            raise WebSearchError(code, "当前无法连接备用公网搜索，请稍后重试。") from exc
        except httpx.HTTPError as exc:
            raise WebSearchError(
                "web_search_fallback_connect", "当前无法连接备用公网搜索，请稍后重试。"
            ) from exc

        if _user_cancelled(stop_event):
            raise WebSearchError("web_search_cancelled", "已取消本轮联网搜索。")
        if deadline is not None and deadline <= _now_monotonic():
            raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")
        return _parse_brave_results(body, status_category, self._max_results)

    def health_check(self) -> WebSearchHealth:
        """使用固定探针查询复用业务解析合同，不接受用户查询。"""

        checked_at = datetime.now(UTC)
        try:
            self.search("bridges-provider-health-check")
        except WebSearchError as exc:
            return WebSearchHealth(
                provider=self.provider_name,
                provider_version=self.provider_version,
                status=_health_status(exc.code),
                checked_at=checked_at,
                error_code=exc.code,
            )
        return WebSearchHealth(
            provider=self.provider_name,
            provider_version=self.provider_version,
            status=WebSearchHealthStatus.READY,
            checked_at=checked_at,
        )

    def _validate_response(self, response: httpx.Response) -> None:
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self._max_response_bytes:
                    raise WebSearchError(
                        "web_search_fallback_response_too_large",
                        "备用公网搜索响应过大，已拒绝处理。",
                        retryable=False,
                        http_status_category=_http_status_category(response.status_code),
                    )
            except ValueError:
                pass
        category = _http_status_category(response.status_code)
        if 300 <= response.status_code < 400:
            raise WebSearchError(
                "web_search_fallback_redirect",
                "备用公网搜索发生了不受控重定向，已拒绝处理。",
                retryable=False,
                http_status_category=category,
            )
        if response.status_code == 429:
            raise WebSearchError(
                "web_search_fallback_rate_limit",
                "备用公网搜索请求过于频繁，请稍后重试。",
                http_status_category=category,
            )
        if response.status_code in {401, 403}:
            raise WebSearchError(
                "web_search_fallback_permission",
                "备用公网搜索凭据或访问权限无效，请检查部署配置。",
                permission=True,
                http_status_category=category,
            )
        if response.status_code >= 500:
            raise WebSearchError(
                "web_search_fallback_provider",
                "备用公网搜索提供方暂时不可用，请稍后重试。",
                http_status_category=category,
            )
        if response.status_code >= 400:
            raise WebSearchError(
                "web_search_fallback_request",
                "备用公网搜索请求未完成，请重试。",
                http_status_category=category,
            )


def build_fallback_provider(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
) -> BraveSearchClient | None:
    """从显式部署配置构造已登记备用源，不接受用户端点。"""

    if not settings.public_search_fallback_enabled:
        return None
    provider = settings.public_search_fallback_provider.strip().lower()
    if provider not in REGISTERED_FALLBACK_PROVIDERS:
        raise FallbackProviderConfigurationError(
            f"未登记的备用公网搜索提供方：{provider or '空值'}。"
        )
    if settings.public_search_fallback_endpoint:
        raise FallbackProviderConfigurationError(
            "备用公网搜索不允许配置任意网络端点；请使用已登记提供方的固定端点。"
        )
    api_key = settings.brave_search_api_key
    if api_key is None or not api_key.get_secret_value().strip():
        raise FallbackProviderConfigurationError(
            "已启用 Brave Search 备用源，但未配置凭据 BRIDGES_BRAVE_SEARCH_API_KEY。"
        )
    return BraveSearchClient(api_key=api_key, http_client=http_client)


def _parse_brave_results(
    body: bytes, status_category: str, max_results: int
) -> StructuredSearchResults:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebSearchError(
            "web_search_fallback_parse",
            "备用公网搜索返回内容损坏，无法解析。",
            page_classification=WebSearchPageClassification.INVALID,
            http_status_category=status_category,
        ) from exc
    web_payload = payload.get("web") if isinstance(payload, dict) else None
    raw_results = (
        web_payload.get("results") if isinstance(web_payload, dict) else None
    )
    if not isinstance(raw_results, list):
        raise WebSearchError(
            "web_search_fallback_parse",
            "备用公网搜索返回结构不符合已登记合同。",
            page_classification=WebSearchPageClassification.INVALID,
            http_status_category=status_category,
        )
    if not raw_results:
        return StructuredSearchResults(
            [],
            page_classification=WebSearchPageClassification.NORMAL_EMPTY,
            http_status_category=status_category,
        )

    parsed: list[WebSearchResult] = []
    seen_urls: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"))
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
        parsed.append(
            WebSearchResult(
                result_id=f"brave-{len(parsed) + 1}",
                title=title,
                site=host,
                url=normalized_url,
                snippet=_clean_text(item.get("description")),
                content_summary=_clean_text(item.get("description")),
                accessed_at=datetime.now(UTC),
                verification=WebSearchVerification.STRUCTURED,
                provider=BRAVE_SEARCH_PROVIDER,
                provider_version=BRAVE_SEARCH_PROVIDER_VERSION,
            )
        )
    if not parsed:
        raise WebSearchError(
            "web_search_fallback_parse",
            "备用公网搜索未返回可安全引用的来源。",
            page_classification=WebSearchPageClassification.INVALID,
            http_status_category=status_category,
        )
    return StructuredSearchResults(
        parsed[:max_results],
        page_classification=WebSearchPageClassification.NORMAL_RESULTS,
        http_status_category=status_category,
    )


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _health_status(code: str) -> WebSearchHealthStatus:
    if code.endswith("_dns"):
        return WebSearchHealthStatus.DNS_ERROR
    if code.endswith("_connect") or code.endswith("_offline"):
        return WebSearchHealthStatus.CONNECT_ERROR
    if code.endswith("_permission") or code.endswith("_credentials"):
        return WebSearchHealthStatus.AUTH_ERROR
    if code.endswith("_rate_limit"):
        return WebSearchHealthStatus.RATE_LIMITED
    return WebSearchHealthStatus.UPSTREAM_ERROR


def _now_monotonic() -> float:
    """小 seam：便于单元测试只观察截止时间，不持有可变全局时钟。"""

    from time import monotonic

    return monotonic()
