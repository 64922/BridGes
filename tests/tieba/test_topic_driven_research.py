from typing import Any

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.contracts import TiebaSearchHit
from bridges.tieba.evidence_search import SEARCH_DEADLINE_SECONDS, search_candidates
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import (
    HitDiagnostics,
    SearchOutcome,
    plan_queries,
    query_record,
)


def test_synonym_searches_are_merged_and_deduplicated_by_thread_url() -> None:
    analysis = parse_tieba_request("华东交通大学吧宿舍条件")
    queries = plan_queries(analysis)
    assert len(queries) > 1
    calls: list[str] = []

    class Search:
        def search_public(self, account_id: str, *, query: str, **kwargs: object) -> SearchOutcome:
            calls.append(query)
            hits = [
                TiebaSearchHit(
                    url="https://tieba.baidu.com/p/100",
                    title="宿舍条件讨论",
                    snippet="华东交通大学吧",
                    thread_id="100",
                )
            ]
            if len(calls) == 2:
                hits.append(
                    TiebaSearchHit(
                        url="https://tieba.baidu.com/p/200",
                        title="寝室实际情况",
                        snippet="华东交通大学吧",
                        thread_id="200",
                        discovery_type="reply",
                        matched_reply="寝室晚上热水不稳定",
                    )
                )
            return SearchOutcome(
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.SUCCESS,
                    evidence_count=len(hits),
                ),
                hits=tuple(hits),
                candidates=tuple(hits),
                rejected=(),
                diagnostics=HitDiagnostics(raw_hits=len(hits), usable=len(hits)),
            )

    result = search_candidates(
        Search(),
        "account-1",
        analysis,
        stop_event=None,
        deadline_seconds=15,
    )
    assert calls == list(queries)
    assert [hit.url for hit in result.hits] == [
        "https://tieba.baidu.com/p/100",
        "https://tieba.baidu.com/p/200",
    ]


def test_search_budget_reaches_relaxed_query_after_retryable_timeout(
    monkeypatch: Any,
) -> None:
    """超时轮仍可重试时，为放宽检索保留预算。"""
    import bridges.tieba.evidence_search as evidence_search

    analysis = parse_tieba_request(
        "华东交通大学吧里关于宿舍洗澡热水、淋浴供水和收费的近期讨论有哪些？"
    )
    queries = plan_queries(analysis)
    assert len(queries) == 3

    elapsed = [0.0]
    monkeypatch.setattr(evidence_search.time, "monotonic", lambda: elapsed[0])
    calls: list[str] = []

    class Search:
        def search_public(
            self, account_id: str, *, query: str, **kwargs: object
        ) -> SearchOutcome:
            calls.append(query)
            elapsed[0] += 15.0
            if len(calls) == 1:
                status = ModuleQueryStatus.SUCCESS
                retryable = False
                candidates: tuple[TiebaSearchHit, ...] = ()
            elif len(calls) == 2:
                status = ModuleQueryStatus.TIMEOUT
                retryable = True
                candidates = ()
            else:
                status = ModuleQueryStatus.SUCCESS
                retryable = False
                candidates = (
                    TiebaSearchHit(
                        url="https://tieba.baidu.com/p/10405678421",
                        title="花椒为啥洗澡热水这么贵",
                        snippet="华东交通大学吧",
                        thread_id="10405678421",
                    ),
                )
            record = query_record(
                query=query,
                status=status,
                evidence_count=len(candidates),
                error_code="tieba_search_timeout" if retryable else None,
                error_message="请求超时。" if retryable else None,
                retryable=retryable,
            )
            return SearchOutcome(
                record=record,
                hits=candidates,
                candidates=candidates,
                rejected=(),
                diagnostics=HitDiagnostics(
                    raw_hits=len(candidates), usable=len(candidates)
                ),
            )

    result = search_candidates(
        Search(),
        "account-1",
        analysis,
        stop_event=None,
        deadline_seconds=SEARCH_DEADLINE_SECONDS,
    )

    assert calls == list(queries)
    assert len(result.hits) == 1
    assert result.hits[0].thread_id == "10405678421"
