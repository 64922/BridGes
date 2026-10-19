"""工单 05：搜索分页元数据、发现路径合并与跨轮次保留。"""

from __future__ import annotations

import pytest

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.browser_search import merge_search_hit_dicts
from bridges.tieba.browser_structured import StructuredResponseError, parse_search_page
from bridges.tieba.evidence_search import search_candidates
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import (
    HitDiagnostics,
    SearchOutcome,
    classify_hits_with_diagnostics,
    query_record,
)
from tests.tieba.search_pagination_support import (
    URL_A,
    _discovery,
    _hit,
    _post,
    _search_payload,
)


def test_parse_search_page_keeps_page_has_more_and_reply_locator() -> None:
    parsed = parse_search_page(
        _search_payload(page=2, has_more=1, posts=[_post()]),
        discovery_type="reply",
    )

    assert parsed.page == 2
    assert parsed.has_more is True
    hit = parsed.hits[0]
    assert hit["search_page"] == 2
    assert hit["discovery_type"] == "reply"
    assert hit["matched_reply"] == "有女生专区，空调看老板心情开。"
    assert hit["matched_reply_id"] == "153449627007"
    assert hit["matched_author"] == "吧友甲"


def test_parse_search_page_defaults_missing_pagination_metadata() -> None:
    payload = _search_payload(posts=[_post(cid="0")])
    payload["data"].pop("current_page")
    payload["data"].pop("has_more")

    parsed = parse_search_page(payload, discovery_type="thread")

    assert parsed.page == 1
    assert parsed.has_more is False
    assert parsed.hits[0]["matched_reply_id"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"no": 0, "data": {"post_list": [{"title": "缺少 tid"}]}},
        {"no": 0, "data": {"post_list": "not-a-list"}},
    ],
)
def test_parse_search_page_rejects_unusable_payloads(payload: object) -> None:
    with pytest.raises(StructuredResponseError):
        parse_search_page(payload, discovery_type="thread")


def test_merge_search_hit_dicts_keeps_thread_and_reply_paths() -> None:
    thread = {
        "url": URL_A,
        "title": "南区健身房",
        "snippet": "华东交通大学吧",
        "discovery_type": "thread",
        "query": "空调",
        "search_page": 1,
        "matched_reply": None,
        "matched_reply_id": None,
        "matched_author": None,
    }
    reply = {
        "url": URL_A,
        "title": "回复：南区健身房",
        "snippet": "华东交通大学吧\n有女生专区，空调看老板心情开。",
        "discovery_type": "reply",
        "query": "空调",
        "search_page": 2,
        "matched_reply": "有女生专区，空调看老板心情开。",
        "matched_reply_id": "153449627007",
        "matched_author": "吧友甲",
    }

    merged = merge_search_hit_dicts([thread, reply])

    assert len(merged) == 1
    assert merged[0]["url"] == URL_A
    assert merged[0]["discovery_type"] == "reply"
    assert merged[0]["matched_reply_id"] == "153449627007"
    assert len(merged[0]["discoveries"]) == 2
    assert {item["discovery_type"] for item in merged[0]["discoveries"]} == {
        "thread",
        "reply",
    }
    assert {item["search_page"] for item in merged[0]["discoveries"]} == {1, 2}


def test_classify_hits_merges_duplicate_url_instead_of_dropping_reply() -> None:
    thread = _hit()
    reply = _hit(
        discovery_type="reply",
        page=2,
        matched_reply="有女生专区，空调看老板心情开。",
        matched_reply_id="153449627007",
    )

    kept, rejected, diagnostics = classify_hits_with_diagnostics((thread, reply))

    assert rejected == ()
    assert len(kept) == 1
    assert kept[0].discovery_type == "reply"
    assert len(kept[0].discoveries) == 2
    assert diagnostics.raw_hits == 2
    assert diagnostics.duplicate == 1
    assert diagnostics.usable == 1


def test_search_candidates_merges_reply_discovery_from_a_later_round() -> None:
    analysis = parse_tieba_request("华东交通大学吧 宿舍条件")
    calls: list[str] = []

    class Search:
        def search_public(
            self, account_id: str, *, query: str, **kwargs: object
        ) -> SearchOutcome:
            del account_id, kwargs
            calls.append(query)
            hits = (
                (_hit(discoveries=[_discovery(discovery_type="thread")]),)
                if len(calls) == 1
                else (
                    _hit(
                        discovery_type="reply",
                        page=2,
                        matched_reply="寝室晚上热水不稳定",
                        matched_reply_id="153449627007",
                        discoveries=[
                            _discovery(
                                discovery_type="reply",
                                page=2,
                                matched_reply="寝室晚上热水不稳定",
                                matched_reply_id="153449627007",
                            )
                        ],
                    ),
                )
            )
            return SearchOutcome(
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.SUCCESS,
                    evidence_count=len(hits),
                ),
                hits=hits,
                candidates=hits,
                rejected=(),
                diagnostics=HitDiagnostics(raw_hits=len(hits), usable=len(hits)),
            )

    result = search_candidates(
        Search(), "account-1", analysis, stop_event=None, deadline_seconds=10
    )

    assert len(calls) >= 2
    assert len(result.hits) == 1
    assert result.hits[0].discovery_type == "reply"
    assert len(result.hits[0].discoveries) == 2
