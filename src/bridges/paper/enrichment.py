"""有限学术元数据补充与来源核对。"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from difflib import SequenceMatcher
from hashlib import sha256
from threading import Event
from typing import Any

import httpx

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.observability.service import ObservabilityService
from bridges.paper.source_types import (
    CROSSREF_ENDPOINT,
    CROSSREF_SOURCE,
    ENRICH_MAX_LOOKUPS,
    ENRICH_TIMEOUT_SECONDS,
    OPENALEX_ENDPOINT,
    OPENALEX_SOURCE,
    TITLE_MATCH_THRESHOLD,
    EnrichedMetadata,
    EnrichOutcome,
    ExternalCallBudget,
    PaperCandidate,
)


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
        budget: ExternalCallBudget | None = None,
        stop_event: Event | None = None,
        sources: Sequence[str] | None = None,
    ) -> EnrichOutcome:
        if not need_publication_info or not candidates:
            return EnrichOutcome()
        wanted = (
            {CROSSREF_SOURCE, OPENALEX_SOURCE}
            if sources is None
            else {item for item in sources if item in {CROSSREF_SOURCE, OPENALEX_SOURCE}}
        )
        if not wanted:
            return EnrichOutcome()
        metadata: dict[str, EnrichedMetadata] = {}
        records: list[ModuleQueryRecord] = []
        truncated = False
        for candidate in candidates[: self._max_lookups]:
            if (stop_event is not None and stop_event.is_set()) or (
                deadline is not None and time.monotonic() >= deadline
            ):
                truncated = True
                break
            crossref: EnrichedMetadata | None = None
            if CROSSREF_SOURCE in wanted:
                started = time.monotonic()
                crossref = self._budgeted_lookup(candidate, CROSSREF_SOURCE, budget, deadline)
                if crossref is None:
                    truncated = True
                    break
                self._audit(
                    account_id=account_id,
                    source=CROSSREF_SOURCE,
                    candidate=candidate,
                    metadata=crossref,
                    started=started,
                )
                records.append(_enrich_record(CROSSREF_SOURCE, candidate.title, crossref))
                if (stop_event is not None and stop_event.is_set()) or (
                    deadline is not None and time.monotonic() >= deadline
                ):
                    if crossref.error_code is None:
                        metadata[_candidate_key(candidate)] = crossref
                    truncated = True
                    break
            openalex: EnrichedMetadata | None = None
            if OPENALEX_SOURCE in wanted:
                started = time.monotonic()
                openalex = self._budgeted_lookup(candidate, OPENALEX_SOURCE, budget, deadline)
                if openalex is None:
                    if crossref is not None and crossref.error_code is None:
                        metadata[_candidate_key(candidate)] = crossref
                    truncated = True
                    break
                self._audit(
                    account_id=account_id,
                    source=OPENALEX_SOURCE,
                    candidate=candidate,
                    metadata=openalex,
                    started=started,
                )
                records.append(_enrich_record(OPENALEX_SOURCE, candidate.title, openalex))
            merged = _merge_selected(crossref, openalex)
            if merged is not None:
                metadata[_candidate_key(candidate)] = merged
        if truncated:
            records.append(
                ModuleQueryRecord(
                    source="academic_metadata",
                    query="（本轮元数据补充未逐篇完成）",
                    status=ModuleQueryStatus.SKIPPED,
                    detail="补充检索因预算、截止或停止未完成，剩余论文未核对发表信息。",
                )
            )
        return EnrichOutcome(metadata=metadata, records=records)

    def _budgeted_lookup(
        self,
        candidate: PaperCandidate,
        source: str,
        budget: ExternalCallBudget | None,
        deadline: float | None,
    ) -> EnrichedMetadata | None:
        key = f"paper.enrich:{source}:{_candidate_key(candidate)}"
        if budget is not None and not budget.register_external(key, purpose="academic_metadata"):
            return None
        outcome = "exception"
        timeout = self._timeout
        if deadline is not None:
            timeout = min(timeout, max(0.001, deadline - time.monotonic()))
        try:
            result = (
                self._lookup_crossref(candidate, timeout=timeout)
                if source == CROSSREF_SOURCE
                else self._lookup_openalex(candidate, timeout=timeout)
            )
            outcome = result.error_code or "matched"
            return result
        finally:
            if budget is not None:
                budget.release_external(key, outcome_code=outcome)

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

    def _lookup_crossref(
        self, candidate: PaperCandidate, *, timeout: float | None = None
    ) -> EnrichedMetadata:
        params = {"query.bibliographic": candidate.title, "rows": "1"}
        payload = self._get_json(CROSSREF_ENDPOINT, params, CROSSREF_SOURCE, timeout=timeout)
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

    def _lookup_openalex(
        self, candidate: PaperCandidate, *, timeout: float | None = None
    ) -> EnrichedMetadata:
        params = {"search": candidate.title, "per-page": "1"}
        payload = self._get_json(OPENALEX_ENDPOINT, params, OPENALEX_SOURCE, timeout=timeout)
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
        self,
        endpoint: str,
        params: dict[str, str],
        source: str,
        *,
        timeout: float | None = None,
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
                timeout=timeout if timeout is not None else self._timeout,
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


def _enrich_record(source: str, title: str, metadata: EnrichedMetadata) -> ModuleQueryRecord:
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


def _merge_selected(
    crossref: EnrichedMetadata | None, openalex: EnrichedMetadata | None
) -> EnrichedMetadata | None:
    """合并实际调用的来源结果；只有一个来源时直接采用（未找到则无）。"""
    if crossref is not None and openalex is not None:
        return _merge_metadata(crossref, openalex)
    selected = crossref if crossref is not None else openalex
    if selected is None or selected.error_code is not None:
        return None
    return selected


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


def _candidate_key(candidate: PaperCandidate) -> str:
    return candidate.arxiv_id or candidate.candidate_id


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
