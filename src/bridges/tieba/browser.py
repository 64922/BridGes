"""将浏览器观察转换为现有取证合同，官方查询仍走官方域名搜索。"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.browser_pagination import MAX_MERGED_REPLIES
from bridges.tieba.browser_runtime import TiebaBrowserError, TiebaBrowserRuntime
from bridges.tieba.budget import current_request_budget, current_request_guard
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaPageEvidence,
    TiebaReadResult,
    TiebaReply,
    TiebaSearchHit,
)
from bridges.tieba.searching import (
    QUERY_SCOPE_PREFIX,
    HitDiagnostics,
    SearchOutcome,
    TiebaSearchPort,
    canonical_url,
    classify_hits_with_diagnostics,
    execution_terms,
    query_record,
    thread_id_of,
)

BROWSER_SCROLL_ROUNDS_LIMIT = 8


def browser_terms(query: str) -> str:
    """把计划查询转换成浏览器实际发送的吧内检索词。"""
    if tuple(query.split()[:2]) != QUERY_SCOPE_PREFIX:
        raise ValueError("只接受目标贴吧的检索计划")
    return execution_terms(query)


def read_projection(url: str, value: dict[str, Any]) -> TiebaReadResult:
    retrieved_at = datetime.now(UTC)
    if value.get("response_valid") is False:
        return _unrecognized_read(url, value, retrieved_at)
    replies: list[TiebaReply] = []
    original = str(value.get("original", "")).strip()
    if original:
        replies.append(
            TiebaReply(
                content=original[:2000],
                floor=_optional_int(value.get("original_floor")),
                reply_id=_optional_str(value.get("origin_reply_id")),
                posted_at=value.get("posted_at") or None,
                author=_optional_str(value.get("author")),
                is_original_poster=True,
            )
        )
    for item in value.get("replies", [])[:MAX_MERGED_REPLIES]:
        replies.append(
            TiebaReply(
                content=str(item.get("content") or "")[:600],
                floor=_optional_int(item.get("floor")),
                reply_id=_optional_str(item.get("reply_id")),
                parent_reply_id=_optional_str(item.get("parent_reply_id")),
                posted_at=item.get("posted_at"),
                author=_optional_str(item.get("author")),
                is_original_poster=False,
            )
        )
    replies = [reply for reply in replies if reply.content.strip()]
    total_replies = _optional_count(value.get("total_replies"))
    loaded_reply_count = int(value.get("loaded_reply_count") or sum(
        not item.get("nested", False) for item in value.get("replies", [])
    ))
    complete = total_replies == 0 and loaded_reply_count == 0
    pages_read = int(value.get("pages_read") or 1)
    pages_limit = int(value.get("pages_limit") or 1)
    total_pages = _optional_int(value.get("total_pages"))
    page_evidence = _page_evidence(value.get("page_evidence"))
    pages_complete = _pages_complete(value, pages_read, total_pages)
    pagination_error_code = _optional_str(value.get("pagination_error_code"))
    pagination_error_message = _optional_str(value.get("pagination_error_message"))
    scroll_rounds = int(value.get("scroll_rounds") or 0)
    scroll_rounds_limit = int(value.get("scroll_rounds_limit") or 0)
    has_more_pages = total_pages is not None and pages_read < total_pages
    partial = not complete or has_more_pages or not pages_complete
    return TiebaReadResult(
        url=canonical_url(url),
        thread_id=thread_id_of(url),
        status=ReadStatus.PARTIAL if partial else ReadStatus.READ,
        forum_name=value.get("forum"),
        title=value.get("title"),
        replies=replies,
        pages_read=pages_read,
        pages_limit=pages_limit,
        pages_complete=pages_complete,
        page_evidence=page_evidence,
        scroll_rounds=scroll_rounds,
        scroll_rounds_limit=scroll_rounds_limit,
        total_pages=total_pages,
        total_replies=total_replies,
        floor_min=min((reply.floor for reply in replies if reply.floor is not None), default=None),
        floor_max=max((reply.floor for reply in replies if reply.floor is not None), default=None),
        retrieved_at=retrieved_at,
        excluded_wait_ms=int(value.get("excluded_wait_ms") or 0),
        error_code=(
            None
            if not partial
            else pagination_error_code or "tieba_browser_partial_replies"
        ),
        error_message=(
            None
            if not partial
            else _partial_message(
                value,
                loaded_reply_count=loaded_reply_count,
                total_replies=total_replies,
                pages_read=pages_read,
                total_pages=total_pages,
                page_evidence=page_evidence,
                pages_complete=pages_complete,
                pagination_error_message=pagination_error_message,
                scroll_rounds=scroll_rounds,
                scroll_rounds_limit=scroll_rounds_limit,
            )
        ),
        pagination_error_code=None if not partial else pagination_error_code,
        pagination_error_message=None if not partial else pagination_error_message,
    )


def _partial_message(
    value: dict[str, Any],
    *,
    loaded_reply_count: int,
    total_replies: int | None,
    pages_read: int,
    total_pages: int | None,
    page_evidence: list[TiebaPageEvidence],
    pages_complete: bool,
    pagination_error_message: str | None,
    scroll_rounds: int,
    scroll_rounds_limit: int,
) -> str:
    prefix = f"{pagination_error_message}；" if pagination_error_message else ""
    if value.get("source") == "structured":
        declared = total_pages if total_pages is not None else "未知"
        pages = (
            f"已成功读取 {pages_read} 页（页面声明共 {declared} 页）"
            if total_pages is not None
            else f"已成功读取 {pages_read} 页"
        )
        if page_evidence:
            read_pages = "、".join(str(item.page) for item in page_evidence)
            pages += f"；成功页：第 {read_pages} 页"
        state = "普通楼层页已全部读取" if pages_complete else "普通楼层页未读完"
        return (
            f"{prefix}{state}；{pages}；"
            f"结构化响应记录 {loaded_reply_count} 条普通楼层，"
            f"页面声明 {total_replies if total_replies is not None else '未知'} 条；"
            "未读取的后续页、楼中楼或图片内容不作推断。"
        )
    return (
        f"{prefix}已读取 {loaded_reply_count} 条普通楼层，"
        f"页面声明 {total_replies if total_replies is not None else '未知'} 条；"
        f"向下加载 {scroll_rounds}/{scroll_rounds_limit} 轮；"
        "DOM 有限滚动不能替代分页完成判定，页面路径不提供楼中楼全量状态，"
        "未穷尽全部回复、楼中楼或图片内容，不作未读取内容的推断。"
    )


def _page_evidence(value: Any) -> list[TiebaPageEvidence]:
    evidence: list[TiebaPageEvidence] = []
    if not isinstance(value, list):
        return evidence
    for raw in value:
        try:
            evidence.append(TiebaPageEvidence.model_validate(raw))
        except ValueError:
            continue
    return evidence


def _pages_complete(
    value: dict[str, Any], pages_read: int, total_pages: int | None
) -> bool:
    if "pages_complete" in value:
        return value.get("pages_complete") is True
    if total_pages is not None:
        return pages_read >= total_pages
    return (
        int(value.get("total_replies") or 0) == 0
        and int(value.get("loaded_reply_count") or 0) == 0
    )


def _unrecognized_read(
    url: str, value: dict[str, Any], retrieved_at: datetime
) -> TiebaReadResult:
    return TiebaReadResult(
        url=canonical_url(url),
        thread_id=thread_id_of(url),
        status=ReadStatus.UNRECOGNIZED,
        forum_name=value.get("forum"),
        title=value.get("title"),
        error_code="tieba_read_unrecognized",
        error_message="页面响应结构校验失败，未取得可核实的帖子内容。",
        retrieved_at=retrieved_at,
    )


def _optional_int(value: Any) -> int | None:
    number = _optional_count(value)
    return number if number is not None and number > 0 else None


def _optional_count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _optional_str(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


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
            hits = tuple(
                TiebaSearchHit.model_validate({**item, "source": "tieba_browser"})
                for item in value.get("hits", [])
            )
            kept, rejected, diagnostics = classify_hits_with_diagnostics(hits)
            return SearchOutcome(
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.SUCCESS,
                    evidence_count=len(hits),
                    source="tieba_browser",
                    detail=f"固定华东交通大学吧内检索；实际吧内检索词：{terms}。归属由读取阶段确认。",
                ),
                hits=hits,
                candidates=kept,
                rejected=rejected,
                diagnostics=diagnostics,
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
                retryable=not cancelled and error.code not in {
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
                outcome_code=(
                    outcome.record.status.value if outcome is not None else "failed"
                ),
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
