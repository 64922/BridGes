"""Issue 04：DDG 健康探针的稳定错误映射（httpx.MockTransport，离线确定）。

覆盖：200 可解析、200 契约漂移、DNS、connect、offline、timeout、403、
429、5xx、重定向与超大响应；断言稳定健康状态与脱敏错误码，且探针查询
固定、不含用户数据。
"""

from __future__ import annotations

import httpx
import pytest

from bridges.web_search.client import DuckDuckGoClient
from bridges.web_search.contracts import WebSearchHealthStatus

_RESULT_HTML = (
    "<html><body>"
    '<a class="result__a" href="https://example.com/transformer">'
    "Transformer 公开资料</a>"
    '<a class="result__snippet" href="https://example.com/transformer">'
    "注意力机制概述</a>"
    "</body></html>"
)


def _client(
    handler: httpx.MockTransport | None = None,
    *,
    max_response_bytes: int = 1_000_000,
) -> DuckDuckGoClient:
    transport = handler or httpx.MockTransport(lambda _: httpx.Response(200))
    return DuckDuckGoClient(
        http_client=httpx.Client(transport=transport),
        max_response_bytes=max_response_bytes,
        fetch_sources=False,
    )


def test_health_probe_uses_fixed_query_without_user_data() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, text=_RESULT_HTML)

    health = _client(httpx.MockTransport(handler)).health_check()

    assert health.status == WebSearchHealthStatus.READY
    assert health.provider == "duckduckgo"
    assert health.error_code is None
    assert captured[0].url.params["q"] == "bridges-provider-health-check"
    assert "bridges-provider-health-check" not in health.model_dump_json()


def test_health_ready_requires_parseable_results_not_just_http_200() -> None:
    health = _client(
        httpx.MockTransport(lambda _: httpx.Response(200, text="<html></html>"))
    ).health_check()

    assert health.status == WebSearchHealthStatus.UPSTREAM_ERROR
    assert health.error_code == "web_search_parse"


def test_health_detects_challenge_page_as_upstream_blocked() -> None:
    challenge = (
        "<html><body><h1>Unusual traffic detected</h1>"
        "<p>Please complete human verification</p></body></html>"
    )

    health = _client(
        httpx.MockTransport(lambda _: httpx.Response(200, text=challenge))
    ).health_check()

    assert health.status == WebSearchHealthStatus.UPSTREAM_ERROR
    assert health.error_code == "web_search_provider_challenge"


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (
            httpx.ConnectError("getaddrinfo failed for html.duckduckgo.com"),
            WebSearchHealthStatus.DNS_ERROR,
            "web_search_dns",
        ),
        (
            httpx.ConnectError("[WinError 10061] connection refused"),
            WebSearchHealthStatus.CONNECT_ERROR,
            "web_search_connect",
        ),
        (
            httpx.ConnectError("network is offline"),
            WebSearchHealthStatus.CONNECT_ERROR,
            "web_search_offline",
        ),
        (
            httpx.ConnectTimeout("timed out"),
            WebSearchHealthStatus.UPSTREAM_ERROR,
            "web_search_timeout",
        ),
    ],
)
def test_health_maps_transport_errors_to_stable_status(
    error: httpx.ConnectError, expected_status: WebSearchHealthStatus, expected_code: str
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise error

    health = _client(httpx.MockTransport(handler)).health_check()

    assert health.status == expected_status
    assert health.error_code == expected_code


@pytest.mark.parametrize(
    ("status_code", "expected_status", "expected_code"),
    [
        (403, WebSearchHealthStatus.AUTH_ERROR, "web_search_permission"),
        (429, WebSearchHealthStatus.RATE_LIMITED, "web_search_rate_limit"),
        (503, WebSearchHealthStatus.UPSTREAM_ERROR, "web_search_provider"),
        (302, WebSearchHealthStatus.UPSTREAM_ERROR, "web_search_redirect"),
        (400, WebSearchHealthStatus.UPSTREAM_ERROR, "web_search_request"),
    ],
)
def test_health_maps_http_status_categories(
    status_code: int, expected_status: WebSearchHealthStatus, expected_code: str
) -> None:
    headers = {"location": "https://evil.example/redirect"} if status_code == 302 else {}
    health = _client(
        httpx.MockTransport(
            lambda _: httpx.Response(status_code, headers=headers, text="<html></html>")
        )
    ).health_check()

    assert health.status == expected_status
    assert health.error_code == expected_code


def test_health_rejects_response_too_large() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 4096, headers={"content-length": "4096"})

    health = _client(httpx.MockTransport(handler), max_response_bytes=1024).health_check()

    assert health.status == WebSearchHealthStatus.UPSTREAM_ERROR
    assert health.error_code == "web_search_response_too_large"


def test_health_never_echoes_response_body() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "<html><body>PRIVATE-PROBE-SECRET-MARKER "
                '<a class="result__a" href="https://example.com/x">T</a></body></html>'
            ),
        )

    health = _client(httpx.MockTransport(handler)).health_check()
    serialized = health.model_dump_json()

    assert health.status == WebSearchHealthStatus.READY
    assert "PRIVATE-PROBE-SECRET-MARKER" not in serialized
    assert "result__a" not in serialized
