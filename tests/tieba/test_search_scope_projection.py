"""工单 05：搜索范围投影、读取优先级、运行记录与浏览器端口。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.browser import BrowserTiebaSearch
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaReadResult,
    TiebaReply,
    TiebaSearchScope,
    TiebaTimeFilter,
)
from bridges.tieba.evidence import _unconfirmed_link, confirmed_post, read_candidates
from bridges.tieba.evidence_projection import build_projection
from bridges.tieba.evidence_search import SearchAttempts, search_candidates
from bridges.tieba.node_synthesis import _run_metadata, _unread_scope
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import (
    HitDiagnostics,
    SearchOutcome,
    plan_query_rounds,
    query_record,
)
from tests.tieba.search_pagination_support import URL_A, URL_B, _hit
from tests.tieba.support import _FakeReader, _read_result


def test_projection_exposes_search_scope_and_unexhausted_pages() -> None:
    analysis = parse_tieba_request("华东交通大学吧 空调")
    attempts = SearchAttempts(
        hits=(),
        rejected=(),
        records=[],
        scope=(
            TiebaSearchScope(
                tab="reply",
                query="空调",
                pages_read=2,
                pages_limit=2,
                visited_pages=[1, 2],
                last_page=2,
                has_more=True,
                stop_reason="pages_limit",
                note="仅读取前 2 页，页面声明还有后续结果。",
            ),
        ),
    )

    projection = build_projection(
        analysis=analysis,
        records=[],
        confirmed=[],
        rejected=[],
        unconfirmed=[],
        sections=[],
        time_filter=TiebaTimeFilter(note="本轮问题没有提出时间条件。"),
        official_checks=[],
        attempts=attempts,
    )

    assert projection.search_scope[0].tab == "reply"
    assert projection.search_scope[0].pages_read == 2
    assert any(
        "回复" in note and "第 1–2 页" in note and "还有后续结果" in note
        for note in projection.evidence_boundary
    )


def test_read_candidates_gives_reply_discoveries_a_read_chance_first() -> None:
    analysis = parse_tieba_request("华东交通大学吧 空调")
    thread = _hit(url=URL_A, title="南区健身房")
    reply = _hit(
        url=URL_B,
        title="回复：南区浴室",
        discovery_type="reply",
        page=2,
        matched_reply="空调坏了很久",
        matched_reply_id="153449627008",
    )
    attempts = SearchAttempts(hits=(thread, reply), rejected=(), records=[])
    reader = _FakeReader(
        {
            URL_A: _read_result(
                url=URL_A,
                status=ReadStatus.READ,
                title="南区健身房",
                replies=(
                    TiebaReply(
                        floor=1,
                        content="南区健身房空调看老板心情开。",
                        is_original_poster=True,
                    ),
                ),
            ),
            URL_B: _read_result(
                url=URL_B,
                status=ReadStatus.READ,
                title="南区浴室",
                replies=(
                    TiebaReply(
                        floor=3,
                        content="南区浴室空调坏了很久。",
                    ),
                ),
            ),
        }
    )

    outcome = read_candidates(
        reader,
        attempts,
        analysis=analysis,
        stop_event=None,
        deadline_seconds=5,
    )

    assert reader.read_urls[0] == URL_B
    assert {post.url for post in outcome.confirmed} == {URL_A, URL_B}
    confirmed = {post.url: post for post in outcome.confirmed}
    assert confirmed[URL_B].discoveries[0].discovery_type == "reply"
    assert confirmed[URL_A].discoveries[0].discovery_type == "thread"


def test_confirmed_post_and_candidate_link_keep_discovery_paths() -> None:
    hit = _hit(
        discovery_type="reply",
        matched_reply="空调看老板心情开。",
        matched_reply_id="153449627007",
    )
    result = TiebaReadResult(
        url=URL_A,
        thread_id="10707882244",
        status=ReadStatus.READ,
        forum_name="华东交通大学吧",
        title="南区健身房",
        pages_read=1,
        pages_limit=2,
        replies=[
            TiebaReply(
                floor=3,
                content="有女生专区，空调看老板心情开。",
            )
        ],
        retrieved_at=datetime.now(UTC),
    )

    post = confirmed_post(hit, result)
    link = _unconfirmed_link(hit, "页面不可读：超时")

    assert post.discoveries == hit.discoveries
    assert link.discoveries == hit.discoveries


def test_resume_replaces_scope_for_a_rerun_query() -> None:
    analysis = parse_tieba_request("华东交通大学吧 空调")
    first_query = plan_query_rounds(analysis)[0].query
    old = TiebaSearchScope(
        tab="thread",
        query=first_query,
        pages_read=1,
        pages_limit=2,
        visited_pages=[1],
        last_page=1,
        has_more=True,
        stop_reason="timeout",
    )

    class Search:
        def search_public(
            self, account_id: str, *, query: str, **kwargs: object
        ) -> SearchOutcome:
            del account_id, kwargs
            scope = TiebaSearchScope(
                tab="thread",
                query=query,
                pages_read=2,
                pages_limit=2,
                visited_pages=[1, 2],
                last_page=2,
                has_more=False,
            )
            return SearchOutcome(
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.SUCCESS,
                    evidence_count=0,
                ),
                hits=(),
                candidates=(),
                rejected=(),
                diagnostics=HitDiagnostics(),
                scope=(scope,),
            )

    result = search_candidates(
        Search(),
        "account-1",
        analysis,
        stop_event=None,
        deadline_seconds=10,
        resume={"search_scope": [old.model_dump()]},
    )

    scope = next(item for item in result.scope if item.query == first_query)
    assert scope.pages_read == 2
    assert scope.stop_reason is None


def test_unread_scope_reports_unexhausted_search_pages() -> None:
    scope = TiebaSearchScope(
        tab="thread",
        query="空调",
        pages_read=2,
        pages_limit=2,
        visited_pages=[1, 2],
        last_page=2,
        has_more=True,
        stop_reason="pages_limit",
        note="仅读取前 2 页，页面声明还有后续结果。",
    )

    notes = _unread_scope([], (scope,))

    assert any("主题" in note and "第 1–2 页" in note and "还有后续结果" in note for note in notes)


class _Runtime:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value
        self.requests: list[dict[str, Any]] = []

    def request(self, account_id: str, payload: dict[str, Any], **kwargs: object) -> dict[str, Any]:
        del account_id, kwargs
        self.requests.append(payload)
        return self.value


class _Public:
    def search_public(self, *args: object, **kwargs: object) -> SearchOutcome:
        raise AssertionError("浏览器搜索不应回退到公开索引")


def test_browser_search_port_maps_pages_and_merges_discovery_paths() -> None:
    thread = _hit()
    reply = _hit(
        discovery_type="reply",
        page=2,
        matched_reply="有女生专区，空调看老板心情开。",
        matched_reply_id="153449627007",
    )
    runtime = _Runtime(
        {
            "hits": [thread.model_dump(), reply.model_dump()],
            "search_scope": [
                TiebaSearchScope(
                    tab="reply",
                    query="空调",
                    pages_read=2,
                    pages_limit=2,
                    visited_pages=[1, 2],
                    last_page=2,
                    has_more=False,
                    note="已读到第 2 页，页面声明没有更多结果。",
                ).model_dump()
            ],
            "partial": False,
            "stop_reason": None,
        }
    )

    outcome = BrowserTiebaSearch(runtime, _Public()).search_public(  # type: ignore[arg-type]
        "account-1",
        query="site:tieba.baidu.com/p/ 华东交通大学吧 空调",
        reason="test",
    )

    assert len(outcome.hits) == 1
    assert outcome.hits[0].discovery_type == "reply"
    assert len(outcome.hits[0].discoveries) == 2
    assert outcome.scope[0].pages_read == 2
    assert outcome.record.status is ModuleQueryStatus.SUCCESS
    assert "实际吧内检索词：空调" in (outcome.record.detail or "")


def test_browser_search_port_keeps_partial_hits_and_scope_on_access_restriction() -> None:
    runtime = _Runtime(
        {
            "hits": [_hit().model_dump()],
            "search_scope": [
                TiebaSearchScope(
                    tab="thread",
                    query="空调",
                    pages_read=1,
                    pages_limit=2,
                    visited_pages=[1],
                    last_page=1,
                    has_more=True,
                    stop_reason="access_restricted",
                    note="翻页时出现登录或验证入口，已保留已取得结果。",
                ).model_dump()
            ],
            "partial": True,
            "stop_reason": "access_restricted",
            "error_code": "tieba_browser_collection_restricted",
            "error_message": "专用贴吧会话已登录，但当前采集入口仍被限制。",
        }
    )

    outcome = BrowserTiebaSearch(runtime, _Public()).search_public(  # type: ignore[arg-type]
        "account-1",
        query="site:tieba.baidu.com/p/ 华东交通大学吧 空调",
        reason="test",
    )

    assert len(outcome.hits) == 1
    assert outcome.scope[0].stop_reason == "access_restricted"
    assert outcome.record.status is ModuleQueryStatus.ERROR
    assert outcome.record.error_code == "tieba_browser_collection_restricted"


def test_run_metadata_counts_search_pages() -> None:
    invocation = SimpleNamespace(run_id="run-1")
    attempts = SearchAttempts(
        hits=(),
        rejected=(),
        records=[],
        scope=(
            TiebaSearchScope(
                tab="thread",
                query="空调",
                pages_read=2,
                pages_limit=2,
                visited_pages=[1, 2],
                last_page=2,
                has_more=False,
            ),
        ),
    )
    reads = SimpleNamespace(attempted_urls=(), confirmed=[], unconfirmed=[], rejected=[])
    now = datetime.now(UTC)

    metadata = _run_metadata(
        invocation,  # type: ignore[arg-type]
        attempts=attempts,
        reads=reads,  # type: ignore[arg-type]
        checks=(),
        confirmed=(),
        started_at=now,
        finished_at=now,
        model_calls=0,
    )

    assert metadata.search_page_count == 2
    assert any("主题" in note and "第 1–2 页" in note for note in metadata.unread_scope)
