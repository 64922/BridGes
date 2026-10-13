"""搜索错误、重试、投影与审计。"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta
from threading import Event

from bridges import public_search_budget as search_budget
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.state_copy import WEB_SEARCH_CANCELLED_TEXT
from bridges.web_search.client import DEFAULT_PROVIDER_COOLDOWN_SECONDS, WebSearchError
from bridges.web_search.contracts import (
    WebSearchPageClassification,
    WebSearchProjection,
    WebSearchProviderAttempt,
    WebSearchResult,
    WebSearchStatus,
)
from bridges.web_search.service_plan import SearchPlan
from bridges.web_search.service_runtime import _user_cancelled
from bridges.web_search.service_state import _WebSearchServiceState


class _WebSearchOutcomesMixin(_WebSearchServiceState):
    @staticmethod
    def _error_status(code: str, permission: bool) -> WebSearchStatus:
        if permission:
            return WebSearchStatus.PERMISSION
        if code.startswith("web_search_page_"):
            return WebSearchStatus.FETCH_ERROR
        return WebSearchStatus.ERROR

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
        return self._projection(
            plan,
            status=WebSearchStatus.ERROR,
            searched_at=self._clock(),
            error_code="web_search_timeout",
            error_message="联网搜索超时，请重试。",
            can_retry=True,
            attempt_count=attempts,
            query_count=query_count,
            query_history=query_history,
            page_classification=metadata[0],
            http_status_category=metadata[1],
            provider_attempts=provider_attempts,
        )

    def _cancelled_projection(
        self,
        plan: SearchPlan,
        *,
        attempts: int,
        query_count: int,
        query_history: list[str],
        provider_attempts: list[WebSearchProviderAttempt],
    ) -> WebSearchProjection:
        return self._projection(
            plan,
            status=WebSearchStatus.CANCELLED,
            searched_at=self._clock(),
            error_code="web_search_cancelled",
            error_message=WEB_SEARCH_CANCELLED_TEXT,
            can_retry=False,
            can_cancel=False,
            attempt_count=attempts,
            query_count=query_count,
            query_history=query_history,
            provider_attempts=provider_attempts,
        )

    @staticmethod
    def _last_search_error(errors: list[BaseException]) -> BaseException | None:
        """优先保留最后一次真实 provider 错误，避免被泛化错误覆盖。"""
        return errors[-1] if errors else None

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
        if isinstance(error, WebSearchError):
            code = error.code
            message = error.message
            permission = error.permission
            can_retry = error.retryable
            classification = error.page_classification or metadata[0]
            status_category = error.http_status_category or metadata[1]
            cooldown_until = None
            if code in {
                "web_search_provider_challenge",
                "web_search_rate_limit",
            }:
                cooldown_until = self._activate_provider_cooldown(
                    self._clock(), error.cooldown_seconds or DEFAULT_PROVIDER_COOLDOWN_SECONDS
                )
            status = (
                WebSearchStatus.CANCELLED
                if code == "web_search_cancelled"
                else self._error_status(code, permission)
            )
            if status == WebSearchStatus.CANCELLED:
                can_retry = False
        else:
            code = "web_search_internal"
            message = "公网搜索服务发生内部异常，请重试。"
            can_retry = False
            classification = metadata[0]
            status_category = metadata[1]
            cooldown_until = None
            status = WebSearchStatus.ERROR
        return self._projection(
            plan,
            status=status,
            searched_at=self._clock(),
            error_code=code,
            error_message=message,
            can_retry=can_retry,
            attempt_count=attempts,
            query_count=query_count,
            query_history=query_history,
            page_classification=classification,
            http_status_category=status_category,
            cooldown_until=cooldown_until,
            provider_attempts=provider_attempts,
        )

    def _retry_allowed(
        self,
        error: BaseException,
        *,
        attempts: int,
        stage_deadline: float,
        stop_event: Event | None,
    ) -> bool:
        if attempts >= 2 or _user_cancelled(stop_event) or not isinstance(error, WebSearchError):
            return False
        retryable_codes = {
            "web_search_connect",
            "web_search_dns",
            "web_search_offline",
            "web_search_provider",
        }
        if error.code == "web_search_timeout":
            retryable = True
        elif error.code == "web_search_provider":
            # 只对明确可重试的 5xx 重试；无 HTTP 状态类别（非真实上游响应）
            # 或 4xx 不立即重试（AC4/AC5）。
            retryable = (
                error.http_status_category is not None
                and error.http_status_category.startswith("5")
            )
        else:
            retryable = error.code in retryable_codes
        if not error.retryable or not retryable:
            return False
        required = (
            search_budget.SEARCH_RETRY_BACKOFF_SECONDS
            + search_budget.SEARCH_MIN_REQUEST_WINDOW_SECONDS
            + search_budget.SEARCH_HANDOFF_RESERVE_SECONDS
        )
        return stage_deadline - time.monotonic() >= required

    def _wait_retry_backoff(
        self, *, stop_event: Event | None, stage_deadline: float
    ) -> bool:
        backoff = search_budget.SEARCH_RETRY_BACKOFF_SECONDS
        if stage_deadline - time.monotonic() < backoff:
            return False
        waiter = getattr(stop_event, "wait", None)
        if callable(waiter) and waiter(backoff):
            return False
        if not callable(waiter):
            self._sleeper(backoff)
        return not _user_cancelled(stop_event) and stage_deadline - time.monotonic() >= (
            search_budget.SEARCH_MIN_REQUEST_WINDOW_SECONDS
            + search_budget.SEARCH_HANDOFF_RESERVE_SECONDS
        )

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
        rewrite_count = sum(
            query not in plan.queries for query in (query_history or [])
        )
        trigger_reason = plan.reason
        if rewrite_count:
            trigger_reason = f"{plan.reason}；已按阶段预算改写查询 {rewrite_count} 次"
        return WebSearchProjection(
            status=status,
            trigger_reason=trigger_reason,
            query_summary=plan.query,
            query_history=query_history or [],
            results=results or [],
            searched_at=searched_at,
            error_code=error_code,
            error_message=error_message,
            http_status_category=http_status_category,
            page_classification=page_classification,
            cooldown_until=cooldown_until,
            can_retry=can_retry,
            can_cancel=can_cancel,
            plan_id=plan.plan_id,
            provider=provider or plan.provider,
            provider_version=provider_version or plan.provider_version,
            selected_provider=selected_provider,
            selected_provider_version=selected_provider_version,
            provider_attempts=provider_attempts or [],
            rules_version=plan.rules_version,
            query_hash=plan.query_hash,
            original_query_hash=plan.original_query_hash,
            freshness_window_seconds=plan.freshness_window_seconds,
            cache_expires_at=cache_expires_at,
            attempt_count=attempt_count,
            query_count=query_count,
            deleted_categories=list(plan.deleted_categories),
        )

    def _audit(
        self,
        account_id: str,
        plan: SearchPlan,
        result: WebSearchProjection,
        *,
        duration_ms: int = 0,
        deadline: float | None = None,
    ) -> None:
        if self._observability is None:
            return
        if result.status in {WebSearchStatus.SUCCESS, WebSearchStatus.PARTIAL}:
            audit_result = AuditResult.SUCCESS
        elif result.status == WebSearchStatus.CANCELLED:
            audit_result = AuditResult.BLOCKED
        elif result.status == WebSearchStatus.PERMISSION:
            audit_result = AuditResult.DENIED
        elif result.status in {
            WebSearchStatus.EMPTY,
            WebSearchStatus.EVIDENCE_INSUFFICIENT,
            WebSearchStatus.FETCH_ERROR,
        }:
            audit_result = AuditResult.DEGRADED
        else:
            audit_result = AuditResult.RETRYABLE_FAIL
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.WEB_SEARCH,
            result=audit_result,
            reason=plan.reason,
            details={
                "data_categories": ["public_query_terms"],
                "authorization_snapshot": "authz-1.0",
                "provider": result.provider,
                "provider_version": result.provider_version,
                "selected_provider": result.selected_provider,
                "selected_provider_version": result.selected_provider_version,
                "provider_attempts": [
                    attempt.model_dump(mode="json")
                    for attempt in result.provider_attempts
                ],
                "rules_version": plan.rules_version,
                "plan_id": plan.plan_id,
                "query_hash": plan.query_hash,
                "original_query_hash": plan.original_query_hash,
                "deleted_categories": list(plan.deleted_categories),
                "query_length": len(plan.query),
                "result_count": len(result.results),
                "attempt_count": result.attempt_count,
                "query_count": result.query_count,
                "query_history_hashes": [
                    hashlib.sha256(query.encode("utf-8")).hexdigest()
                    for query in result.query_history
                ],
                "query_history_count": len(result.query_history),
                "query_rewrite_count": sum(
                    query not in plan.queries for query in result.query_history
                ),
                "cache_hit": result.cache_hit,
                "status": result.status.value,
                "http_status_category": result.http_status_category,
                "page_classification": (
                    result.page_classification.value
                    if result.page_classification is not None
                    else None
                ),
                "request_profile_version": self._request_profile_version,
                "cooldown_active": result.cooldown_until is not None,
                "stage_duration_ms": duration_ms,
                "active_sources": [
                    *dict.fromkeys(
                        attempt.provider for attempt in result.provider_attempts
                    )
                ]
                or [result.provider],
                "budget_source": "web_search",
                "deadline_remaining_ms": (
                    max(0, int((deadline - time.monotonic()) * 1000))
                    if deadline is not None
                    else None
                ),
            },
        )

    def _active_provider_cooldown(self, now: datetime) -> datetime | None:
        with self._provider_state_lock:
            cooldown_until = self._provider_cooldown_until
            if cooldown_until is None:
                return None
            if cooldown_until <= now:
                self._provider_cooldown_until = None
                return None
            return cooldown_until

    def _activate_provider_cooldown(
        self, now: datetime, seconds: int
    ) -> datetime:
        cooldown_until = now + timedelta(seconds=max(1, seconds))
        with self._provider_state_lock:
            if (
                self._provider_cooldown_until is None
                or cooldown_until > self._provider_cooldown_until
            ):
                self._provider_cooldown_until = cooldown_until
            return self._provider_cooldown_until
