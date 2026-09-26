"""``career.plan`` 的检索执行：只把招聘页链接当作候选。

查询词由模块自己拼装并逐字发送（只含用户原话岗位词、城市、阶段与固定字面量），
经允许的搜索服务走同一套审计、缓存与预算路径。只有确实指向招聘页的链接才成为
候选，其余结果（公司介绍、新闻、问答等）如实丢弃并在记录里写明丢了多少条。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from bridges.career_plan.lexicon import SOURCE_LABELS, classify_source
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus


@dataclass(frozen=True)
class CareerSearchHit:
    """一条已确认指向招聘页的候选（尚未读取页面）。"""

    url: str
    title: str
    snippet: str
    source: str


@dataclass(frozen=True)
class CareerSearchOutcome:
    """一次检索调用的结果：统一查询记录与已分类的候选。"""

    record: ModuleQueryRecord
    hits: tuple[CareerSearchHit, ...]


class CareerSearchPort(Protocol):
    """允许的搜索服务的窄接口（由 ``WebSearchService`` 适配）。"""

    def search_public(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        source: str,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> CareerSearchOutcome: ...


def classify_hits(
    results: list[tuple[str, str, str]], *, source: str
) -> tuple[tuple[CareerSearchHit, ...], int]:
    """按来源挑选招聘页链接；返回 ``(候选, 被丢弃的非招聘页条数)``。"""
    kept: list[CareerSearchHit] = []
    dropped = 0
    seen: set[str] = set()
    for url, title, snippet in results:
        if not url or url in seen:
            continue
        seen.add(url)
        if classify_source(url) != source:
            dropped += 1
            continue
        kept.append(
            CareerSearchHit(
                url=url, title=title, snippet=snippet or "", source=source
            )
        )
    return tuple(kept), dropped


def query_record(
    *,
    query: str,
    status: ModuleQueryStatus,
    evidence_count: int,
    retrieved_at: datetime | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    retryable: bool = False,
    detail: str | None = None,
) -> ModuleQueryRecord:
    """统一查询记录（沿用 V2 Issue 11 建立的证据合同）。"""
    return ModuleQueryRecord(
        source="tavily",
        query=query,
        status=status,
        evidence_count=evidence_count,
        retrieved_at=retrieved_at or datetime.now(UTC),
        error_code=error_code,
        error_message=error_message,
        retryable=retryable,
        detail=detail,
    )


class WebSearchServiceAdapter:
    """把允许的搜索服务适配成职业规划模块的检索边界。"""

    def __init__(self, service: object, *, max_results: int = 8) -> None:
        self._service = service
        self._max_results = max_results

    def search_public(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        source: str,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> CareerSearchOutcome:
        from bridges.web_search.service import SearchPlan  # noqa: PLC0415

        label = SOURCE_LABELS.get(source, source)
        plan = SearchPlan(
            should_search=True,
            query=query,
            reason=reason,
            queries=(query,),
            max_results=self._max_results,
        )
        projection = self._service.search(  # type: ignore[attr-defined]
            account_id, plan, stop_event=stop_event, deadline=deadline
        )
        retrieved_at = datetime.now(UTC)
        if projection is None:
            return CareerSearchOutcome(
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.ERROR,
                    evidence_count=0,
                    retrieved_at=retrieved_at,
                    error_code="career_search_failed",
                    error_message=f"{label}检索没有形成结果，请稍后重试。",
                    retryable=True,
                ),
                hits=(),
            )
        # 搜索投影的实际字段是 snippet／content_summary（页面正文摘要），
        # 没有笼统的 content；摘要只用于候选说明，绝不当作岗位页正文。
        results = [
            (item.url, item.title, item.snippet or item.content_summary)
            for item in projection.results
        ]
        hits, dropped = classify_hits(results, source=source)
        detail_parts = []
        if dropped:
            detail_parts.append(f"另有 {dropped} 条结果不是招聘页，未作为候选")
        return CareerSearchOutcome(
            record=query_record(
                query=query,
                status=_record_status(projection.status, has_hits=bool(hits)),
                evidence_count=len(hits),
                retrieved_at=projection.searched_at or retrieved_at,
                error_code=projection.error_code,
                error_message=projection.error_message,
                retryable=projection.can_retry,
                detail="；".join(detail_parts) or None,
            ),
            hits=hits,
        )


def _record_status(status: object, *, has_hits: bool) -> ModuleQueryStatus:
    """把搜索服务状态映射成统一查询记录状态（页面抓取失败不算检索失败）。"""
    name = getattr(status, "value", str(status))
    if name == "cancelled":
        return ModuleQueryStatus.CANCELLED
    if name == "empty":
        return ModuleQueryStatus.EMPTY
    if name in {
        "success",
        "partial",
        "source_conflict",
        "fetch_error",
        "evidence_insufficient",
    }:
        return ModuleQueryStatus.SUCCESS if has_hits else ModuleQueryStatus.EMPTY
    return ModuleQueryStatus.ERROR
