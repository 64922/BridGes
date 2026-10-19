"""将浏览器观察转换为现有取证合同，官方查询仍走官方域名搜索。"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.browser_nested import (
    DEFAULT_NESTED_PAGES_LIMIT,
    MAX_NESTED_PARENTS_PER_PAGE,
)
from bridges.tieba.browser_read_projection import read_projection
from bridges.tieba.browser_runtime import TiebaBrowserError, TiebaBrowserRuntime
from bridges.tieba.browser_search_scope import scope_detail, search_status
from bridges.tieba.budget import current_request_budget, current_request_guard
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaReadResult,
    TiebaSearchHit,
    TiebaSearchScope,
)
from bridges.tieba.searching import (
    QUERY_SCOPE_PREFIX,
    HitDiagnostics,
    SearchOutcome,
    TiebaSearchPort,
    canonical_url,
    classify_hits_with_diagnostics,
    execution_terms,
    merge_search_hits,
    query_record,
    thread_id_of,
)

BROWSER_SCROLL_ROUNDS_LIMIT = 8


def browser_terms(query: str) -> str:
    """把计划查询转换成浏览器实际发送的吧内检索词。"""
    if tuple(query.split()[:2]) != QUERY_SCOPE_PREFIX:
        raise ValueError("只接受目标贴吧的检索计划")
    return execution_terms(query)


class BrowserTiebaSearch:
    """贴吧优先原生检索；失败时保留浏览器记录，再查询限定公开索引。"""

    uses_per_request_budget = True

    def __init__(self, runtime: TiebaBrowserRuntime, public: TiebaSearchPort) -> None:
        self._runtime = runtime
        self._public = public

    def search_public(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> SearchOutcome:
        if not query.startswith("site:tieba.baidu.com/p/ "):
            return self._public.search_public(
                account_id, query=query, reason=reason, stop_event=stop_event, deadline=deadline
            )
        try:
            terms = browser_terms(query)
            payload: dict[str, Any] = {"action": "search", "terms": terms}
            if deadline is not None:
                payload["deadline_monotonic"] = deadline
            value = self._runtime.request(
                account_id,
                payload,
                stop_event=stop_event,
                deadline=deadline,
                request_budget=current_request_budget(),
                request_guard=current_request_guard(),
            )
            hits = merge_search_hits(
                tuple(
                    TiebaSearchHit.model_validate({**item, "source": "tieba_browser"})
                    for item in value.get("hits", [])
                )
            )
            scope = tuple(
                TiebaSearchScope.model_validate(item)
                for item in value.get("search_scope", [])
                if isinstance(item, dict)
            )
            kept, rejected, diagnostics = classify_hits_with_diagnostics(hits)
            status, error_code, error_message, retryable = search_status(value)
            return SearchOutcome(
                record=query_record(
                    query=query,
                    status=status,
                    evidence_count=len(hits),
                    source="tieba_browser",
                    error_code=error_code,
                    error_message=error_message,
                    retryable=retryable,
                    detail=(
                        f"固定华东交通大学吧内检索；实际吧内检索词：{terms}。"
                        f"{scope_detail(scope)}归属由读取阶段确认。"
                    ),
                ),
                hits=hits,
                candidates=kept,
                rejected=rejected,
                diagnostics=diagnostics,
                scope=scope,
                excluded_wait_ms=int(value.get("excluded_wait_ms") or 0),
            )
        except TiebaBrowserError as error:
            cancelled = error.code == "tieba_browser_cancelled"
            record = query_record(
                query=query,
                source="tieba_browser",
                evidence_count=0,
                status=ModuleQueryStatus.CANCELLED if cancelled else ModuleQueryStatus.ERROR,
                error_code=error.code,
                error_message=str(error),
                retryable=not cancelled
                and error.code
                not in {
                    "tieba_browser_auth_required",
                    "tieba_browser_auth_interrupted",
                    "tieba_browser_auth_unverified",
                    "tieba_browser_auth_status_unknown",
                    "tieba_browser_collection_restricted",
                    "tieba_browser_task_changed",
                    "tieba_browser_run_invalidated",
                    "tieba_browser_request_guard_failed",
                    "tieba_browser_work_time_exhausted",
                    "tieba_browser_request_queue_full",
                    "tieba_browser_account_deleting",
                },
            )
            if cancelled or (deadline is not None and time.monotonic() >= deadline):
                return SearchOutcome(
                    record=record,
                    hits=(),
                    candidates=(),
                    rejected=(),
                    excluded_wait_ms=error.excluded_wait_ms,
                )
            if error.code in {
                "tieba_browser_auth_required",
                "tieba_browser_auth_interrupted",
                "tieba_browser_auth_unverified",
                "tieba_browser_auth_status_unknown",
                "tieba_browser_collection_restricted",
                "tieba_browser_work_time_exhausted",
                "tieba_browser_request_queue_full",
                "tieba_browser_task_changed",
                "tieba_browser_run_invalidated",
                "tieba_browser_request_guard_failed",
                "tieba_browser_account_deleting",
            }:
                return SearchOutcome(
                    record=record,
                    hits=(),
                    candidates=(),
                    rejected=(),
                    excluded_wait_ms=error.excluded_wait_ms,
                )
            outcome = self._search_public_fallback(
                account_id,
                query=query,
                reason=reason,
                stop_event=stop_event,
                deadline=deadline,
            )
            return SearchOutcome(
                record=outcome.record,
                hits=outcome.hits,
                candidates=outcome.candidates,
                rejected=outcome.rejected,
                diagnostics=outcome.diagnostics,
                prior_records=(record,),
                excluded_wait_ms=error.excluded_wait_ms + outcome.excluded_wait_ms,
            )

    def _search_public_fallback(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        stop_event: object | None,
        deadline: float | None,
    ) -> SearchOutcome:
        if getattr(self._public, "uses_per_request_budget", False):
            return self._public.search_public(
                account_id,
                query=query,
                reason=reason,
                stop_event=stop_event,
                deadline=deadline,
            )
        budget = current_request_budget()
        if budget is None:
            return self._public.search_public(
                account_id,
                query=query,
                reason=reason,
                stop_event=stop_event,
                deadline=deadline,
            )
        key = budget.next_call_key("tieba.search.fallback")
        purpose = "tieba_search_fallback"
        if not budget.register_external(key, purpose=purpose):
            return SearchOutcome(
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.ERROR,
                    evidence_count=0,
                    error_code="run_budget_exhausted",
                    error_message="本轮运行预算已用尽，未发起新的检索请求。",
                    retryable=False,
                ),
                hits=(),
                candidates=(),
                rejected=(),
                diagnostics=HitDiagnostics(),
            )
        started = time.monotonic()
        outcome: SearchOutcome | None = None
        try:
            outcome = self._public.search_public(
                account_id,
                query=query,
                reason=reason,
                stop_event=stop_event,
                deadline=deadline,
            )
            return outcome
        finally:
            budget.release_external(
                key,
                outcome_code=(outcome.record.status.value if outcome is not None else "failed"),
                duration_ms=int((time.monotonic() - started) * 1000),
            )


class BrowserTiebaThreadReader:
    """不可变账户绑定，避免并发读取串用其他账户的浏览器。"""

    uses_per_request_budget = True

    def __init__(self, runtime: TiebaBrowserRuntime, account_id: str) -> None:
        self._runtime = runtime
        self._account_id = account_id

    def read(
        self,
        url: str,
        *,
        pages_limit: int = 2,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> TiebaReadResult:
        try:
            payload: dict[str, Any] = {
                "action": "read",
                "url": canonical_url(url),
                "pages_limit": max(1, int(pages_limit)),
                "nested_pages_limit": DEFAULT_NESTED_PAGES_LIMIT,
                "nested_parents_limit": MAX_NESTED_PARENTS_PER_PAGE,
                "scroll_rounds_limit": BROWSER_SCROLL_ROUNDS_LIMIT,
            }
            if deadline is not None:
                payload["deadline_monotonic"] = deadline
            value = self._runtime.request(
                self._account_id,
                payload,
                stop_event=stop_event,
                deadline=deadline,
                request_budget=current_request_budget(),
                request_guard=current_request_guard(),
            )
            return read_projection(url, value)
        except TiebaBrowserError as error:
            return TiebaReadResult(
                url=canonical_url(url),
                thread_id=thread_id_of(url),
                status=_read_error_status(error.code),
                error_code=error.code,
                error_message=str(error),
                excluded_wait_ms=error.excluded_wait_ms,
                retrieved_at=datetime.now(UTC),
            )


def _read_error_status(code: str) -> ReadStatus:
    if code == "tieba_browser_cancelled":
        return ReadStatus.CANCELLED
    if code == "tieba_browser_response_invalid":
        return ReadStatus.UNRECOGNIZED
    if code == "tieba_browser_timeout":
        return ReadStatus.TIMEOUT
    if code in {
        "tieba_browser_auth_required",
        "tieba_browser_auth_interrupted",
        "tieba_browser_auth_unverified",
        "tieba_browser_auth_status_unknown",
        "tieba_browser_collection_restricted",
    }:
        return ReadStatus.ACCESS_RESTRICTED
    return ReadStatus.ERROR
