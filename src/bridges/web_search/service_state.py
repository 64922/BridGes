"""公网搜索服务 mixin 的共享状态和方法合同。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import Event, RLock
from typing import Any

from bridges.observability.service import ObservabilityService
from bridges.web_search.contracts import (
    WebSearchPageClassification,
    WebSearchProjection,
    WebSearchProviderAttempt,
    WebSearchResult,
    WebSearchStatus,
)
from bridges.web_search.health_monitor import WebSearchHealthMonitor
from bridges.web_search.service_plan import (
    LocalQueryPlanner,
    SearchClient,
    SearchPlan,
    WebSearchCache,
)


class _WebSearchServiceState:
    _client: SearchClient
    _provider: str
    _provider_version: str
    _request_profile_version: str
    _planner: LocalQueryPlanner
    _observability: ObservabilityService | None
    _cache: WebSearchCache
    _clock: Callable[[], datetime]
    _sleeper: Callable[[float], None]
    _provider_cooldown_until: datetime | None
    _provider_state_lock: RLock
    _health_monitor: WebSearchHealthMonitor | None

    def initial_projection(
        self, plan: SearchPlan, *, recovery: bool = False
    ) -> WebSearchProjection | None:
        raise NotImplementedError

    def search(
        self,
        account_id: str,
        plan: SearchPlan,
        *,
        stop_event: Event | None = None,
        deadline: float | None = None,
        stage_deadline: float | None = None,
    ) -> WebSearchProjection | None:
        raise NotImplementedError

    def _run_queries(
        self,
        queries: tuple[str, ...],
        *,
        deadline: float,
        stage_deadline: float | None = None,
        stop_event: Event | None,
    ) -> tuple[
        list[WebSearchResult],
        list[BaseException],
        int,
        list[WebSearchPageClassification],
        list[str],
    ]:
        raise NotImplementedError

    @staticmethod
    def _annotate_results(
        results: list[WebSearchResult], provider: str, provider_version: str
    ) -> list[WebSearchResult]:
        raise NotImplementedError

    @staticmethod
    def _provider_attempt(
        provider: str,
        provider_version: str,
        results: list[WebSearchResult],
        error: BaseException | None,
        raw_results: list[WebSearchResult],
        *,
        duration_ms: int,
        attempt_number: int = 1,
        query: str | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        retry_planned: bool = False,
        page_classification: WebSearchPageClassification | None = None,
        http_status_category: str | None = None,
    ) -> WebSearchProviderAttempt:
        raise NotImplementedError

    def _finish_search_result(
        self,
        account_id: str,
        plan: SearchPlan,
        result: WebSearchProjection,
        *,
        started: float,
        deadline: float,
    ) -> WebSearchProjection:
        raise NotImplementedError

    def _search_one(
        self, query: str, deadline: float, stop_event: Any | None
    ) -> list[WebSearchResult]:
        raise NotImplementedError

    @staticmethod
    def _merge_results(
        existing: list[WebSearchResult], incoming: list[WebSearchResult]
    ) -> list[WebSearchResult]:
        raise NotImplementedError

    @staticmethod
    def _rewrite_query(plan: SearchPlan, history: list[str]) -> str | None:
        raise NotImplementedError

    def _project_results(
        self,
        plan: SearchPlan,
        results: list[WebSearchResult],
        attempts: int,
        *,
        query_count: int,
        query_history: list[str],
        page_classification: WebSearchPageClassification | None = None,
        http_status_category: str | None = None,
        provider: str | None = None,
        provider_version: str | None = None,
        selected_provider: str | None = None,
        selected_provider_version: str | None = None,
        cooldown_until: datetime | None = None,
        provider_attempts: list[WebSearchProviderAttempt] | None = None,
    ) -> WebSearchProjection:
        raise NotImplementedError

    @staticmethod
    def _error_status(code: str, permission: bool) -> WebSearchStatus:
        raise NotImplementedError

    def _timeout_projection(
        self,
        plan: SearchPlan,
        attempts: int,
        *,
        query_count: int,
        query_history: list[str],
        provider_attempts: list[WebSearchProviderAttempt] | None = None,
        metadata: tuple[WebSearchPageClassification | None, str | None] = (None, None),
    ) -> WebSearchProjection:
        raise NotImplementedError

    def _cancelled_projection(
        self,
        plan: SearchPlan,
        *,
        attempts: int,
        query_count: int,
        query_history: list[str],
        provider_attempts: list[WebSearchProviderAttempt],
    ) -> WebSearchProjection:
        raise NotImplementedError

    @staticmethod
    def _last_search_error(errors: list[BaseException]) -> BaseException | None:
        raise NotImplementedError

    def _error_projection(
        self,
        plan: SearchPlan,
        error: BaseException,
        *,
        attempts: int,
        query_count: int,
        query_history: list[str],
        provider_attempts: list[WebSearchProviderAttempt],
        metadata: tuple[WebSearchPageClassification | None, str | None],
    ) -> WebSearchProjection:
        raise NotImplementedError

    def _retry_allowed(
        self,
        error: BaseException,
        *,
        attempts: int,
        stage_deadline: float,
        stop_event: Event | None,
    ) -> bool:
        raise NotImplementedError

    def _wait_retry_backoff(
        self, *, stop_event: Event | None, stage_deadline: float
    ) -> bool:
        raise NotImplementedError

    @staticmethod
    def _projection(
        plan: SearchPlan,
        *,
        status: WebSearchStatus,
        results: list[WebSearchResult] | None = None,
        searched_at: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        can_retry: bool = False,
        can_cancel: bool = False,
        attempt_count: int = 0,
        query_count: int = 0,
        query_history: list[str] | None = None,
        cache_expires_at: datetime | None = None,
        page_classification: WebSearchPageClassification | None = None,
        http_status_category: str | None = None,
        cooldown_until: datetime | None = None,
        provider: str | None = None,
        provider_version: str | None = None,
        selected_provider: str | None = None,
        selected_provider_version: str | None = None,
        provider_attempts: list[WebSearchProviderAttempt] | None = None,
    ) -> WebSearchProjection:
        raise NotImplementedError

    def _audit(
        self,
        account_id: str,
        plan: SearchPlan,
        result: WebSearchProjection,
        *,
        duration_ms: int = 0,
        deadline: float | None = None,
    ) -> None:
        raise NotImplementedError

    def _active_provider_cooldown(self, now: datetime) -> datetime | None:
        raise NotImplementedError

    def _activate_provider_cooldown(
        self, now: datetime, seconds: int
    ) -> datetime:
        raise NotImplementedError
