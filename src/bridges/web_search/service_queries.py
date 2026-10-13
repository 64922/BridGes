"""查询执行与来源尝试记录。"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ALL_COMPLETED, Future, wait
from datetime import datetime, timedelta
from functools import partial
from threading import Event
from typing import Any, cast

from bridges.state_copy import WEB_SEARCH_INTERNAL_TEXT, WEB_SEARCH_STAGE_TIMEOUT_TEXT
from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import (
    WebSearchPageClassification,
    WebSearchProjection,
    WebSearchProviderAttempt,
    WebSearchResult,
    WebSearchStatus,
)
from bridges.web_search.service_plan import SearchPlan
from bridges.web_search.service_runtime import (
    _elapsed_ms,
    _invoke_search_client,
    _SearchStopSignal,
    _submit_daemon,
    _TimedFutureResult,
    _user_cancelled,
)
from bridges.web_search.service_state import _WebSearchServiceState


class _WebSearchQueryMixin(_WebSearchServiceState):
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
        """在阶段预算内并行执行一组最小查询。"""

        if _user_cancelled(stop_event):
            return [], [], 0, [], []
        request_queries = queries[:1]
        if not request_queries or time.monotonic() >= deadline:
            return [], [], 0, [], []
        query_stop_event = _SearchStopSignal(stop_event)
        futures = {
            _submit_daemon(
                partial(self._search_one, query, deadline, query_stop_event),
                name="web-search-query",
            ): query
            for query in request_queries
        }
        pending = set(futures)
        done: set[Future[_TimedFutureResult]] = set()
        cleanup_deadline = (
            stage_deadline
            if stage_deadline is not None
            else deadline
        )
        try:
            while pending:
                if _user_cancelled(stop_event):
                    query_stop_event.set()
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    query_stop_event.set()
                    break
                completed, _ = wait(
                    pending,
                    timeout=min(0.05, remaining),
                    return_when=ALL_COMPLETED,
                )
                done.update(completed)
                pending.difference_update(completed)

            # 截止边界后的交接窗口只负责重新观察真实完成状态；旧的
            # ``done`` 快照不能覆盖在窗口内完成的 future。
            if pending and not _user_cancelled(stop_event):
                query_stop_event.set()
                while pending and time.monotonic() < cleanup_deadline:
                    completed, _ = wait(
                        pending,
                        timeout=min(0.01, cleanup_deadline - time.monotonic()),
                        return_when=ALL_COMPLETED,
                    )
                    done.update(completed)
                    pending.difference_update(completed)
            results: list[WebSearchResult] = []
            errors: list[BaseException] = []
            page_classifications: list[WebSearchPageClassification] = []
            http_status_categories: list[str] = []
            for future in futures:
                if future not in done:
                    timeout_code = "web_search_stage_timeout" if (
                        stage_deadline is not None
                        and time.monotonic() >= stage_deadline
                    ) else "web_search_timeout"
                    errors.append(
                        WebSearchError(
                            timeout_code,
                            (
                                WEB_SEARCH_STAGE_TIMEOUT_TEXT
                                if timeout_code == "web_search_stage_timeout"
                                else "联网搜索超时，请重试。"
                            ),
                        )
                    )
                    continue
                try:
                    timed = future.result()
                except Exception:  # noqa: BLE001 - 单查询失败不拖垮整轮
                    errors.append(
                        WebSearchError(
                            "web_search_internal",
                            WEB_SEARCH_INTERNAL_TEXT,
                            retryable=False,
                        )
                    )
                else:
                    value = timed.value
                    if stage_deadline is not None and timed.finished_at > stage_deadline:
                        errors.append(
                            WebSearchError(
                                "web_search_stage_timeout",
                                WEB_SEARCH_STAGE_TIMEOUT_TEXT,
                            )
                        )
                    elif timed.finished_at > deadline:
                        errors.append(
                            WebSearchError("web_search_timeout", "联网搜索超时，请重试。")
                        )
                    elif isinstance(value, BaseException):
                        errors.append(value)
                    else:
                        results.extend(cast(list[WebSearchResult], value))
                        classification = getattr(value, "page_classification", None)
                        if classification is not None:
                            page_classifications.append(classification)
                        status_category = getattr(value, "http_status_category", None)
                        if status_category is not None:
                            http_status_categories.append(status_category)
            return (
                results,
                errors,
                len(request_queries),
                page_classifications,
                http_status_categories,
            )
        finally:
            for future in pending:
                future.cancel()

    @staticmethod
    def _annotate_results(
        results: list[WebSearchResult], provider: str, provider_version: str
    ) -> list[WebSearchResult]:
        return [
            result.model_copy(
                update={"provider": provider, "provider_version": provider_version}
            )
            for result in results
        ]

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
        if error is not None:
            result_code = getattr(error, "code", "web_search_provider")
            classification = getattr(error, "page_classification", None)
            status_category = getattr(error, "http_status_category", None)
        else:
            result_code = "success" if results else "web_search_no_results"
            classification = getattr(raw_results, "page_classification", None)
            status_category = getattr(raw_results, "http_status_category", None)
        return WebSearchProviderAttempt(
            provider=provider,
            provider_version=provider_version,
            attempt_number=attempt_number,
            query_hash=(
                hashlib.sha256(query.encode("utf-8")).hexdigest()
                if query is not None
                else None
            ),
            started_at=started_at,
            ended_at=ended_at,
            result_code=result_code,
            result_count=len(results),
            duration_ms=duration_ms,
            http_status_category=http_status_category or status_category,
            retry_planned=retry_planned,
            page_classification=page_classification or classification,
        )

    def _finish_search_result(
        self,
        account_id: str,
        plan: SearchPlan,
        result: WebSearchProjection,
        *,
        started: float,
        deadline: float,
    ) -> WebSearchProjection:
        if result.status in {WebSearchStatus.SUCCESS, WebSearchStatus.PARTIAL}:
            expires_at = self._clock() + timedelta(
                seconds=plan.freshness_window_seconds
            )
            result = result.model_copy(update={"cache_expires_at": expires_at})
            self._cache.put(account_id, plan, result, expires_at)
        self._audit(
            account_id,
            plan,
            result,
            duration_ms=_elapsed_ms(started),
            deadline=deadline,
        )
        return result

    def _search_one(
        self, query: str, deadline: float, stop_event: Any | None
    ) -> list[WebSearchResult]:
        return _invoke_search_client(
            self._client,
            query,
            deadline=deadline,
            stop_event=stop_event,
        )

    @staticmethod
    def _merge_results(
        existing: list[WebSearchResult], incoming: list[WebSearchResult]
    ) -> list[WebSearchResult]:
        merged = [*existing]
        seen_urls = {result.url for result in merged}
        seen_ids = {result.result_id for result in merged}
        for result in incoming:
            if result.url in seen_urls:
                continue
            if result.result_id in seen_ids:
                result = result.model_copy(update={"result_id": f"web-{len(merged) + 1}"})
            merged.append(result)
            seen_urls.add(result.url)
            seen_ids.add(result.result_id)
        return merged

    @staticmethod
    def _rewrite_query(plan: SearchPlan, history: list[str]) -> str | None:
        base = plan.query.strip()
        if not base:
            return None
        candidate = f"{' '.join(base.split()[:4])} 基础定义 原理"[:80].strip()
        return candidate if candidate not in history else None
