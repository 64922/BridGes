"""Tavily 搜索服务组合入口。"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock

from pydantic import SecretStr

from bridges.contracts.chat import ChatMode
from bridges.observability.service import ObservabilityService
from bridges.web_search.contracts import (
    WebSearchHealth,
    WebSearchHealthSnapshot,
    WebSearchHealthStatus,
    WebSearchHealthSummary,
    aggregate_public_search_health,
)
from bridges.web_search.health_monitor import WebSearchHealthMonitor
from bridges.web_search.service_outcomes import _WebSearchOutcomesMixin
from bridges.web_search.service_plan import (
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_PROVIDER,
    WEB_SEARCH_RULES_VERSION,
    InMemoryWebSearchCache,
    LocalQueryPlanner,
)
from bridges.web_search.service_plan import (
    SearchClient as SearchClient,
)
from bridges.web_search.service_plan import (
    SearchPlan as SearchPlan,
)
from bridges.web_search.service_plan import (
    WebSearchCache as WebSearchCache,
)
from bridges.web_search.service_queries import _WebSearchQueryMixin
from bridges.web_search.service_results import _WebSearchResultsMixin
from bridges.web_search.service_search import _WebSearchSearchMixin
from bridges.web_search.tavily import (
    TAVILY_REQUEST_PROFILE_VERSION,
    TavilySearchClient,
)
from bridges.web_search.tavily_support import (
    TAVILY_SEARCH_PROVIDER_VERSION as TAVILY_SEARCH_PROVIDER_VERSION,
)

__all__ = [
    "DEFAULT_CACHE_TTL_SECONDS",
    "DEFAULT_PROVIDER",
    "InMemoryWebSearchCache",
    "LocalQueryPlanner",
    "SearchClient",
    "SearchPlan",
    "TAVILY_SEARCH_PROVIDER_VERSION",
    "WEB_SEARCH_RULES_VERSION",
    "WebSearchCache",
    "WebSearchProviderDriftError",
    "WebSearchService",
]


class WebSearchProviderDriftError(ValueError):
    """备用公网提供方注册与固定 Tavily 契约冲突。"""

    code = "unexpected_search_provider"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class WebSearchService(
    _WebSearchSearchMixin,
    _WebSearchQueryMixin,
    _WebSearchResultsMixin,
    _WebSearchOutcomesMixin,
):
    """固定 Tavily 的公网搜索服务。"""
    def __init__(
        self,
        *,
        client: SearchClient | None = None,
        fallback_client: SearchClient | None = None,
        fallback_provider: str = "brave_search",
        fallback_provider_version: str = "brave-search-api-v1",
        fallback_reserve_seconds: float = 0.0,
        planner: LocalQueryPlanner | None = None,
        observability: ObservabilityService | None = None,
        cache: WebSearchCache | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
        health_ttl_seconds: int = 300,
        health_stale_ready_seconds: int = 1800,
        health_probe_timeout_seconds: float = 5.0,
        health_auto_refresh: bool = True,
    ) -> None:
        # Issue 04/01：出现 Tavily 之外的备用提供方即配置漂移，组合期
        # 失败关闭，绝不静默忽略或继续保留 fallback 结构。
        if fallback_client is not None:
            raise WebSearchProviderDriftError(
                "检测到备用公网搜索提供方注册（fallback_client）；"
                "当前产品唯一通用联网提供方是 Tavily，拒绝启动。"
            )
        self._client = client or TavilySearchClient()
        self._provider = getattr(self._client, "provider_name", None) or DEFAULT_PROVIDER
        self._provider_version = (
            getattr(self._client, "provider_version", None)
            or TAVILY_SEARCH_PROVIDER_VERSION
        )
        self._request_profile_version = (
            getattr(self._client, "request_profile_version", None)
            or TAVILY_REQUEST_PROFILE_VERSION
        )
        self._planner = planner or LocalQueryPlanner()
        self._observability = observability
        self._cache = cache or InMemoryWebSearchCache()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper or time.sleep
        self._provider_cooldown_until: datetime | None = None
        self._provider_state_lock = RLock()
        checker = getattr(self._client, "health_check", None)
        self._health_monitor: WebSearchHealthMonitor | None = (
            WebSearchHealthMonitor(
                checker,
                clock=self._clock,
                ttl_seconds=health_ttl_seconds,
                stale_ready_seconds=health_stale_ready_seconds,
                probe_timeout_seconds=health_probe_timeout_seconds,
                auto_refresh=health_auto_refresh,
                provider_version=self._provider_version,
            )
            if callable(checker)
            else None
        )

    def replace_tavily_api_key(self, api_key: SecretStr) -> None:
        """候选 Key 验证通过后切换当前 Tavily 适配器。"""
        if isinstance(self._client, TavilySearchClient):
            self._client.replace_api_key(api_key)

    def plan(
        self,
        content: str,
        mode: ChatMode,
        *,
        force: bool = False,
    ) -> SearchPlan:
        return self._planner.plan(content, mode, force=force)

    @property
    def health_monitor(self) -> WebSearchHealthMonitor | None:
        """运行期健康快照监视器；客户端不支持健康检查时为空。"""
        return self._health_monitor

    def health_snapshot(self) -> WebSearchHealthSnapshot | None:
        """只读当前公网搜索健康快照；客户端无健康检查时为空。"""
        monitor = self._health_monitor
        return None if monitor is None else monitor.peek()

    def refresh_health(self) -> WebSearchHealthSnapshot | None:
        """同步执行一次有界真实探测（发布探针/诊断命令使用）。"""
        monitor = self._health_monitor
        return None if monitor is None else monitor.refresh_now()

    def health_check(self) -> WebSearchHealthSummary:
        """检查已登记来源；Issue 01 起只登记 Tavily 且漂移失败关闭。"""

        clients = [(self._client, self._provider, self._provider_version)]
        health: list[WebSearchHealth] = []
        for client, provider, provider_version in clients:
            checker = getattr(client, "health_check", None)
            if not callable(checker):
                health.append(
                    WebSearchHealth(
                        provider=provider,
                        provider_version=provider_version,
                        status=WebSearchHealthStatus.UPSTREAM_ERROR,
                        checked_at=datetime.now(UTC),
                        error_code="web_search_health_check",
                    )
                )
                continue
            try:
                result = checker()
            except Exception:  # noqa: BLE001 - 单一健康检查失败不影响整体聚合
                result = WebSearchHealth(
                    provider=provider,
                    provider_version=provider_version,
                    status=WebSearchHealthStatus.UPSTREAM_ERROR,
                    checked_at=datetime.now(UTC),
                    error_code="web_search_health_check",
                )
            health.append(
                result.model_copy(
                    update={
                        "provider": provider,
                        "provider_version": provider_version,
                    }
                )
            )
        return aggregate_public_search_health(health)
