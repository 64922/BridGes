"""Issue 01：Tavily 服务层语义与生产组合根测试。

覆盖：plan/重试/冷却在 Tavily 错误类下语义正确、投影 ``provider="tavily"``、
``attempts`` 与实际 HTTP 调用数一致、缺 Key 投影、健康聚合漂移失败关闭、
生产装配点只注册 Tavily。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

# 仓库既有循环依赖（contracts/chat.py:36 经 routing→ai→adapters 回环）：
# 先完整加载 ai 包，避免本文件单独收集时 contracts.chat 部分初始化。
import bridges.ai.adapters  # noqa: F401 - 仅用于打破既有循环导入顺序
from bridges.config import Settings
from bridges.contracts.chat import ChatMode
from bridges.observability.service import ObservabilityService
from bridges.web_search.contracts import (
    WebSearchHealth,
    WebSearchHealthStatus,
    WebSearchProjection,
    WebSearchResult,
    WebSearchStatus,
    aggregate_public_search_health,
)
from bridges.web_search.providers import (
    BRAVE_SEARCH_PROVIDER,
    BRAVE_SEARCH_PROVIDER_VERSION,
    build_web_search_provider,
)
from bridges.web_search.service import (
    LocalQueryPlanner,
    SearchPlan,
    WebSearchProviderDriftError,
    WebSearchService,
)
from bridges.web_search.tavily import (
    TAVILY_SEARCH_PROVIDER,
    TAVILY_SEARCH_PROVIDER_VERSION,
    TavilySearchClient,
)

API_KEY = "tvly-service-key"


def _search_response() -> dict[str, object]:
    return {
        "query": "公开主题",
        "results": [
            {
                "title": "公开来源",
                "url": "https://example.com/source",
                "content": "公开来源内容摘要。",
                "score": 0.9,
            }
        ],
        "response_time": 0.3,
    }


def _tavily_service(handler) -> WebSearchService:
    client = TavilySearchClient(
        api_key=SecretStr(API_KEY),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        fetch_sources=False,
    )
    return WebSearchService(client=client)


def _plan(**kwargs) -> SearchPlan:
    defaults = {
        "should_search": True,
        "query": "公开主题",
        "reason": "需要事实核查",
        "max_retries": 1,
    }
    defaults.update(kwargs)
    return SearchPlan(**defaults)


def test_plan_and_projection_carry_tavily_provider_identity() -> None:
    plan = SearchPlan(True, "公开主题", "需要事实核查")

    assert plan.provider == "tavily"
    assert plan.provider_version == TAVILY_SEARCH_PROVIDER_VERSION

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_search_response())

    projection = _tavily_service(handler).search("acct-1", plan)

    assert projection is not None
    assert projection.status == WebSearchStatus.SUCCESS
    assert projection.provider == TAVILY_SEARCH_PROVIDER
    assert projection.provider_version == TAVILY_SEARCH_PROVIDER_VERSION
    assert projection.selected_provider == TAVILY_SEARCH_PROVIDER
    assert projection.results[0].provider == TAVILY_SEARCH_PROVIDER
    assert projection.results[0].provider_version == TAVILY_SEARCH_PROVIDER_VERSION


def test_planner_honors_mode_gating_and_emits_tavily_plan() -> None:
    planner = LocalQueryPlanner()
    study = planner.plan("量子计算最新进展", ChatMode.STUDY)
    companion = planner.plan("量子计算最新进展", ChatMode.COMPANION)

    assert study.should_search is False
    assert companion.should_search is True
    assert companion.provider == "tavily"


def test_attempts_match_actual_http_calls_for_success() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_search_response())

    projection = _tavily_service(handler).search("acct-1", _plan())

    assert projection is not None
    assert calls == 1
    assert projection.attempt_count == 1
    assert projection.query_count == 1
    assert [a.attempt_number for a in projection.provider_attempts] == [1]
    assert projection.provider_attempts[0].provider == "tavily"
    assert projection.provider_attempts[0].result_code == "success"
    assert projection.searched_at is not None


def test_configuration_error_is_terminal_and_never_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "invalid key"})

    projection = _tavily_service(handler).search("acct-1", _plan())

    assert projection is not None
    assert calls == 1
    assert projection.status == WebSearchStatus.PERMISSION
    assert projection.error_code == "web_search_configuration"
    assert projection.can_retry is False
    assert "Tavily API Key" in (projection.error_message or "")
    assert "稍后重试" not in (projection.error_message or "")
    assert projection.attempt_count == 1
    assert projection.provider_attempts[0].result_code == "web_search_configuration"


def test_rate_limit_enters_cooldown_without_immediate_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": "rate limited"})

    service = _tavily_service(handler)
    first = service.search("acct-1", _plan())
    second = service.search("acct-1", _plan())

    assert first is not None
    assert first.error_code == "web_search_rate_limit"
    assert first.can_retry is True  # 允许用户显式重试，但系统不自动重试
    assert first.cooldown_until is not None
    assert first.attempt_count == 1
    assert second is not None
    assert second.error_code == "web_search_provider_challenge"
    assert second.cooldown_until == first.cooldown_until
    assert second.attempt_count == 0
    assert calls == 1, "冷却期内不得再次发出真实请求"


def test_transient_provider_error_is_retried_once_then_fails() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": "upstream"})
        return httpx.Response(500, json={"error": "upstream again"})

    projection = _tavily_service(handler).search("acct-1", _plan())

    assert projection is not None
    assert calls == 2
    assert projection.status == WebSearchStatus.ERROR
    assert projection.error_code == "web_search_provider"
    assert projection.attempt_count == 2
    assert [a.attempt_number for a in projection.provider_attempts] == [1, 2]
    assert projection.provider_attempts[0].retry_planned is True
    assert projection.can_retry is True


def test_timeout_is_retried_once_within_budget() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.TimeoutException("timed out", request=request)
        return httpx.Response(200, json=_search_response())

    projection = _tavily_service(handler).search("acct-1", _plan())

    assert projection is not None
    assert projection.status == WebSearchStatus.SUCCESS
    assert calls == 2
    assert projection.attempt_count == 2
    assert [a.attempt_number for a in projection.provider_attempts] == [1, 2]


def test_missing_key_projects_credentials_error_without_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("缺 Key 时不得发出任何请求")

    service = WebSearchService(
        client=TavilySearchClient(
            api_key=None,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
    )

    projection = service.search("acct-1", _plan())

    assert projection is not None
    assert projection.status == WebSearchStatus.PERMISSION
    assert projection.error_code == "web_search_credentials"
    assert projection.can_retry is False
    assert "未配置搜索凭据" in (projection.error_message or "")
    assert projection.provider == "tavily"


def test_service_default_client_is_tavily_and_never_duckduckgo() -> None:
    """生产注册表移除 DDG 后，缺省客户端必须是 Tavily（缺 Key 可启动）。"""
    service = WebSearchService()

    assert service._client.provider_name == "tavily"  # noqa: SLF001 - 组合根断言
    projection = service.search(
        "acct-1", SearchPlan(True, "公开主题", "需要事实核查")
    )
    assert projection is not None
    assert projection.error_code == "web_search_credentials"


def test_health_summary_registers_only_tavily_and_fails_on_drift() -> None:
    checked_at = datetime.now(UTC)
    healthy = _tavily_service(
        lambda request: httpx.Response(200, json=_search_response())
    )

    summary = healthy.health_check()

    assert summary.available is True
    assert summary.status == WebSearchHealthStatus.READY
    assert [provider.provider for provider in summary.providers] == ["tavily"]

    drift = aggregate_public_search_health(
        [
            WebSearchHealth(
                provider="duckduckgo",
                provider_version="duckduckgo-html-v1",
                status=WebSearchHealthStatus.READY,
                checked_at=checked_at,
            )
        ]
    )
    assert drift.available is False
    assert drift.error_code == "unexpected_search_provider"


def test_fallback_client_registration_is_still_config_drift() -> None:
    client = _tavily_service(
        lambda request: httpx.Response(200, json=_search_response())
    )
    with pytest.raises(WebSearchProviderDriftError, match="Tavily"):
        WebSearchService(
            client=client,
            fallback_client=client,
            fallback_provider=BRAVE_SEARCH_PROVIDER,
            fallback_provider_version=BRAVE_SEARCH_PROVIDER_VERSION,
        )


def test_build_web_search_provider_only_registers_tavily() -> None:
    settings = Settings(tavily_api_key=SecretStr(API_KEY))
    provider = build_web_search_provider(settings)

    assert provider.provider_name == TAVILY_SEARCH_PROVIDER
    assert provider.provider_version == TAVILY_SEARCH_PROVIDER_VERSION
    assert isinstance(provider, TavilySearchClient)

    without_key = build_web_search_provider(Settings())
    assert without_key.provider_name == "tavily"


def test_error_projections_never_leak_key_material_across_error_matrix() -> None:
    """Issue 01 不变量：整张错误矩阵的任何投影都不得含 Key（tvly- 前缀串）。"""
    matrix: list[tuple[int, str]] = [
        (401, '{"error":"invalid"}'),
        (403, '{"error":"forbidden"}'),
        (429, '{"error":"rate limited"}'),
        (500, '{"error":"upstream"}'),
        (503, '{"error":"upstream"}'),
        (200, "not-json"),
        (200, '{"results":"wrong"}'),
    ]

    for status_code, body in matrix:
        def handler(
            request: httpx.Request,
            _code: int = status_code,
            _body: str = body,
        ) -> httpx.Response:
            del request
            return httpx.Response(_code, content=_body.encode("utf-8"))

        projection = _tavily_service(handler).search("acct-1", _plan())
        assert projection is not None
        serialized = projection.model_dump_json()
        assert "tvly-" not in serialized, projection.error_code
        assert API_KEY not in serialized


def test_audit_uses_tavily_profile_and_no_key_material() -> None:
    observability = ObservabilityService()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_search_response())

    service = _tavily_service(handler)
    service._observability = observability  # noqa: SLF001 - 测试注入
    service.search("acct-1", _plan())

    events = observability.list_audit_events()
    assert len(events) == 1
    details = events[0].details or {}
    assert details["provider"] == "tavily"
    assert details["provider_version"] == TAVILY_SEARCH_PROVIDER_VERSION
    assert details["request_profile_version"] == "tavily-api-v1"
    assert API_KEY not in json.dumps(details, ensure_ascii=False)
    assert "tvly-" not in json.dumps(details, ensure_ascii=False)


def test_empty_results_project_as_empty_with_tavily_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"query": "公开主题", "results": []})

    projection = _tavily_service(handler).search("acct-1", _plan())

    assert projection is not None
    assert projection.status == WebSearchStatus.EMPTY
    assert projection.error_code == "web_search_no_results"
    assert projection.provider == "tavily"


def test_projection_round_trip_keeps_tavily_provider() -> None:
    result = WebSearchResult(
        result_id="web-1",
        title="公开来源",
        site="example.com",
        url="https://example.com/source",
        snippet="摘要",
        accessed_at=datetime.now(UTC),
    )
    projection = WebSearchProjection(
        status=WebSearchStatus.SUCCESS,
        trigger_reason="需要事实核查",
        query_summary="公开主题",
        results=[result],
    )
    assert projection.provider == "tavily"
    assert projection.provider_version == TAVILY_SEARCH_PROVIDER_VERSION
    assert projection.results[0].provider == "tavily"
