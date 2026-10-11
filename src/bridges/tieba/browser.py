"""将浏览器观察转换为现有取证合同，官方查询仍走官方域名搜索。"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.browser_runtime import TiebaBrowserError, TiebaBrowserRuntime
from bridges.tieba.contracts import ReadStatus, TiebaReadResult, TiebaReply, TiebaSearchHit
from bridges.tieba.searching import (
    SearchOutcome,
    TiebaSearchPort,
    canonical_url,
    classify_hits_with_diagnostics,
    query_record,
    thread_id_of,
)


def browser_terms(query: str) -> str:
    words = query.split()
    if words[:2] != ["site:tieba.baidu.com/p/", "华东交通大学吧"]:
        raise ValueError("只接受目标贴吧的检索计划")
    return " ".join("国庆" if word == "国庆节" else word for word in words[2:6] if word != "贴吧")[
        :80
    ]


def read_projection(url: str, value: dict[str, Any]) -> TiebaReadResult:
    replies = [
        TiebaReply(
            content=str(value.get("original", ""))[:500],
            posted_at=value.get("posted_at") or None,
            is_original_poster=True,
        )
    ]
    replies.extend(TiebaReply.model_validate(item) for item in value.get("replies", [])[:39])
    replies = [reply for reply in replies if reply.content.strip()]
    return TiebaReadResult(
        url=canonical_url(url),
        thread_id=thread_id_of(url),
        status=ReadStatus.PARTIAL,
        forum_name=value.get("forum"),
        title=value.get("title"),
        replies=replies,
        pages_read=1,
        pages_limit=1,
        retrieved_at=datetime.now(UTC),
        error_code="tieba_browser_partial_replies",
        error_message="已取得当前页面加载的正文与回复；未穷尽全部回复、楼中楼或图片内容。",
    )


class BrowserTiebaSearch:
    """贴吧优先原生检索；失败时保留浏览器记录，再查询限定公开索引。"""

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
            value = self._runtime.request(
                account_id,
                {"action": "search", "terms": terms},
                stop_event=stop_event,
                deadline=deadline,
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
                retryable=not cancelled,
            )
            if cancelled or (deadline is not None and time.monotonic() >= deadline):
                return SearchOutcome(record=record, hits=(), candidates=(), rejected=())
            outcome = self._public.search_public(
                account_id, query=query, reason=reason, stop_event=stop_event, deadline=deadline
            )
            return SearchOutcome(
                record=outcome.record,
                hits=outcome.hits,
                candidates=outcome.candidates,
                rejected=outcome.rejected,
                diagnostics=outcome.diagnostics,
                prior_records=(record,),
            )


class BrowserTiebaThreadReader:
    """不可变账户绑定，避免并发读取串用其他账户的浏览器。"""

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
        del pages_limit  # 新版滚动页面不伪造分页；范围在投影里说明。
        try:
            value = self._runtime.request(
                self._account_id,
                {"action": "read", "url": canonical_url(url)},
                stop_event=stop_event,
                deadline=deadline,
            )
            return read_projection(url, value)
        except TiebaBrowserError as error:
            return TiebaReadResult(
                url=canonical_url(url),
                thread_id=thread_id_of(url),
                status=ReadStatus.CANCELLED
                if error.code == "tieba_browser_cancelled"
                else ReadStatus.ACCESS_RESTRICTED,
                error_code=error.code,
                error_message=str(error),
                retrieved_at=datetime.now(UTC),
            )
