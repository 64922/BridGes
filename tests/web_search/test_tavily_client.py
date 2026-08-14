"""Issue 01：Tavily 客户端合同（httpx.MockTransport，离线确定）。

覆盖：结果映射、Extract 正文填充、错误矩阵（401/403/429/5xx/timeout/
connect/解析失败）、请求不携带任何 Qwen 凭据、缺 Key 行为与健康检查。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import (
    WebSearchHealthStatus,
    WebSearchPageClassification,
    WebSearchVerification,
)
from bridges.web_search.tavily import (
    TAVILY_EXTRACT_ENDPOINT,
    TAVILY_SEARCH_ENDPOINT,
    TAVILY_SEARCH_PROVIDER,
    TAVILY_SEARCH_PROVIDER_VERSION,
    TavilySearchClient,
)

API_KEY = "tvly-test-key-123"


def _search_response() -> dict[str, object]:
    return {
        "query": "量子 计算",
        "answer": "",
        "results": [
            {
                "title": "量子计算公开资料",
                "url": "https://example.com/quantum",
                "content": "量子计算使用量子比特进行计算。",
                "score": 0.95,
                "raw_content": "",
            },
            {
                "title": "第二来源",
                "url": "https://example.org/second",
                "content": "第二来源摘要内容。",
                "score": 0.8,
                "raw_content": "",
            },
        ],
        "response_time": 0.4,
    }


def _client(handler, **kwargs):
    return TavilySearchClient(
        api_key=SecretStr(API_KEY),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def test_tavily_client_sends_bearer_key_and_minimal_public_query() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_search_response())

    results = _client(handler).search("量子 计算")

    assert len(captured) == 2  # search + extract
    search_request = captured[0]
    assert search_request.method == "POST"
    assert str(search_request.url) == TAVILY_SEARCH_ENDPOINT
    assert search_request.headers["authorization"] == f"Bearer {API_KEY}"
    assert search_request.headers["accept"] == "application/json"
    body = json.loads(search_request.content.decode("utf-8"))
    assert body["query"] == "量子 计算"
    assert body["max_results"] == 5
    assert body["search_depth"] == "basic"
    assert "api_key" not in body
    # 请求不携带任何 Qwen 凭据或账户信息。
    serialized = json.dumps(body, ensure_ascii=False).lower()
    assert "qwen" not in serialized
    assert "bearer" not in body

    assert results[0].url == "https://example.com/quantum"
    assert results[0].title == "量子计算公开资料"
    assert results[0].site == "example.com"
    assert results[0].provider == TAVILY_SEARCH_PROVIDER
    assert results[0].provider_version == TAVILY_SEARCH_PROVIDER_VERSION
    assert results[0].verification == "structured"
    assert results[0].content_summary == "量子计算使用量子比特进行计算。"
    assert results[0].result_id == "web-1"


def test_tavily_extract_fills_content_summary_and_fetched_at() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TAVILY_SEARCH_ENDPOINT:
            return httpx.Response(200, json=_search_response())
        assert str(request.url) == TAVILY_EXTRACT_ENDPOINT
        extract_body = json.loads(request.content.decode("utf-8"))
        assert extract_body["urls"] == [
            "https://example.com/quantum",
            "https://example.org/second",
        ]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/quantum",
                        "raw_content": "量子计算真实正文内容，足够长且可核验。",
                    }
                ],
                "failed_results": [
                    {"url": "https://example.org/second", "error": "fetch failed"}
                ],
            },
        )

    results = _client(handler).search("量子 计算")

    assert results[0].verification == WebSearchVerification.VERIFIED
    assert results[0].fetched_at is not None
    assert results[0].content_summary == "量子计算真实正文内容，足够长且可核验。"
    # 提取失败的结果保留搜索响应的结构化内容，不拖垮整轮。
    assert results[1].verification == WebSearchVerification.STRUCTURED
    assert results[1].fetched_at is None
    assert results[1].content_summary == "第二来源摘要内容。"


def test_tavily_extract_failure_keeps_structured_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TAVILY_SEARCH_ENDPOINT:
            return httpx.Response(200, json=_search_response())
        return httpx.Response(500, content=b"upstream error")

    results = _client(handler).search("量子 计算")

    assert len(results) == 2
    assert all(result.verification == "structured" for result in results)
    assert all(result.fetched_at is None for result in results)


@pytest.mark.parametrize("status_code", [401, 403])
def test_tavily_extract_configuration_errors_are_not_swallowed(
    status_code: int,
) -> None:
    """提取端点的 401/403 仍是配置错误，必须透传而不是伪装成结构化结果。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TAVILY_SEARCH_ENDPOINT:
            return httpx.Response(200, json=_search_response())
        return httpx.Response(status_code, json={"error": "invalid key"})

    with pytest.raises(WebSearchError) as exc_info:
        _client(handler).search("量子 计算")

    assert exc_info.value.code == "web_search_configuration"
    assert exc_info.value.retryable is False


