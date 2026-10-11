"""OpenAlex 与 Crossref 的论文发现和轻量引用扩展。"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Event
from typing import Any

import httpx

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.observability.service import ObservabilityService
from bridges.paper.contracts import PaperCitationEdge
from bridges.paper.discovery_parsing import (
    _abstract_from_index,
    _arxiv_id,
    _canonical_openalex_id,
    _crossref_year,
    _date_from_crossref,
    _date_or_year,
    _doi,
    _fingerprint,
    _first_title,
    _integer,
    _openalex_key,
    _openalex_venue,
    _strip_markup,
)
from bridges.paper.sources import (
    CROSSREF_ENDPOINT,
    CROSSREF_SOURCE,
    OPENALEX_ENDPOINT,
    OPENALEX_SOURCE,
    CandidateSearchOutcome,
    PaperCandidate,
)

_TIMEOUT_SECONDS = 8.0
_MAX_RESULTS = 10
_WORK_FIELDS = (
    "id,ids,doi,title,publication_year,publication_date,authorships,abstract_inverted_index,"
    "best_oa_location,primary_location,cited_by_count,open_access,referenced_works"
)
_CROSSREF_FIELDS = (
    "DOI,title,author,published,published-print,issued,container-title,type,URL,"
    "abstract,link,is-referenced-by-count"
)


@dataclass(frozen=True)
class CitationExpansionOutcome:
    candidates: list[PaperCandidate]
    edges: list[PaperCitationEdge]
    records: list[ModuleQueryRecord]


class AcademicPaperSource:
    """一个公开学术检索来源；查询只包含意图分析得到的检索词。"""

    def __init__(
        self,
        source: str,
        *,
        client: httpx.Client | None = None,
        observability: ObservabilityService | None = None,
        timeout: float = _TIMEOUT_SECONDS,
    ) -> None:
        if source not in {OPENALEX_SOURCE, CROSSREF_SOURCE}:
            raise ValueError(f"unsupported academic source: {source}")
        self.source = source
        self.endpoint = OPENALEX_ENDPOINT if source == OPENALEX_SOURCE else CROSSREF_ENDPOINT
        self._client = client or httpx.Client()
        self._owns_client = client is None
        self._observability = observability
        self._timeout = timeout

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

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
        started = time.monotonic()
        limit = max(1, min(_MAX_RESULTS, max_results))
        actual_query = " ".join(query.split())[:300]
        if stop_event is not None and stop_event.is_set():
            outcome = self._error(actual_query, ModuleQueryStatus.CANCELLED, "cancelled")
        else:
            params = self._search_params(actual_query, limit, year_from, year_to)
            payload, error = self._get_json(params, deadline=deadline)
            if error is not None or payload is None:
                if error is None:
                    error = (
                        ModuleQueryStatus.ERROR,
                        f"{self.source}_parse",
                        "学术来源返回结构无效。",
                        False,
                    )
                outcome = self._error(actual_query, *error)
            else:
                items = self._items(payload)
                if items is None:
                    outcome = self._error(
                        actual_query,
                        ModuleQueryStatus.ERROR,
                        f"{self.source}_parse",
                        "学术来源返回结构无效。",
                    )
                else:
                    candidates = [
                        candidate for item in items if (candidate := self._candidate(item))
                    ]
                    record = ModuleQueryRecord(
                        source=self.source,
                        query=actual_query,
                        status=ModuleQueryStatus.SUCCESS if candidates else ModuleQueryStatus.EMPTY,
                        evidence_count=len(candidates),
                        retrieved_at=datetime.now(UTC),
                        detail=f"返回 {len(candidates)} 条记录；单次上限 {limit}。",
                    )
                    outcome = CandidateSearchOutcome(actual_query, candidates, record)
        self._audit(account_id, actual_query, outcome.record, started)
        return outcome

    def _search_params(
        self, query: str, limit: int, year_from: int | None, year_to: int | None
    ) -> dict[str, str]:
        if self.source == OPENALEX_SOURCE:
            params = {"search": query, "per-page": str(limit), "select": _WORK_FIELDS}
            filters: list[str] = []
            if year_from is not None:
                filters.append(f"from_publication_date:{year_from}-01-01")
            if year_to is not None:
                filters.append(f"to_publication_date:{year_to}-12-31")
            if filters:
                params["filter"] = ",".join(filters)
            return params
        params = {
            "query.bibliographic": query,
            "rows": str(limit),
            "select": _CROSSREF_FIELDS,
        }
        filters = []
        if year_from is not None:
            filters.append(f"from-pub-date:{year_from}-01-01")
        if year_to is not None:
            filters.append(f"until-pub-date:{year_to}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        return params

    def _items(self, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        if self.source == OPENALEX_SOURCE:
            items = payload.get("results")
        else:
            message = payload.get("message")
            items = message.get("items") if isinstance(message, dict) else None
        if not isinstance(items, list):
            return None
        return [item for item in items if isinstance(item, dict)]

    def _candidate(self, item: dict[str, Any]) -> PaperCandidate | None:
        title = (
            _first_title(item.get("title"))
            if self.source == OPENALEX_SOURCE
            else _first_title(item.get("title"))
        )
        if not title:
            return None
        if self.source == OPENALEX_SOURCE:
            identifier = str(item.get("id") or "")
            source_id = _openalex_key(identifier)
            year = _integer(item.get("publication_year"))
            published_at = _date_or_year(item.get("publication_date"), year)
            authors = [
                str((entry.get("author") or {}).get("display_name") or "")
                for entry in item.get("authorships") or ()
                if isinstance(entry, dict)
            ]
            location = item.get("best_oa_location") or item.get("primary_location") or {}
            landing = str(location.get("landing_page_url") or identifier)
            pdf_url = str(location.get("pdf_url") or "")
            open_access = (item.get("open_access") or {}).get("is_oa")
            return PaperCandidate(
                arxiv_id=_arxiv_id(item.get("ids") or {}),
                title=title,
                authors=[author for author in authors if author],
                published_at=published_at,
                abs_url=landing,
                pdf_url=pdf_url,
                abstract=_abstract_from_index(item.get("abstract_inverted_index")),
                source=OPENALEX_SOURCE,
                source_id=source_id,
                doi=_doi(item.get("doi")),
                openalex_id=identifier,
                cited_by_count=_integer(item.get("cited_by_count")),
                venue=_openalex_venue(item),
                full_text_url=pdf_url or None,
                is_open_access=bool(open_access) if open_access is not None else None,
                sources=(OPENALEX_SOURCE,),
                source_ids=(f"openalex:{source_id}",),
                referenced_works=tuple(str(value) for value in item.get("referenced_works") or ()),
            )
        doi = str(item.get("DOI") or "") or None
        year = _crossref_year(item)
        date = _date_from_crossref(item, year)
        authors = [
            " ".join(
                filter(None, (str(author.get("given") or ""), str(author.get("family") or "")))
            )
            for author in item.get("author") or ()
            if isinstance(author, dict)
        ]
        links = item.get("link") or ()
        pdf_url = next(
            (
                str(link.get("URL"))
                for link in links
                if isinstance(link, dict) and "pdf" in str(link.get("content-type") or "").lower()
            ),
            "",
        )
        doi_url = f"https://doi.org/{doi}" if doi else str(item.get("URL") or "")
        return PaperCandidate(
            arxiv_id=None,
            title=title,
            authors=[author for author in authors if author],
            published_at=date,
            abs_url=doi_url,
            pdf_url=pdf_url,
            abstract=_strip_markup(str(item.get("abstract") or "")),
            source=CROSSREF_SOURCE,
            source_id=doi or str(item.get("URL") or title),
            doi=doi,
            cited_by_count=_integer(item.get("is-referenced-by-count")),
            venue=_first_title(item.get("container-title")) or None,
            full_text_url=pdf_url or None,
            sources=(CROSSREF_SOURCE,),
            source_ids=((f"doi:{doi}",) if doi else (f"crossref:{item.get('URL') or title}",)),
        )

    def expand_citations(
        self,
        account_id: str,
        seeds: list[PaperCandidate],
        *,
        max_results: int = 8,
        stop_event: Event | None = None,
        deadline: float | None = None,
        budget: Any | None = None,
    ) -> CitationExpansionOutcome:
        if self.source != OPENALEX_SOURCE:
            return CitationExpansionOutcome([], [], [])
        edges: list[PaperCitationEdge] = []
        records: list[ModuleQueryRecord] = []
        candidates_by_id: dict[str, PaperCandidate] = {}
        seen_edges: set[tuple[str, str]] = set()
        for seed in seeds[:3]:
            work_id = _openalex_key(seed.openalex_id or "")
            if not work_id or (stop_event is not None and stop_event.is_set()):
                continue
            query = f"cites:{work_id}"
            call_key = f"paper.citation:{work_id}"
            if budget is not None and not budget.register_external(
                call_key, purpose="academic_citation_search"
            ):
                records.append(
                    ModuleQueryRecord(
                        source=OPENALEX_SOURCE,
                        query=query,
                        status=ModuleQueryStatus.SKIPPED,
                        detail="共享运行预算不足，未执行这条引用扩展。",
                    )
                )
                continue
            started = time.monotonic()
            outcome_code = "error"
            try:
                payload, error = self._get_json(
                    {
                        "filter": query,
                        "per-page": str(max(1, min(8, max_results))),
                        "select": _WORK_FIELDS,
                    },
                    deadline=deadline,
                )
                works = self._items(payload) if payload is not None else None
                if error is None and works is None:
                    error = (
                        ModuleQueryStatus.ERROR,
                        f"{self.source}_parse",
                        "学术来源返回结构无效。",
                        False,
                    )
                outcome_code = error[0].value if error is not None else "success"
            finally:
                if budget is not None:
                    budget.release_external(call_key, outcome_code=outcome_code)
            if error is not None:
                outcome = self._error(query, *error)
                outcome_code = outcome.record.status.value
                self._audit(account_id, query, outcome.record, started)
                records.append(outcome.record)
                continue
            works = works or []
            seed_id = _canonical_openalex_id(seed.openalex_id or work_id)
            for work in works:
                candidate = self._candidate(work)
                if candidate is None:
                    continue
                cited = {_canonical_openalex_id(str(ref)) for ref in candidate.referenced_works}
                if seed_id not in cited:
                    continue
                candidate = replace(
                    candidate,
                    discovery_paths=(f"citation:{seed.candidate_id}",),
                )
                candidate_id = candidate.candidate_id
                previous = candidates_by_id.get(candidate_id)
                if previous is not None:
                    candidate = replace(
                        previous,
                        discovery_paths=tuple(
                            dict.fromkeys((*previous.discovery_paths, *candidate.discovery_paths))
                        ),
                    )
                candidates_by_id[candidate_id] = candidate
                edge_key = (candidate_id, seed.candidate_id)
                if edge_key not in seen_edges:
                    edges.append(
                        PaperCitationEdge(
                            citing_candidate_id=candidate_id,
                            cited_candidate_id=seed.candidate_id,
                            citing_title=candidate.title,
                            cited_title=seed.title,
                        )
                    )
                    seen_edges.add(edge_key)
            edge_count = sum(1 for edge in edges if edge.cited_candidate_id == seed.candidate_id)
            record = ModuleQueryRecord(
                source=OPENALEX_SOURCE,
                query=query,
                status=ModuleQueryStatus.SUCCESS if works else ModuleQueryStatus.EMPTY,
                evidence_count=edge_count,
                retrieved_at=datetime.now(UTC),
                detail=f"引用扩展取得 {len(works)} 条作品记录，筛出 {edge_count} 条实际引用关系。",
            )
            self._audit(account_id, query, record, started)
            records.append(record)
        return CitationExpansionOutcome(list(candidates_by_id.values()), edges, records)

    def _get_json(
        self, params: dict[str, str], *, deadline: float | None
    ) -> tuple[dict[str, Any] | None, tuple[ModuleQueryStatus, str, str, bool] | None]:
        timeout = self._timeout
        if deadline is not None:
            timeout = min(timeout, max(0.001, deadline - time.monotonic()))
        try:
            response = self._client.get(
                self.endpoint,
                params=params,
                timeout=timeout,
                headers={"User-Agent": "BridGes/1.0 (academic paper discovery)"},
            )
        except httpx.TimeoutException:
            return None, (
                ModuleQueryStatus.TIMEOUT,
                f"{self.source}_timeout",
                "学术来源检索超时。",
                True,
            )
        except httpx.HTTPError:
            return None, (
                ModuleQueryStatus.ERROR,
                f"{self.source}_offline",
                "学术来源暂时不可达。",
                True,
            )
        if response.status_code == 429:
            return None, (
                ModuleQueryStatus.RATE_LIMITED,
                f"{self.source}_rate_limit",
                "学术来源限流。",
                True,
            )
        if response.status_code >= 400:
            return None, (
                ModuleQueryStatus.ERROR,
                f"{self.source}_http_{response.status_code}",
                "学术来源返回错误。",
                response.status_code >= 500,
            )
        try:
            payload = response.json()
        except ValueError:
            return None, (
                ModuleQueryStatus.ERROR,
                f"{self.source}_parse",
                "学术来源返回内容无法解析。",
                False,
            )
        if not isinstance(payload, dict):
            return None, (
                ModuleQueryStatus.ERROR,
                f"{self.source}_parse",
                "学术来源返回结构无效。",
                False,
            )
        return payload, None

    def _error(
        self,
        query: str,
        status: ModuleQueryStatus,
        code: str,
        message: str | None = None,
        retryable: bool = False,
    ) -> CandidateSearchOutcome:
        return CandidateSearchOutcome(
            query,
            [],
            ModuleQueryRecord(
                source=self.source,
                query=query,
                status=status,
                retrieved_at=datetime.now(UTC),
                error_code=code,
                error_message=message or "本轮未取得学术来源结果。",
                retryable=retryable,
            ),
        )

    def _audit(
        self,
        account_id: str,
        query: str,
        record: ModuleQueryRecord,
        started: float,
    ) -> None:
        if self._observability is None:
            return
        result = (
            AuditResult.SUCCESS
            if record.status in {ModuleQueryStatus.SUCCESS, ModuleQueryStatus.EMPTY}
            else AuditResult.RETRYABLE_FAIL
            if record.retryable
            else AuditResult.BLOCKED
        )
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.ACADEMIC_SEARCH,
            result=result,
            reason="论文模块公开学术检索",
            details={
                "data_categories": ["public_query_terms"],
                "provider": self.source,
                "query_fingerprint": _fingerprint(query),
                "query_length": len(query),
                "terminal": record.error_code or record.status.value,
                "evidence_count": record.evidence_count,
                "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
            },
        )


class OpenAlexPaperSource(AcademicPaperSource):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(OPENALEX_SOURCE, **kwargs)


class CrossrefPaperSource(AcademicPaperSource):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(CROSSREF_SOURCE, **kwargs)
