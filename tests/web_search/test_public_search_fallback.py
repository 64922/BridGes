"""Issue 02/03：公开搜索主用合同的确定性测试；Issue 04 起备用提供方为配置漂移。

Issue 04 之前，备用提供方（Brave 等）允许被“注册但忽略”；现在注册任何
备用客户端都是配置漂移，组合期失败关闭，稳定错误码
``unexpected_search_provider``。Issue 01 起主用提供方为 Tavily；Brave
客户端本身的合同测试保留，用于证明历史能力已冻结且不会复活到生产组合。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic, sleep

import httpx
import pytest
from pydantic import SecretStr

from bridges.api.main import create_app
from bridges.closeout.fixtures import CloseoutWebSearchClient
from bridges.config import Settings
from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import (
    WebSearchHealth,
    WebSearchHealthStatus,
    WebSearchPageClassification,
    WebSearchResult,
    WebSearchStatus,
    aggregate_public_search_health,
)
from bridges.web_search.providers import (
    BRAVE_SEARCH_PROVIDER,
    BRAVE_SEARCH_PROVIDER_VERSION,
    BraveSearchClient,
    FallbackProviderConfigurationError,
    StructuredSearchResults,
    build_fallback_provider,
)
from bridges.web_search.service import (
    InMemoryWebSearchCache,
    SearchPlan,
    WebSearchProviderDriftError,
    WebSearchService,
)
from bridges.web_search.tavily import (
    TAVILY_SEARCH_PROVIDER,
    TAVILY_SEARCH_PROVIDER_VERSION,
    TavilySearchClient,
)


def _result(
    result_id: str = "result-1",
    *,
    title: str = "公开来源",
    provider: str = TAVILY_SEARCH_PROVIDER,
    provider_version: str = TAVILY_SEARCH_PROVIDER_VERSION,
) -> WebSearchResult:
    return WebSearchResult(
        result_id=result_id,
        title=title,
        site="example.com",
        url=f"https://example.com/{result_id}",
        snippet="公开摘要",
        accessed_at=datetime.now(UTC),
        provider=provider,
        provider_version=provider_version,
    )


class _FakeClient:
    provider_name = TAVILY_SEARCH_PROVIDER
    provider_version = TAVILY_SEARCH_PROVIDER_VERSION

    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.queries: list[str] = []
        self.deadlines: list[float | None] = []

    def search(self, query: str, *, deadline: float | None = None, **_: object):
        self.queries.append(query)
        self.deadlines.append(deadline)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if callable(self.outcome):
            return self.outcome(query)
        return self.outcome


class _HealthClient(_FakeClient):
    def __init__(self, outcome: object, health: WebSearchHealth) -> None:
        super().__init__(outcome)
        self.health = health

    def health_check(self) -> WebSearchHealth:
        return self.health


def test_overall_health_rejects_configured_fallback_as_config_drift() -> None:
    checked_at = datetime.now(UTC)
    primary = _HealthClient(
        [],
        WebSearchHealth(
            provider=TAVILY_SEARCH_PROVIDER,
            provider_version=TAVILY_SEARCH_PROVIDER_VERSION,
            status=WebSearchHealthStatus.UPSTREAM_ERROR,
            checked_at=checked_at,
            error_code="web_search_provider_challenge",
        ),
    )
    fallback = _HealthClient(
        [],
        WebSearchHealth(
            provider=BRAVE_SEARCH_PROVIDER,
            provider_version=BRAVE_SEARCH_PROVIDER_VERSION,
            status=WebSearchHealthStatus.READY,
            checked_at=checked_at,
        ),
    )

    # Issue 04：传入备用客户端即配置漂移，组合期失败关闭（稳定错误码）。
    with pytest.raises(WebSearchProviderDriftError, match="unexpected_search_provider|Tavily"):
        WebSearchService(
            client=primary,
            fallback_client=fallback,
            fallback_provider=BRAVE_SEARCH_PROVIDER,
            fallback_provider_version=BRAVE_SEARCH_PROVIDER_VERSION,
        )

    # 聚合层纵深防御：即使健康证据里混入其他提供方，也必须以漂移失败。
    summary = aggregate_public_search_health(
        [
            primary.health,
            fallback.health,
        ]
    )
    assert summary.available is False
    assert summary.status == WebSearchHealthStatus.UPSTREAM_ERROR
    assert summary.error_code == "unexpected_search_provider"


def test_overall_health_is_unavailable_when_all_registered_providers_fail() -> None:
    checked_at = datetime.now(UTC)
    failed = _HealthClient(
        [],
        WebSearchHealth(
            provider=TAVILY_SEARCH_PROVIDER,
            provider_version=TAVILY_SEARCH_PROVIDER_VERSION,
            status=WebSearchHealthStatus.RATE_LIMITED,
            checked_at=checked_at,
            error_code="web_search_rate_limit",
        ),
    )

    summary = WebSearchService(client=failed).health_check()

    assert summary.available is False
    assert summary.status == WebSearchHealthStatus.RATE_LIMITED
    assert summary.providers[0].provider_version == TAVILY_SEARCH_PROVIDER_VERSION


def test_fallback_registry_requires_explicit_registration_and_credentials() -> None:
    assert build_fallback_provider(Settings()) is None

    with pytest.raises(FallbackProviderConfigurationError, match="未登记"):
        build_fallback_provider(
            Settings(
                public_search_fallback_enabled=True,
                public_search_fallback_provider="arbitrary-endpoint",
            )
        )

    with pytest.raises(FallbackProviderConfigurationError, match="凭据"):
        build_fallback_provider(
            Settings(
                public_search_fallback_enabled=True,
                public_search_fallback_provider=BRAVE_SEARCH_PROVIDER,
            )
        )

    with pytest.raises(FallbackProviderConfigurationError, match="端点"):
        build_fallback_provider(
            Settings(
                public_search_fallback_enabled=True,
                public_search_fallback_provider=BRAVE_SEARCH_PROVIDER,
                public_search_fallback_endpoint="https://evil.example/search",
                brave_search_api_key=SecretStr("brave-secret"),
            )
        )


def test_fallback_settings_use_global_env_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIDGES_PUBLIC_SEARCH_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("BRIDGES_PUBLIC_SEARCH_FALLBACK_PROVIDER", BRAVE_SEARCH_PROVIDER)
    monkeypatch.setenv("BRIDGES_BRAVE_SEARCH_API_KEY", "brave-secret")

    settings = Settings()

    assert settings.public_search_fallback_enabled is True
    assert settings.public_search_fallback_provider == BRAVE_SEARCH_PROVIDER
    assert settings.brave_search_api_key is not None
    assert settings.brave_search_api_key.get_secret_value() == "brave-secret"


def test_brave_search_client_parses_structured_results_without_exposing_key() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "结构化来源",
                            "url": "https://example.com/structured",
                            "description": "结构化摘要",
                        }
                    ]
                }
            },
        )

    secret = "brave-secret"
    client = BraveSearchClient(
        api_key=SecretStr(secret),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    results = client.search("公开主题")

    assert captured[0].url.host == "api.search.brave.com"
    assert captured[0].url.path == "/res/v1/web/search"
    assert captured[0].headers["x-subscription-token"] == secret
    assert results[0].title == "结构化来源"
    assert results[0].url == "https://example.com/structured"
    assert results[0].provider == BRAVE_SEARCH_PROVIDER
    assert results[0].provider_version == BRAVE_SEARCH_PROVIDER_VERSION
    assert results[0].verification == "structured"
    assert secret not in json.dumps(results[0].model_dump(mode="json"), ensure_ascii=False)


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "auth_error"),
        (429, "rate_limited"),
        (503, "upstream_error"),
    ],
)
def test_brave_health_check_reuses_provider_error_contract(
    status_code: int, expected: str
) -> None:
    client = BraveSearchClient(
        api_key=SecretStr("brave-secret"),
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(status_code))
        ),
    )

    health = client.health_check()

    assert health.provider == BRAVE_SEARCH_PROVIDER
    assert health.status == expected
    assert health.error_code is not None


def test_brave_health_check_rejects_missing_credentials() -> None:
    client = BraveSearchClient(
        api_key=SecretStr(""),
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
        ),
    )

    health = client.health_check()

    assert health.status == "auth_error"
    assert health.error_code == "web_search_fallback_credentials"


def test_primary_success_without_fallback_registration() -> None:
    primary = _FakeClient([_result()])
    service = WebSearchService(client=primary)

    projection = service.search("acct-1", SearchPlan(True, "公开主题", "需要事实核查"))

    assert projection is not None
    assert projection.status == WebSearchStatus.SUCCESS
    assert projection.provider == "tavily"
    assert projection.selected_provider == "tavily"
    assert projection.provider_attempts[0].provider == "tavily"
    assert [item.provider for item in projection.provider_attempts] == ["tavily"]


def test_primary_permission_failure_keeps_permission_status() -> None:
    primary = _FakeClient(
        WebSearchError(
            "web_search_permission",
            "主用搜索权限失败",
            permission=True,
        )
    )
    service = WebSearchService(client=primary)

    projection = service.search(
        "acct-1", SearchPlan(True, "公开主题", "需要事实核查")
    )

    assert projection is not None
    assert projection.status == WebSearchStatus.PERMISSION
    assert projection.error_code == "web_search_permission"
    assert [item.provider for item in projection.provider_attempts] == ["tavily"]


def test_slow_primary_keeps_timeout_projection() -> None:
    class _SlowClient:
        provider_name = TAVILY_SEARCH_PROVIDER
        provider_version = TAVILY_SEARCH_PROVIDER_VERSION

        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(
            self,
            query: str,
            *,
            stop_event: Event | None = None,
            **_: object,
        ) -> list[WebSearchResult]:
            self.queries.append(query)
            while stop_event is None or not stop_event.is_set():
                sleep(0.005)
            raise WebSearchError("web_search_timeout", "主用超时")

    primary = _SlowClient()
    service = WebSearchService(client=primary)

    projection = service.search(
        "acct-1",
        SearchPlan(
            True,
            "公开主题",
            "需要事实核查",
            total_timeout_seconds=0.90,
        ),
    )

    # Issue 03：总预算内只有 Tavily 一个提供方；保留真实超时投影。
    assert projection is not None
    assert projection.status == WebSearchStatus.ERROR
    assert projection.error_code == "web_search_timeout"
    assert projection.provider_attempts[0].result_code == "web_search_timeout"
    assert [item.provider for item in projection.provider_attempts] == ["tavily"]


def test_challenge_keeps_cooldown_without_switching_provider() -> None:
    primary = _FakeClient(
        WebSearchError(
            "web_search_provider_challenge",
            "主用受阻",
            cooldown_seconds=30,
        )
    )
    service = WebSearchService(client=primary)

    projection = service.search("acct-1", SearchPlan(True, "公开主题", "需要事实核查"))

    # Issue 03：挑战页不切换提供方；进入冷却并允许用户显式重试。
    assert projection is not None
    assert projection.status == WebSearchStatus.ERROR
    assert projection.error_code == "web_search_provider_challenge"
    assert projection.cooldown_until is not None
    assert primary.queries == ["公开主题"]
    assert [item.provider for item in projection.provider_attempts] == ["tavily"]
    assert projection.provider_attempts[0].result_code == "web_search_provider_challenge"


@pytest.mark.parametrize(
    "classification",
    [
        WebSearchPageClassification.CHALLENGE,
        WebSearchPageClassification.INVALID,
    ],
)
def test_classified_empty_primary_response_does_not_switch_provider(
    classification: WebSearchPageClassification,
) -> None:
    primary = _FakeClient(
        StructuredSearchResults([], page_classification=classification)
    )
    service = WebSearchService(client=primary)

    projection = service.search(
        "acct-1", SearchPlan(True, "公开主题", "需要事实核查")
    )

    # Issue 03：分类异常页不切换提供方；空结果按 EMPTY 终态返回。
    assert projection is not None
    assert projection.status == WebSearchStatus.EMPTY
    assert primary.queries == ["公开主题", "公开主题 基础定义 原理"]
    assert projection.provider_attempts[0].result_code == "web_search_no_results"


def test_cache_key_keeps_provider_separate_from_primary() -> None:
    cache = InMemoryWebSearchCache()
    primary_plan = SearchPlan(True, "公开主题", "需要事实核查")
    fallback_plan = SearchPlan(
        True,
        "公开主题",
        "需要事实核查",
        provider=BRAVE_SEARCH_PROVIDER,
        provider_version=BRAVE_SEARCH_PROVIDER_VERSION,
    )
    projection = _result(
        "brave-1",
        provider=BRAVE_SEARCH_PROVIDER,
        provider_version=BRAVE_SEARCH_PROVIDER_VERSION,
    )
    from bridges.web_search.contracts import WebSearchProjection

    fallback_projection = WebSearchProjection(
        status=WebSearchStatus.SUCCESS,
        trigger_reason=fallback_plan.reason,
        query_summary=fallback_plan.query,
        results=[projection],
        provider=BRAVE_SEARCH_PROVIDER,
        provider_version=BRAVE_SEARCH_PROVIDER_VERSION,
        selected_provider=BRAVE_SEARCH_PROVIDER,
        selected_provider_version=BRAVE_SEARCH_PROVIDER_VERSION,
    )
    cache.put(
        "acct-1",
        fallback_plan,
        fallback_projection,
        datetime.now(UTC) + timedelta(minutes=1),
    )

    assert cache.get("acct-1", primary_plan, datetime.now(UTC)) is None
    assert cache.get("acct-1", fallback_plan, datetime.now(UTC)) is not None


def test_empty_after_one_bounded_rewrite_keeps_empty_status() -> None:
    def primary_outcome(query: str) -> list[WebSearchResult]:
        return []

    primary = _FakeClient(primary_outcome)
    service = WebSearchService(client=primary)

    projection = service.search("acct-1", SearchPlan(True, "公开主题", "需要事实核查"))

    # Issue 03：空结果改写后仍空，不切换提供方。
    assert projection is not None
    assert projection.status == WebSearchStatus.EMPTY
    assert projection.page_classification != "challenge"
    assert primary.queries == ["公开主题", "公开主题 基础定义 原理"]
    assert projection.query_history == primary.queries
    assert {item.provider for item in projection.provider_attempts} == {"tavily"}


def test_configured_fallback_is_config_drift_even_on_failure() -> None:
    primary = _FakeClient(WebSearchError("web_search_timeout", "主用超时"))
    fallback = _FakeClient(WebSearchError("web_search_fallback_timeout", "备用超时"))

    # Issue 04：即使 Tavily 失败，任何备用客户端注册都是配置漂移，拒绝组合。
    with pytest.raises(WebSearchProviderDriftError):
        WebSearchService(
            client=primary,
            fallback_client=fallback,
            fallback_provider=BRAVE_SEARCH_PROVIDER,
            fallback_provider_version=BRAVE_SEARCH_PROVIDER_VERSION,
        )


def test_timeout_when_stage_budget_consumed_before_request() -> None:
    primary = _FakeClient(WebSearchError("web_search_timeout", "主用超时"))
    service = WebSearchService(client=primary)

    projection = service.search(
        "acct-1",
        SearchPlan(True, "公开主题", "需要事实核查", total_timeout_seconds=0.01),
        deadline=monotonic() + 0.01,
    )

    assert projection is not None
    assert projection.error_code in {"web_search_timeout", "web_search_no_results"}


def test_production_composition_only_registers_tavily() -> None:
    """Issue 01/04：生产组合的健康提供方列表只有 Tavily，并挂载健康监视器。

    closeout fixture 模式（test 环境显式开启）使用协议 fixture 客户端，
    但提供方标识仍必须是 ``tavily``，不得回退到任何 DDG 形态。
    """

    app = create_app()
    web_search_service = app.state.web_search_service

    assert web_search_service is not None
    assert web_search_service._client is not None
    assert isinstance(
        web_search_service._client, (TavilySearchClient, CloseoutWebSearchClient)
    )
    assert web_search_service._client.provider_name == TAVILY_SEARCH_PROVIDER
    # test 环境不启用自动刷新，readiness 首次读取返回 pending 快照，绝不
    # 在单测中访问公网。
    monitor = web_search_service.health_monitor
    assert monitor is not None
    assert monitor.snapshot().pending is True
    assert monitor.peek().pending is True
    # 生产组合不登记备用提供方；任何 fallback client/key 均未注册。
    assert app.state.settings is None or not app.state.settings.public_search_fallback_enabled