def test_tavily_search_without_fetch_skips_extract() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=_search_response())

    results = _client(handler, fetch_sources=False).search("量子 计算")

    assert len(results) == 2
    assert seen_urls == [TAVILY_SEARCH_ENDPOINT]


def test_tavily_client_requires_configured_key_without_any_http_call() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_search_response())

    client = TavilySearchClient(
        api_key=None,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(WebSearchError) as exc_info:
        client.search("公开主题")
    assert exc_info.value.code == "web_search_credentials"
    assert exc_info.value.permission is True
    assert exc_info.value.retryable is False
    assert "未配置搜索凭据" in exc_info.value.message
    assert "Tavily" in exc_info.value.message
    assert requests == []


@pytest.mark.parametrize("status_code", [401, 403])
def test_tavily_configuration_errors_are_not_retryable_and_mention_the_key(
    status_code: int,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "invalid key"})

    with pytest.raises(WebSearchError) as exc_info:
        _client(handler).search("公开主题")

    assert exc_info.value.code == "web_search_configuration"
    assert exc_info.value.retryable is False
    assert exc_info.value.permission is True
    assert exc_info.value.http_status_category == "4xx"
    assert "Tavily API Key" in exc_info.value.message
    # 配置错误文案不得暗示稍后重试（Issue 01 AC：不暗示稍后重试）。
    assert "重试" not in exc_info.value.message


def test_tavily_rate_limit_is_classified_without_immediate_retry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    with pytest.raises(WebSearchError) as exc_info:
        _client(handler).search("公开主题")

    assert exc_info.value.code == "web_search_rate_limit"
    assert exc_info.value.retryable is True
    assert exc_info.value.http_status_category == "4xx"


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_tavily_5xx_is_retryable_provider_error(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "upstream"})

    with pytest.raises(WebSearchError) as exc_info:
        _client(handler).search("公开主题")

    assert exc_info.value.code == "web_search_provider"
    assert exc_info.value.retryable is True
    assert exc_info.value.http_status_category == "5xx"


def test_tavily_timeout_and_connect_error_classification() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    with pytest.raises(WebSearchError) as timeout_info:
        _client(timeout_handler).search("公开主题")
    assert timeout_info.value.code == "web_search_timeout"

    def dns_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("getaddrinfo failed for api.tavily.com", request=request)

    with pytest.raises(WebSearchError) as dns_info:
        _client(dns_handler).search("公开主题")
    assert dns_info.value.code == "web_search_dns"

    def offline_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(WebSearchError) as offline_info:
        _client(offline_handler).search("公开主题")
    assert offline_info.value.code == "web_search_offline"


