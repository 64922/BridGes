"""搜索阶段状态机与可交付结果选择。"""

from __future__ import annotations

import time
from threading import Event

from bridges import public_search_budget as search_budget
from bridges.state_copy import (
    WEB_SEARCH_INTERNAL_TEXT,
    WEB_SEARCH_PROVIDER_UNREADY_TEXT,
    WEB_SEARCH_STAGE_TIMEOUT_TEXT,
)
from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import (
    WebSearchHealthStatus,
    WebSearchPageClassification,
    WebSearchProjection,
    WebSearchProviderAttempt,
    WebSearchStatus,
)
from bridges.web_search.service_plan import SearchPlan
from bridges.web_search.service_runtime import (
    _elapsed_ms,
    _user_cancelled,
)
from bridges.web_search.service_state import _WebSearchServiceState


class _WebSearchSearchMixin(_WebSearchServiceState):
    def initial_projection(
        self, plan: SearchPlan, *, recovery: bool = False
    ) -> WebSearchProjection | None:
        if not plan.should_search:
            return None
        # Issue 04/01：Tavily 明确非 READY 时，本轮仍按 Issue 03 做受控真实尝试，
        # 但 UI/消息立即显示降级提示，避免把用户查询阶段与探针状态割裂。
        monitor = self._health_monitor
        degraded = False
        if monitor is not None:
            snapshot = monitor.peek()
            degraded = (
                snapshot is not None
                and not snapshot.pending
                and snapshot.status != WebSearchHealthStatus.READY
            )
        return self._projection(
            plan,
            status=(
                WebSearchStatus.RECOVERY
                if recovery or degraded
                else WebSearchStatus.LOADING
            ),
            can_cancel=True,
            error_message=(
                WEB_SEARCH_PROVIDER_UNREADY_TEXT if degraded else None
            ),
        )

    def search(
        self,
        account_id: str,
        plan: SearchPlan,
        *,
        stop_event: Event | None = None,
        deadline: float | None = None,
        stage_deadline: float | None = None,
    ) -> WebSearchProjection | None:
        if not plan.should_search:
            return None
        started = time.monotonic()
        if _user_cancelled(stop_event):
            result = self._cancelled_projection(
                plan,
                attempts=0,
                query_count=0,
                query_history=[],
                provider_attempts=[],
            )
            self._audit(
                account_id,
                plan,
                result,
                duration_ms=_elapsed_ms(started),
            )
            return result
        now = self._clock()
        cached = self._cache.get(account_id, plan, now)
        if cached is not None:
            cached = cached.model_copy(
                update={
                    "plan_id": plan.plan_id,
                    "query_hash": plan.query_hash,
                    "cache_hit": True,
                }
            )
            self._audit(account_id, plan, cached, duration_ms=_elapsed_ms(started))
            return cached
        if _user_cancelled(stop_event):
            result = self._cancelled_projection(
                plan,
                attempts=0,
                query_count=0,
                query_history=[],
                provider_attempts=[],
            )
            self._audit(account_id, plan, result, duration_ms=_elapsed_ms(started))
            return result

        cooldown_until = self._active_provider_cooldown(now)
        if cooldown_until is not None:
            result = self._projection(
                plan,
                status=WebSearchStatus.ERROR,
                searched_at=now,
                error_code="web_search_provider_challenge",
                error_message=(
                    "搜索提供方（Tavily）暂时受阻，正在冷却；请稍后显式重试，"
                    "系统不会在本轮自动重复请求。"
                ),
                can_retry=True,
                page_classification=WebSearchPageClassification.CHALLENGE,
                cooldown_until=cooldown_until,
            )
            self._audit(account_id, plan, result, duration_ms=_elapsed_ms(started))
            return result

        now_monotonic = time.monotonic()
        explicit_stage_deadline = stage_deadline is not None
        stage_timeout_enabled = explicit_stage_deadline or deadline is None
        stage_limit = now_monotonic + max(
            0.0, min(plan.total_timeout_seconds, search_budget.PUBLIC_SEARCH_STAGE_SECONDS)
        )
        if stage_deadline is None:
            if deadline is None:
                stage_deadline = stage_limit
                provider_deadline = stage_deadline - search_budget.SEARCH_HANDOFF_RESERVE_SECONDS
            else:
                # 兼容旧调用方：传入的单一 deadline 本身就是硬截止，
                # 不在调用方未声明阶段预算时偷偷追加收尾时间。
                stage_deadline = min(deadline, stage_limit)
                provider_deadline = stage_deadline
        else:
            stage_deadline = min(stage_deadline, stage_limit)
            provider_deadline = min(
                deadline if deadline is not None else stage_deadline,
                stage_deadline - search_budget.SEARCH_HANDOFF_RESERVE_SECONDS,
            )
        provider_deadline = max(now_monotonic, provider_deadline)
        stage_deadline = max(provider_deadline, stage_deadline)

        attempts = 0
        query_history: list[str] = []
        provider_attempts: list[WebSearchProviderAttempt] = []
        query = plan.query or (plan.queries[0] if plan.queries else "")
        retry_query = self._rewrite_query(plan, query_history)
        last_error: BaseException | None = None
        last_result_metadata: tuple[
            WebSearchPageClassification | None, str | None
        ] = (None, None)

        while attempts < 1 + max(0, plan.max_retries) and query:
            if _user_cancelled(stop_event):
                result = self._cancelled_projection(
                    plan,
                    attempts=attempts,
                    query_count=len(query_history),
                    query_history=query_history,
                    provider_attempts=provider_attempts,
                )
                return self._finish_search_result(
                    account_id, plan, result, started=started, deadline=stage_deadline
                )
            if time.monotonic() >= provider_deadline:
                break

            attempt_number = attempts + 1
            attempt_started = time.monotonic()
            attempt_started_at = self._clock()
            (
                round_results,
                round_errors,
                sent_count,
                round_page_classifications,
                round_http_status_categories,
            ) = self._run_queries(
                (query,),
                deadline=provider_deadline,
                stage_deadline=(stage_deadline if stage_timeout_enabled else None),
                stop_event=stop_event,
            )
            if not sent_count:
                break
            if sent_count:
                attempts += sent_count
                query_history.append(query)

            error = self._last_search_error(round_errors)
            if error is None and round_errors:
                error =                 WebSearchError(
                    "web_search_internal", WEB_SEARCH_INTERNAL_TEXT, retryable=False
                )
            if round_page_classifications:
                last_result_metadata = (
                    round_page_classifications[-1],
                    round_http_status_categories[-1]
                    if round_http_status_categories
                    else None,
                )
            elif isinstance(error, WebSearchError):
                last_result_metadata = (
                    error.page_classification,
                    error.http_status_category,
                )

            attempt = self._provider_attempt(
                self._provider,
                self._provider_version,
                round_results,
                error,
                round_results,
                duration_ms=_elapsed_ms(attempt_started),
                attempt_number=attempt_number,
                query=query,
                started_at=attempt_started_at,
                ended_at=self._clock(),
                page_classification=last_result_metadata[0],
                http_status_category=last_result_metadata[1],
            )
            provider_attempts.append(attempt)

            if round_results:
                result = self._project_results(
                    plan,
                    self._annotate_results(
                        round_results, self._provider, self._provider_version
                    ),
                    attempts,
                    query_count=len(query_history),
                    query_history=query_history,
                    page_classification=last_result_metadata[0],
                    http_status_category=last_result_metadata[1],
                    provider_attempts=provider_attempts,
                )
                return self._finish_search_result(
                    account_id, plan, result, started=started, deadline=stage_deadline
                )

            if _user_cancelled(stop_event):
                return self._finish_search_result(
                    account_id,
                    plan,
                    self._cancelled_projection(
                        plan,
                        attempts=attempts,
                        query_count=len(query_history),
                        query_history=query_history,
                        provider_attempts=provider_attempts,
                    ),
                    started=started,
                    deadline=stage_deadline,
                )
            last_error = error
            if error is None:
                # 空结果允许一次有界改写；这也是第二次请求的唯一用途，
                # 不会再叠加一次“同查询重试”。
                if attempts >= 1 + plan.max_retries:
                    break
                query = retry_query or ""
                retry_query = None
                if not query:
                    break
                provider_attempts[-1] = provider_attempts[-1].model_copy(
                    update={"retry_planned": True}
                )
                if not self._wait_retry_backoff(
                    stop_event=stop_event, stage_deadline=stage_deadline
                ):
                    break
                continue

            if attempts >= 1 + plan.max_retries or not self._retry_allowed(
                error,
                attempts=attempts,
                stage_deadline=stage_deadline,
                stop_event=stop_event,
            ):
                break
            provider_attempts[-1] = provider_attempts[-1].model_copy(
                update={"retry_planned": True}
            )
            if not self._wait_retry_backoff(
                stop_event=stop_event, stage_deadline=stage_deadline
            ):
                break
            # 错误重试固定重发原查询；查询改写只用于空结果分支，
            # 因而本轮最多产生两次 Tavily HTTP 请求。
            query = plan.query or query

        if _user_cancelled(stop_event):
            result = self._cancelled_projection(
                plan,
                attempts=attempts,
                query_count=len(query_history),
                query_history=query_history,
                provider_attempts=provider_attempts,
            )
        elif last_error is not None:
            result = self._error_projection(
                plan,
                last_error,
                attempts=attempts,
                query_count=len(query_history),
                query_history=query_history,
                provider_attempts=provider_attempts,
                metadata=last_result_metadata,
            )
        elif stage_timeout_enabled and time.monotonic() >= stage_deadline and attempts == 0:
            result = self._projection(
                plan,
                status=WebSearchStatus.ERROR,
                searched_at=self._clock(),
                error_code="web_search_stage_timeout",
                error_message=WEB_SEARCH_STAGE_TIMEOUT_TEXT,
                can_retry=True,
                attempt_count=attempts,
                query_count=len(query_history),
                query_history=query_history,
                provider_attempts=provider_attempts,
            )
        elif attempts == 0 and time.monotonic() >= provider_deadline:
            # 没有任何真实尝试形成投影（预算在发起前耗尽）：provider 超时。
            result = self._timeout_projection(
                plan,
                attempts,
                query_count=len(query_history),
                query_history=query_history,
                provider_attempts=provider_attempts,
            )
        else:
            result = self._project_results(
                plan,
                [],
                attempts,
                query_count=len(query_history),
                query_history=query_history,
                page_classification=last_result_metadata[0],
                http_status_category=last_result_metadata[1],
                provider_attempts=provider_attempts,
            )
        return self._finish_search_result(
            account_id, plan, result, started=started, deadline=stage_deadline
        )
