"""Tavily 通用网页搜索客户端（Issue 01，第 7 轮）。

Tavily 取代 DuckDuckGo 成为产品唯一通用公网搜索提供方。客户端只发送查询
规划器产出的公开、最小查询词，不读取账户配置、附件或用户画像；端点固定、
凭据只以 ``Authorization: Bearer`` 形式随请求发送，响应受固定大小上限约束。

错误语义（与第 6 轮 DDG 合同对齐但语义分离）：
- timeout/connect/DNS/offline/5xx：可重试，语义与第 6 轮一致；
- 401/403：配置错误（``web_search_configuration``），不可重试，中文文案
  明确提示检查 Tavily API Key，不暗示稍后重试；
- 429：限流（``web_search_rate_limit``），不立即重试，由服务层进入冷却；
- 响应契约解析失败：独立成类（``web_search_contract``），不可重试；
- 缺 Key：``web_search_credentials``，提示「未配置搜索凭据」。

任何错误投影不得包含 Key、完整请求或响应正文。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from bridges import public_search_budget as search_budget
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

TAVILY_SEARCH_PROVIDER = "tavily"
TAVILY_SEARCH_PROVIDER_VERSION = "tavily-search-api-v1"
TAVILY_REQUEST_PROFILE_VERSION = "tavily-api-v1"
TAVILY_SEARCH_ENDPOINT = "https://api.tavily.com/search"
TAVILY_EXTRACT_ENDPOINT = "https://api.tavily.com/extract"
TAVILY_MAX_RESULTS = 5
TAVILY_MAX_RESPONSE_BYTES = 1_000_000
TAVILY_EXTRACT_MAX_RESPONSE_BYTES = 2_000_000
TAVILY_EXTRACT_DEPTH = "basic"
_SOURCE_SUMMARY_MAX_CHARS = 4_000
_TAVILY_REQUEST_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Encoding": "gzip",
}


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


class TavilySearchClient:
    """Tavily Search/Extract API 的固定端点、Bearer 认证适配器。

    凭据只允许经安装流程（``BridGes start``）或 ``BRIDGES_TAVILY_API_KEY``
    环境/文件路径进入；客户端不读取、不继承、不发送任何 Qwen 凭据。
    缺 Key 时构造不失败，搜索入口返回「未配置搜索凭据」投影。
    """

    provider_name = TAVILY_SEARCH_PROVIDER
    provider_version = TAVILY_SEARCH_PROVIDER_VERSION
    request_profile_version = TAVILY_REQUEST_PROFILE_VERSION

    def __init__(
        self,
        *,
        api_key: SecretStr | None = None,
        http_client: httpx.Client | None = None,
        timeout: float = search_budget.PUBLIC_SEARCH_STAGE_SECONDS,
        max_results: int = TAVILY_MAX_RESULTS,
        max_response_bytes: int = TAVILY_MAX_RESPONSE_BYTES,
        fetch_sources: bool = True,
    ) -> None:
        self._api_key = api_key
        self._client = http_client or httpx.Client(timeout=timeout)
        self._timeout = timeout
        self._max_results = max(1, min(max_results, TAVILY_MAX_RESULTS))
        self._max_response_bytes = max_response_bytes
        self._should_fetch_sources = fetch_sources

    def search(
        self,
        query: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
        stop_event: Event | None = None,
        fetch_sources: bool | None = None,
    ) -> list[WebSearchResult]:
        if not query.strip():
            raise WebSearchError(
                "web_search_request", "公网搜索查询不能为空。", retryable=False
            )
        self._require_key()
        if _user_cancelled(stop_event):
            raise WebSearchError("web_search_cancelled", "已取消本轮联网搜索。")
        if timeout is not None:
            relative_deadline = monotonic() + timeout
            deadline = (
                relative_deadline
                if deadline is None
                else min(deadline, relative_deadline)
            )
        if deadline is not None and deadline <= monotonic():
            raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")

        results = self._search_endpoint(
            query, deadline=deadline, stop_event=stop_event
        )
        should_fetch = (
            self._should_fetch_sources if fetch_sources is None else fetch_sources
        )
        if not should_fetch or not results:
            return results
        return self._fill_source_contents(results, deadline=deadline, stop_event=stop_event)

    def health_check(self) -> WebSearchHealth:
        """使用固定探针查询复用业务解析合同，不接受用户查询。"""
        checked_at = datetime.now(UTC)
        try:
            self.search(
                "bridges-provider-health-check",
                fetch_sources=False,
            )
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

    def _require_key(self) -> None:
        if (
            self._api_key is None
            or not self._api_key.get_secret_value().strip()
        ):
            raise WebSearchError(
                "web_search_credentials",
                "未配置搜索凭据：请先运行 BridGes start 配置 Tavily API Key，"
                "或设置 BRIDGES_TAVILY_API_KEY。",
                permission=True,
                retryable=False,
            )

    def _search_endpoint(
        self,
        query: str,
        *,
        deadline: float | None,
        stop_event: Event | None,
    ) -> TavilySearchResults:
        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": self._max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        try:
            with self._client.stream(
                "POST",
                TAVILY_SEARCH_ENDPOINT,
                json=payload,
                headers=self._authorization_headers(),
                follow_redirects=False,
                timeout=(
                    _remaining_timeout(deadline)
                    if deadline is not None
                    else self._timeout
                ),
            ) as response:
                self._validate_response(response, self._max_response_bytes)
                body = _read_bounded(response, self._max_response_bytes)
                status_category = _http_status_category(response.status_code)
        except httpx.TimeoutException as exc:
            raise WebSearchError(
                "web_search_timeout", "联网搜索超时，请重试。"
            ) from exc
        except httpx.ConnectError as exc:
            code = _connection_error_code(exc)
            message = (
                "无法解析公网搜索地址，请检查 DNS 或网络后重试。"
                if code == "web_search_dns"
                else "当前无法连接公网搜索，请检查网络后重试。"
            )
            raise WebSearchError(code, message) from exc
        except httpx.HTTPError as exc:
            raise WebSearchError(
                "web_search_offline", "当前无法连接公网搜索，请检查网络后重试。"
            ) from exc

        if _user_cancelled(stop_event):
            raise WebSearchError("web_search_cancelled", "已取消本轮联网搜索。")
        if deadline is not None and deadline <= monotonic():
            raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")
        return _parse_tavily_results(body, status_category, self._max_results)

    def _fill_source_contents(
        self,
        results: TavilySearchResults,
        *,
        deadline: float | None,
        stop_event: Event | None,
    ) -> list[WebSearchResult]:
        """用 Tavily Extract 填充正文与抓取时间；任何提取失败不拖垮整轮。"""
        urls = [result.url for result in results]
        if not urls:
            return results
        if _user_cancelled(stop_event):
            return results
        payload: dict[str, Any] = {
            "urls": urls,
            "extract_depth": TAVILY_EXTRACT_DEPTH,
        }
        try:
            with self._client.stream(
                "POST",
                TAVILY_EXTRACT_ENDPOINT,
                json=payload,
                headers=self._authorization_headers(),
                follow_redirects=False,
                timeout=(
                    _remaining_timeout(deadline)
                    if deadline is not None
                    else self._timeout
                ),
            ) as response:
                self._validate_response(
                    response, TAVILY_EXTRACT_MAX_RESPONSE_BYTES
                )
                body = _read_bounded(response, TAVILY_EXTRACT_MAX_RESPONSE_BYTES)
        except WebSearchError as exc:
            # 凭据/配置类错误必须透传（错误分类语义一致，不得吞掉）；其余
            # 提取失败属于增强步骤，保留搜索响应的结构化内容摘要即可。
            if exc.code in {"web_search_configuration", "web_search_credentials"}:
                raise
            return results
        except httpx.HTTPError:
            # 提取属于增强步骤：失败时保留搜索响应的结构化内容摘要。
            return results

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return results
        extracted_by_url = {
            item.get("url"): item.get("raw_content")
            for item in payload.get("results", [])
            if isinstance(item, dict)
            and isinstance(item.get("url"), str)
            and isinstance(item.get("raw_content"), str)
            and item["raw_content"].strip()
        }
        fetched_at = datetime.now(UTC)
        filled: list[WebSearchResult] = []
        for result in results:
            raw_content = extracted_by_url.get(result.url)
            if raw_content is None:
                filled.append(result)
                continue
            filled.append(
                result.model_copy(
                    update={
                        "verification": WebSearchVerification.VERIFIED,
                        "fetched_at": fetched_at,
                        "content_summary": _bounded_text(
                            raw_content, _SOURCE_SUMMARY_MAX_CHARS
                        ),
                    }
                )
            )
        return TavilySearchResults(
            filled,
            page_classification=results.page_classification,
            http_status_category=results.http_status_category,
        )

    def _authorization_headers(self) -> dict[str, str]:
        key = self._api_key.get_secret_value() if self._api_key is not None else ""
        return {
            **_TAVILY_REQUEST_HEADERS,
            "Authorization": f"Bearer {key}",
        }

    def _validate_response(
        self, response: httpx.Response, max_bytes: int
    ) -> None:
        """统一映射提供方状态，并在消费正文前执行响应上限。"""
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > max_bytes:
                    raise WebSearchError(
                        "web_search_response_too_large",
                        "公网搜索响应过大，已拒绝处理。",
                        retryable=False,
                        http_status_category=_http_status_category(response.status_code),
                    )
            except ValueError:
                pass
        category = _http_status_category(response.status_code)
        if 300 <= response.status_code < 400:
            raise WebSearchError(
                "web_search_redirect",
                "公网搜索发生了不受控重定向，已拒绝处理。",
                retryable=False,
                http_status_category=category,
            )
        if response.status_code == 429:
            raise WebSearchError(
                "web_search_rate_limit",
                "搜索请求过于频繁（Tavily 限流），请稍后显式重试。",
                http_status_category=category,
            )
        if response.status_code in {401, 403}:
            raise WebSearchError(
                "web_search_configuration",
                "搜索凭据无效（Tavily API Key 未通过校验），"
                "请检查 Tavily API Key 配置。",
                permission=True,
                retryable=False,
                http_status_category=category,
            )
        if response.status_code >= 500:
            raise WebSearchError(
                "web_search_provider",
                "公网搜索提供方暂时不可用，请稍后重试。",
                http_status_category=category,
            )
        if response.status_code >= 400:
            raise WebSearchError(
                "web_search_request",
                "公网搜索请求未完成，请重试。",
                http_status_category=category,
            )


def _parse_tavily_results(
    body: bytes,
    status_category: str,
    max_results: int,
) -> TavilySearchResults:
    """解析 Tavily 结构化响应；契约不符单独成类（``web_search_contract``）。"""
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
        content = _clean_text(item.get("content"))
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


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _bounded_text(value: str, max_chars: int) -> str:
    return value[:max_chars]


def _health_status(code: str) -> WebSearchHealthStatus:
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
    return WebSearchHealthStatus.UPSTREAM_ERROR
