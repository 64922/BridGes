"""搜索来源筛选与结果状态投影。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bridges.web_search.contracts import (
    WebSearchPageClassification,
    WebSearchProjection,
    WebSearchProviderAttempt,
    WebSearchResult,
    WebSearchStatus,
)
from bridges.web_search.service_plan import SearchPlan
from bridges.web_search.service_state import _WebSearchServiceState


class _WebSearchResultsMixin(_WebSearchServiceState):
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
        resolved_provider = provider or plan.provider
        resolved_provider_version = provider_version or plan.provider_version
        provider_fields: dict[str, Any] = {
            "provider": resolved_provider,
            "provider_version": resolved_provider_version,
            "selected_provider": selected_provider or (resolved_provider if results else None),
            "selected_provider_version": selected_provider_version
            or (resolved_provider_version if results else None),
            "provider_attempts": provider_attempts,
        }
        conflicting = [
            result
            for result in results
            if getattr(result, "verification", "verified") == "conflicting"
        ]
        if conflicting:
            return self._projection(
                plan,
                status=WebSearchStatus.SOURCE_CONFLICT,
                results=results,
                searched_at=self._clock(),
                error_code="web_search_source_conflict",
                error_message="多个公开来源对当前事实给出冲突信息，暂不能形成确定结论。",
                can_retry=True,
                attempt_count=attempts,
                query_count=query_count,
                query_history=query_history,
                page_classification=page_classification,
                http_status_category=http_status_category,
                cooldown_until=cooldown_until,
                **provider_fields,
            )
        verified = [
            result
            for result in results
            if getattr(result, "verification", "verified")
            in {"verified", "cross_verified", "structured"}
        ]
        failed = [
            result
            for result in results
            if getattr(result, "verification", "verified") == "fetch_failed"
        ]
        if not results:
            if page_classification == WebSearchPageClassification.NORMAL_RESULTS:
                return self._projection(
                    plan,
                    status=WebSearchStatus.EVIDENCE_INSUFFICIENT,
                    searched_at=self._clock(),
                    error_code="web_search_evidence_insufficient",
                    error_message="搜索页面包含结果节点，但没有可安全引用的公开来源。",
                    can_retry=True,
                    attempt_count=attempts,
                    query_count=query_count,
                    query_history=query_history,
                    page_classification=page_classification,
                    http_status_category=http_status_category,
                    cooldown_until=cooldown_until,
                    **provider_fields,
                )
            return self._projection(
                plan,
                status=WebSearchStatus.EMPTY,
                searched_at=self._clock(),
                error_code="web_search_no_results",
                error_message="没有找到可核实的公开网页结果。",
                can_retry=True,
                attempt_count=attempts,
                query_count=query_count,
                query_history=query_history,
                page_classification=page_classification,
                http_status_category=http_status_category,
                cooldown_until=cooldown_until,
                **provider_fields,
            )
        if not verified:
            code = "web_search_page_fetch" if failed else "web_search_evidence_insufficient"
            message = (
                "搜索结果对应的页面无法抓取，尚未完成本次联网核验。"
                if failed
                else "当前只有搜索摘要，尚未获得可核验的来源页面。"
            )
            return self._projection(
                plan,
                status=(
                    WebSearchStatus.FETCH_ERROR
                    if failed
                    else WebSearchStatus.EVIDENCE_INSUFFICIENT
                ),
                results=results,
                searched_at=self._clock(),
                error_code=code,
                error_message=message,
                can_retry=True,
                attempt_count=attempts,
                query_count=query_count,
                query_history=query_history,
                page_classification=page_classification,
                http_status_category=http_status_category,
                cooldown_until=cooldown_until,
                **provider_fields,
            )
        status = WebSearchStatus.PARTIAL if failed else WebSearchStatus.SUCCESS
        return self._projection(
            plan,
            status=status,
            results=results,
            searched_at=self._clock(),
            error_message=None,
            can_retry=False,
            attempt_count=attempts,
            query_count=query_count,
            query_history=query_history,
            page_classification=page_classification,
            http_status_category=http_status_category,
            cooldown_until=cooldown_until,
            **provider_fields,
        )
