from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.contracts import TiebaSearchHit
from bridges.tieba.evidence import search_candidates
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