def test_tavily_contract_parse_failures_are_separate_and_not_retryable() -> None:
    cases: list[tuple[bytes, str]] = [
        (b"not-json", "web_search_contract"),
        (b"{}", "web_search_contract"),
        (b'{"results": "wrong"}', "web_search_contract"),
    ]

    for body, expected_code in cases:
        def handler(request: httpx.Request, _body: bytes = body) -> httpx.Response:
            return httpx.Response(200, content=_body)

        with pytest.raises(WebSearchError) as exc_info:
            _client(handler).search("公开主题")
        assert exc_info.value.code == expected_code
        assert exc_info.value.retryable is False
        assert exc_info.value.page_classification == WebSearchPageClassification.INVALID


def test_tavily_empty_results_are_a_normal_empty_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"query": "x", "results": []})

    results = _client(handler).search("明确无结果")

    assert results == []
    assert results.page_classification == WebSearchPageClassification.NORMAL_EMPTY


def test_tavily_rejects_unsafe_or_duplicate_source_urls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "私有地址来源",
                        "url": "http://127.0.0.1/private",
                        "content": "不应被抓取",
                    },
                    {
                        "title": "重复来源",
                        "url": "https://example.com/quantum",
                        "content": "第一次出现",
                    },
                    {
                        "title": "重复来源副本",
                        "url": "https://example.com/quantum",
                        "content": "重复 URL",
                    },
                ]
            },
        )

    results = _client(handler).search("公开主题")

    assert len(results) == 1
    assert results[0].url == "https://example.com/quantum"


def test_tavily_all_unsafe_sources_fail_as_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "私有地址来源",
                        "url": "http://127.0.0.1/private",
                        "content": "不应被抓取",
                    }
                ]
            },
        )

    with pytest.raises(WebSearchError) as exc_info:
        _client(handler).search("公开主题")
    assert exc_info.value.code == "web_search_contract"


def test_tavily_oversized_response_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": "1000000"},
            content=b"x" * 128,
        )

    client = _client(handler, max_response_bytes=64)
    with pytest.raises(WebSearchError) as exc_info:
        client.search("公开主题")
    assert exc_info.value.code == "web_search_response_too_large"
    assert exc_info.value.retryable is False


def test_tavily_health_check_reuses_contract_and_maps_errors() -> None:
    probe_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        probe_queries.append(body["query"])
        return httpx.Response(200, json=_search_response())

    client = _client(handler, fetch_sources=False)
    healthy = client.health_check()

    assert healthy.status == WebSearchHealthStatus.READY
    assert healthy.provider == TAVILY_SEARCH_PROVIDER
    assert healthy.provider_version == TAVILY_SEARCH_PROVIDER_VERSION
    assert probe_queries == ["bridges-provider-health-check"]

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid key"})

    assert _client(unauthorized).health_check().status == WebSearchHealthStatus.AUTH_ERROR
    assert _client(unauthorized).health_check().error_code == "web_search_configuration"

    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    assert _client(limited).health_check().status == WebSearchHealthStatus.RATE_LIMITED

    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "upstream"})

    assert _client(unavailable).health_check().status == WebSearchHealthStatus.UPSTREAM_ERROR


def test_tavily_health_check_reports_missing_key_as_auth_error() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_search_response())

    client = TavilySearchClient(
        api_key=SecretStr(""),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    health = client.health_check()

    assert health.status == WebSearchHealthStatus.AUTH_ERROR
    assert health.error_code == "web_search_credentials"
    assert requests == []


def test_tavily_error_messages_never_include_key_or_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": "invalid api key: tvly-secret-value"},
        )

    with pytest.raises(WebSearchError) as exc_info:
        _client(handler).search("公开主题")

    assert API_KEY not in exc_info.value.message
    assert "tvly-secret-value" not in exc_info.value.message


def test_tavily_results_are_annotated_with_accessed_time() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_search_response())

    before = datetime.now(UTC)
    results = _client(handler, fetch_sources=False).search("量子 计算")
    after = datetime.now(UTC)

    assert before <= results[0].accessed_at <= after
