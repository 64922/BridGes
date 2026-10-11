"""arXiv 来源适配及论文来源入口。"""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Event

from bridges.arxiv_mcp.contracts import (
    ArxivPaperProjection,
    ArxivSearchProjection,
    ArxivSearchStatus,
)
from bridges.arxiv_mcp.service import ArxivSearchPlan, ArxivSearchService
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.paper.enrichment import MetadataEnricher as MetadataEnricher
from bridges.paper.source_types import ARXIV_SOURCE as ARXIV_SOURCE
from bridges.paper.source_types import CROSSREF_ENDPOINT as CROSSREF_ENDPOINT
from bridges.paper.source_types import CROSSREF_SOURCE as CROSSREF_SOURCE
from bridges.paper.source_types import ENRICH_MAX_LOOKUPS as ENRICH_MAX_LOOKUPS
from bridges.paper.source_types import ENRICH_TIMEOUT_SECONDS as ENRICH_TIMEOUT_SECONDS
from bridges.paper.source_types import OPENALEX_ENDPOINT as OPENALEX_ENDPOINT
from bridges.paper.source_types import OPENALEX_SOURCE as OPENALEX_SOURCE
from bridges.paper.source_types import TITLE_MATCH_THRESHOLD as TITLE_MATCH_THRESHOLD
from bridges.paper.source_types import CandidateSearchOutcome as CandidateSearchOutcome
from bridges.paper.source_types import EnrichedMetadata as EnrichedMetadata
from bridges.paper.source_types import EnrichOutcome as EnrichOutcome
from bridges.paper.source_types import ExternalCallBudget as ExternalCallBudget
from bridges.paper.source_types import MetadataResponseError as MetadataResponseError
from bridges.paper.source_types import PaperCandidate as PaperCandidate


class ArxivPaperSource:
    """arXiv 主来源：把模块查询计划转换为真实候选与统一记录。"""

    def __init__(self, service: ArxivSearchService) -> None:
        self._service = service

    def search(
        self,
        account_id: str,
        query: str,
        *,
        max_results: int,
        stop_event: Event | None = None,
        deadline: float | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> CandidateSearchOutcome:
        actual_query = query
        if year_from is not None or year_to is not None:
            start = f"{year_from or 1000}01010000"
            end = f"{year_to or 2999}12312359"
            actual_query = f"({query}) AND submittedDate:[{start} TO {end}]"
        plan = ArxivSearchPlan(
            should_search=True,
            query=actual_query,
            reason="V2 论文模块显式检索",
            max_results=max(1, min(10, max_results)),
        )
        projection = self._service.search(
            account_id, plan, stop_event=stop_event, deadline=deadline
        )
        if projection is None:
            return CandidateSearchOutcome(
                query=actual_query,
                candidates=[],
                record=ModuleQueryRecord(
                    source=ARXIV_SOURCE,
                    query=actual_query,
                    status=ModuleQueryStatus.SKIPPED,
                    detail="本轮没有可发送的查询词。",
                ),
            )
        return CandidateSearchOutcome(
            query=projection.query_summary or actual_query,
            candidates=[_candidate_from_projection(paper) for paper in projection.papers],
            record=_record_from_projection(projection),
        )


def _candidate_from_projection(paper: ArxivPaperProjection) -> PaperCandidate:
    return PaperCandidate(
        arxiv_id=paper.arxiv_id,
        title=paper.title,
        authors=list(paper.authors),
        published_at=paper.published_at,
        abs_url=paper.abs_url,
        pdf_url=paper.pdf_url,
        abstract=paper.abstract,
        primary_category=paper.primary_category,
        source=ARXIV_SOURCE,
        source_id=paper.arxiv_id,
        sources=(ARXIV_SOURCE,),
        source_ids=(f"arxiv:{paper.arxiv_id}",),
    )


def _record_from_projection(projection: ArxivSearchProjection) -> ModuleQueryRecord:
    status = _query_status(projection)
    return ModuleQueryRecord(
        source=ARXIV_SOURCE,
        query=projection.query_summary,
        status=status,
        evidence_count=len(projection.papers),
        retrieved_at=projection.searched_at or datetime.now(UTC),
        error_code=projection.error_code,
        error_message=projection.error_message,
        retryable=projection.can_retry,
        detail=_record_detail(projection),
    )


def _query_status(projection: ArxivSearchProjection) -> ModuleQueryStatus:
    if projection.status in {ArxivSearchStatus.SUCCESS, ArxivSearchStatus.RECOVERY}:
        return ModuleQueryStatus.SUCCESS if projection.papers else ModuleQueryStatus.EMPTY
    if projection.status is ArxivSearchStatus.EMPTY:
        return ModuleQueryStatus.EMPTY
    if projection.status is ArxivSearchStatus.CANCELLED:
        return ModuleQueryStatus.CANCELLED
    if projection.status is ArxivSearchStatus.PERMISSION:
        return ModuleQueryStatus.ERROR
    if projection.error_code == "arxiv_timeout":
        return ModuleQueryStatus.TIMEOUT
    if projection.error_code == "arxiv_rate_limit":
        return ModuleQueryStatus.RATE_LIMITED
    return ModuleQueryStatus.ERROR


def _record_detail(projection: ArxivSearchProjection) -> str | None:
    notes: list[str] = []
    if projection.cache_hit:
        notes.append("命中未过期的进程内缓存")
    if projection.stale:
        notes.append("本次上游调用失败，回退到过期缓存结果（可能不是最新）")
    if projection.attempt_count:
        notes.append(f"上游请求 {projection.attempt_count} 次")
    if projection.retry_after_seconds:
        notes.append(f"上游冷却剩余 {projection.retry_after_seconds} 秒")
    return "；".join(notes) if notes else None
