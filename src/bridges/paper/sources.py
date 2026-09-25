"""受限外部来源适配器：arXiv 主来源 + 学术元数据补充。

统一记录合同（``bridges.contracts.modules``）在每次调用后都产出
:class:`~bridges.contracts.modules.ModuleQueryRecord`：查询词、真实取得的
证据条数、取得时间与错误分类。**公开检索只发送最小查询词**——账户、会话、
画像与附件材料一律不出现在查询里（元数据补充只发送论文标题）。

arXiv 复用既有 ``ArxivSearchService``（缓存、节流、冷却、有界重试、陈旧兜底
与脱敏审计均已存在，按交付计划「来源适配可审计后沿用」）；Crossref／OpenAlex
只作**有限尝试**：对 arXiv 已返回的候选逐条查一次发表信息（arXiv 本身不返回
期刊/会议与引用数据，这一步是必要补充；没有需要核对的候选时不发任何请求），
失败不阻断结果，只如实标注缺口。每次外发请求都留下账户归属的脱敏披露审计
（``AuditAction.ACADEMIC_METADATA_LOOKUP``，只记来源、标题指纹、状态与耗时）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from hashlib import sha256
from threading import Event
from typing import Any

import httpx

from bridges.arxiv_mcp.contracts import (
    ArxivPaperProjection,
    ArxivSearchProjection,
    ArxivSearchStatus,
)
from bridges.arxiv_mcp.service import ArxivSearchPlan, ArxivSearchService
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.observability.service import ObservabilityService

ARXIV_SOURCE = "arxiv"
CROSSREF_SOURCE = "crossref"
OPENALEX_SOURCE = "openalex"

CROSSREF_ENDPOINT = "https://api.crossref.org/works"
OPENALEX_ENDPOINT = "https://api.openalex.org/works"

#: 元数据补充的单次请求超时（秒）与逐轮查询上限（有限尝试）。
ENRICH_TIMEOUT_SECONDS = 6.0
ENRICH_MAX_LOOKUPS = 5

#: 标题核对阈值：低于该相似度视为「不是同一篇」，宁缺勿错配。
TITLE_MATCH_THRESHOLD = 0.82


@dataclass(frozen=True)
class PaperCandidate:
    """arXiv 返回的真实论文元数据（未做任何改写）。"""

    arxiv_id: str
    title: str
    authors: list[str]
    published_at: datetime
    abs_url: str
    pdf_url: str
    abstract: str
    primary_category: str | None = None


@dataclass(frozen=True)
class CandidateSearchOutcome:
    """一次 arXiv 检索的结果与统一记录。"""

    query: str
    candidates: list[PaperCandidate]
    record: ModuleQueryRecord


@dataclass(frozen=True)
class EnrichedMetadata:
    """一条学术元数据补充结果（按来源标注，绝不混为 arXiv 元数据）。"""

    source: str
    matched_title: str | None = None
    doi: str | None = None
    cited_by_count: int | None = None
    venue: str | None = None
    year: int | None = None
    is_open_access: bool | None = None
    full_text_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class EnrichOutcome:
    """一轮补充检索的结果、记录与逐篇元数据（按 arxiv_id 归档）。"""

    metadata: dict[str, EnrichedMetadata] = field(default_factory=dict)
    records: list[ModuleQueryRecord] = field(default_factory=list)


class MetadataResponseError(Exception):
    """上游元数据响应无法使用（解析失败/非预期结构）。"""


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
    ) -> CandidateSearchOutcome:
        plan = ArxivSearchPlan(
            should_search=True,
            query=query,
            reason="V2 论文模块显式检索",
            max_results=max(1, min(10, max_results)),
        )
        projection = self._service.search(
            account_id, plan, stop_event=stop_event, deadline=deadline
        )
        if projection is None:
            return CandidateSearchOutcome(
                query=query,
                candidates=[],
                record=ModuleQueryRecord(
                    source=ARXIV_SOURCE,
                    query=query,
                    status=ModuleQueryStatus.SKIPPED,
                    detail="本轮没有可发送的查询词。",
                ),
            )
        return CandidateSearchOutcome(
            query=projection.query_summary or query,
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
        return (
            ModuleQueryStatus.SUCCESS if projection.papers else ModuleQueryStatus.EMPTY
        )
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


class MetadataEnricher:
    """Crossref／OpenAlex 有限补充：核对发表信息、引用与全文可得性。

    每个来源对每篇论文最多查询一次、失败即记账不重试；标题相似度不足时视为
    没有找到（宁可缺信息，也不把别人的元数据挂到这篇论文上）。每次外发请求
    都写入账户归属的脱敏披露审计（``云端披露记录``：时间、服务、数据类别、
    状态与标题指纹，不保存标题正文）。
    """

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        observability: ObservabilityService | None = None,
        max_lookups: int = ENRICH_MAX_LOOKUPS,
        timeout: float = ENRICH_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._observability = observability
        self._max_lookups = max_lookups
        self._timeout = timeout

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def enrich(
        self,
        candidates: list[PaperCandidate],
        *,
        account_id: str,
        need_publication_info: bool,
        deadline: float | None = None,
    ) -> EnrichOutcome:
        if not need_publication_info or not candidates:
            return EnrichOutcome()
        metadata: dict[str, EnrichedMetadata] = {}
        records: list[ModuleQueryRecord] = []
        truncated = False
        for candidate in candidates[: self._max_lookups]:
            if deadline is not None and time.monotonic() >= deadline:
                truncated = True
                break
            started = time.monotonic()
            crossref = self._lookup_crossref(candidate)
            self._audit(
                account_id=account_id,
                source=CROSSREF_SOURCE,
                candidate=candidate,
                metadata=crossref,
                started=started,
            )
            records.append(_enrich_record(CROSSREF_SOURCE, candidate.title, crossref))
            if deadline is not None and time.monotonic() >= deadline:
                truncated = True
                break
            started = time.monotonic()
            openalex = self._lookup_openalex(candidate)
            self._audit(
                account_id=account_id,
                source=OPENALEX_SOURCE,
                candidate=candidate,
                metadata=openalex,
                started=started,
            )
            records.append(_enrich_record(OPENALEX_SOURCE, candidate.title, openalex))
            merged = _merge_metadata(crossref, openalex)
            if merged is not None:
                metadata[candidate.arxiv_id] = merged
        if truncated:
            records.append(
                ModuleQueryRecord(
                    source="academic_metadata",
                    query="（本轮元数据补充未逐篇完成）",
                    status=ModuleQueryStatus.SKIPPED,
                    detail="补充检索达到本轮时间预算，剩余论文未核对发表信息。",
                )
            )
        return EnrichOutcome(metadata=metadata, records=records)

    # -- 披露审计 --------------------------------------------------------

    def _audit(
        self,
        *,
        account_id: str,
        source: str,
        candidate: PaperCandidate,
        metadata: EnrichedMetadata,
        started: float,
    ) -> None:
        """记录一次外发元数据请求（账户归属 + 数据类别 + 标题指纹，不含正文）。"""
        if self._observability is None:
            return
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.ACADEMIC_METADATA_LOOKUP,
            result=_audit_result(metadata.error_code),
            reason="论文模块发表信息补充",
            details={
                "data_categories": ["public_query_terms"],
                "provider": source,
                "query_fingerprint": _title_fingerprint(candidate.title),
                "query_length": len(candidate.title),
                "arxiv_id": candidate.arxiv_id,
                "terminal": metadata.error_code or "matched",
                "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
            },
        )

    # -- 来源实现 --------------------------------------------------------

    def _lookup_crossref(self, candidate: PaperCandidate) -> EnrichedMetadata:
        params = {"query.bibliographic": candidate.title, "rows": "1"}
        payload = self._get_json(CROSSREF_ENDPOINT, params, CROSSREF_SOURCE)
        if isinstance(payload, EnrichedMetadata):
            return payload
        items = ((payload or {}).get("message") or {}).get("items") or []
        for item in items:
            title = _crossref_title(item)
            if not _titles_match(candidate.title, title):
                continue
            return EnrichedMetadata(
                source=CROSSREF_SOURCE,
                matched_title=title,
                doi=item.get("DOI"),
                cited_by_count=_as_int(item.get("is-referenced-by-count")),
                venue=_crossref_venue(item),
                year=_crossref_year(item),
            )
        return EnrichedMetadata(
            source=CROSSREF_SOURCE,
            error_code="crossref_no_match",
            error_message="Crossref 未找到标题匹配的记录。",
        )

    def _lookup_openalex(self, candidate: PaperCandidate) -> EnrichedMetadata:
        params = {"search": candidate.title, "per-page": "1"}
        payload = self._get_json(OPENALEX_ENDPOINT, params, OPENALEX_SOURCE)
        if isinstance(payload, EnrichedMetadata):
            return payload
        results = (payload or {}).get("results") or []
        for item in results:
            title = str(item.get("title") or "")
            if not _titles_match(candidate.title, title):
                continue
            best_location = item.get("best_oa_location") or {}
            return EnrichedMetadata(
                source=OPENALEX_SOURCE,
                matched_title=title,
                doi=item.get("doi"),
                cited_by_count=_as_int(item.get("cited_by_count")),
                venue=_openalex_venue(item),
                year=_as_int(item.get("publication_year")),
                is_open_access=_openalex_is_oa(item),
                full_text_url=best_location.get("pdf_url") or best_location.get("landing_page_url"),
            )
        return EnrichedMetadata(
            source=OPENALEX_SOURCE,
            error_code="openalex_no_match",
            error_message="OpenAlex 未找到标题匹配的记录。",
        )

    def _get_json(
        self, endpoint: str, params: dict[str, str], source: str
    ) -> dict[str, Any] | EnrichedMetadata:
        if self._client is None:
            return EnrichedMetadata(
                source=source,
                error_code=f"{source}_unavailable",
                error_message="元数据来源客户端未装配。",
            )
        try:
            response = self._client.get(
                endpoint,
                params=params,
                timeout=self._timeout,
                headers={"User-Agent": "BridGes/1.0 (academic metadata lookup)"},
            )
        except httpx.TimeoutException:
            return EnrichedMetadata(
                source=source,
                error_code=f"{source}_timeout",
                error_message="元数据来源超时，已跳过该来源。",
            )
        except httpx.HTTPError:
            return EnrichedMetadata(
                source=source,
                error_code=f"{source}_offline",
                error_message="元数据来源不可达，已跳过该来源。",
            )
        if response.status_code == 429:
            return EnrichedMetadata(
                source=source,
                error_code=f"{source}_rate_limit",
                error_message="元数据来源限流，已跳过该来源。",
            )
        if response.status_code >= 400:
            return EnrichedMetadata(
                source=source,
                error_code=f"{source}_http_{response.status_code}",
                error_message="元数据来源返回错误，已跳过该来源。",
            )
        try:
            payload = response.json()
        except ValueError:
            return EnrichedMetadata(
                source=source,
                error_code=f"{source}_parse",
                error_message="元数据来源返回内容无法解析，已跳过该来源。",
            )
        if not isinstance(payload, dict):
            return EnrichedMetadata(
                source=source,
                error_code=f"{source}_parse",
                error_message="元数据来源返回内容无法解析，已跳过该来源。",
            )
        return payload


def _enrich_record(
    source: str, title: str, metadata: EnrichedMetadata
) -> ModuleQueryRecord:
    found = metadata.error_code is None
    return ModuleQueryRecord(
        source=source,
        query=_minimal_query(title),
        status=ModuleQueryStatus.SUCCESS if found else ModuleQueryStatus.EMPTY,
        evidence_count=1 if found else 0,
        retrieved_at=datetime.now(UTC),
        error_code=metadata.error_code,
        error_message=metadata.error_message,
        retryable=metadata.error_code is not None
        and metadata.error_code.endswith(("_timeout", "_offline", "_rate_limit")),
    )


def _merge_metadata(
    crossref: EnrichedMetadata, openalex: EnrichedMetadata
) -> EnrichedMetadata | None:
    """合并两个来源的补充信息；都是未找到时返回 None。"""
    if crossref.error_code is not None and openalex.error_code is not None:
        return None
    return EnrichedMetadata(
        source=OPENALEX_SOURCE if openalex.error_code is None else CROSSREF_SOURCE,
        matched_title=openalex.matched_title or crossref.matched_title,
        doi=openalex.doi or crossref.doi,
        cited_by_count=(
            openalex.cited_by_count
            if openalex.cited_by_count is not None
            else crossref.cited_by_count
        ),
        venue=openalex.venue or crossref.venue,
        year=openalex.year or crossref.year,
        is_open_access=openalex.is_open_access,
        full_text_url=openalex.full_text_url,
    )


def _minimal_query(title: str) -> str:
    """元数据查询只发送论文标题（最小必要查询词，不含账户或私人上下文）。"""
    return re.sub(r"\s+", " ", title).strip()[:300]


def _title_fingerprint(title: str) -> str:
    """标题指纹（审计只记指纹，不记用户可见正文外的原文）。"""
    return sha256(" ".join(title.split()).encode("utf-8")).hexdigest()[:16]


def _audit_result(error_code: str | None) -> AuditResult:
    if error_code is None:
        return AuditResult.SUCCESS
    if error_code.endswith(("_timeout", "_offline", "_rate_limit")):
        return AuditResult.RETRYABLE_FAIL
    if error_code.endswith("_unavailable"):
        return AuditResult.BLOCKED
    return AuditResult.DEGRADED


def _titles_match(expected: str, actual: str) -> bool:
    if not actual.strip():
        return False
    return SequenceMatcher(None, _normalize_title(expected), _normalize_title(actual)).ratio() >= (
        TITLE_MATCH_THRESHOLD
    )


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _crossref_title(item: dict[str, Any]) -> str:
    titles = item.get("title") or []
    return str(titles[0]) if titles else ""


def _crossref_venue(item: dict[str, Any]) -> str | None:
    containers = item.get("container-title") or []
    return str(containers[0]) if containers else None


def _crossref_year(item: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "issued"):
        parts = ((item.get(key) or {}).get("date-parts") or [[]])[0]
        if parts:
            return _as_int(parts[0])
    return None


def _openalex_venue(item: dict[str, Any]) -> str | None:
    location = item.get("primary_location") or {}
    source = location.get("source") or {}
    name = source.get("display_name")
    return str(name) if name else None


def _openalex_is_oa(item: dict[str, Any]) -> bool | None:
    open_access = item.get("open_access")
    if isinstance(open_access, dict):
        value = open_access.get("is_oa")
        if isinstance(value, bool):
            return value
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
