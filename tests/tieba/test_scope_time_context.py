"""工单 08：话题、校区与发言时间条件必须作用于新分页证据。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.ai.model_quota import QuotaVerificationBasis, RunModelQuota
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaReadResult,
    TiebaReply,
    TiebaSearchHit,
    TiebaTemporalScope,
    TiebaTimeFilter,
    TiebaTimeScopeMode,
)
from bridges.tieba.evidence import ReadOutcome, read_candidates
from bridges.tieba.evidence_projection import evidence_boundary
from bridges.tieba.evidence_search import SearchAttempts
from bridges.tieba.evidence_time import apply_time_condition, summary_scope
from bridges.tieba.evidence_time_rules import parse_posted_date
from bridges.tieba.lexicon import TARGET_FORUM_NAME
from bridges.tieba.node_synthesis import _run_metadata
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.summary import TiebaSummaryGenerator

ANCHOR = datetime(2026, 10, 19, tzinfo=UTC)


def _post(
    *replies: TiebaReply,
    title: str = "宿舍讨论",
    thread_id: str = "123",
) -> TiebaPostProjection:
    return TiebaPostProjection(
        thread_id=thread_id,
        url=f"https://tieba.baidu.com/p/{thread_id}",
        title=title,
        affiliation_evidence=f"已读取帖子页面，页面声明所属贴吧为「{TARGET_FORUM_NAME}」",
        read_status=ReadStatus.PARTIAL,
        pages_read=1,
        pages_limit=2,
        replies_obtained=bool(replies),
        replies=list(replies),
        retrieved_at=ANCHOR,
    )


def _reply(reply_id: str, content: str, posted_at: str | None = None, **extra: Any) -> TiebaReply:
    return TiebaReply(reply_id=reply_id, content=content, posted_at=posted_at, **extra)


def test_default_window_prefers_recent_and_omits_older_evidence() -> None:
    analysis = parse_tieba_request("搜集华东交通大学吧宿舍讨论")
    post = _post(
        _reply("r1", "近期宿舍有空调", "2026-09-01"),
        _reply("r2", "近期寝室热水稳定", "2026-08-01"),
        _reply("r3", "近期宿舍门禁变严", "2026-07-01"),
        _reply("h1", "以前宿舍没有空调", "2024-09-01"),
        _reply("u1", "时间未知的宿舍体验", "4月1日"),
    )

    state, scoped = summary_scope(analysis, [post])

    assert state.mode is TiebaTimeScopeMode.DEFAULT_RECENT
    assert state.eligible_count == 3
    assert state.excluded_count == 2
    assert state.exclusion_reasons == {"out_of_time_range": 1, "unknown_time": 1}
    assert [reply.reply_id for reply in scoped[0].replies] == ["r1", "r2", "r3"]
    assert state.history_supplemented is False


def test_default_window_explicitly_supplements_history_when_recent_is_thin() -> None:
    analysis = parse_tieba_request("搜集华东交通大学吧宿舍讨论")
    post = _post(
        _reply("r1", "近期宿舍有空调", "2026-09-01"),
        _reply("h1", "以前宿舍没有空调", "2024-09-01"),
        _reply("h2", "去年宿舍刚改造", "2025-09-01"),
        _reply("u1", "时间未知的宿舍体验", "4月1日"),
    )

    state, scoped = summary_scope(analysis, [post])

    assert state.mode is TiebaTimeScopeMode.DEFAULT_SUPPLEMENT
    assert state.history_supplemented is True
    assert state.eligible_count == 4
    assert state.excluded_count == 0
    assert "补充历史" in state.note
    assert [reply.reply_id for reply in scoped[0].replies] == ["r1", "h1", "h2", "u1"]
    assert {reply.temporal_scope for reply in scoped[0].replies} == {
        TiebaTemporalScope.RECENT,
        TiebaTemporalScope.HISTORICAL,
        TiebaTemporalScope.UNKNOWN,
    }


def test_explicit_range_keeps_out_of_range_parent_only_as_context() -> None:
    analysis = parse_tieba_request("搜集华东交通大学吧 2026年9月 宿舍讨论")
    post = _post(
        _reply("parent", "宿舍空调好用吗", "2025-09-01"),
        _reply("child", "九月北区宿舍已经装空调了", "2026-09-05", parent_reply_id="parent"),
        _reply("outside", "十月宿舍又停水", "2026-10-02"),
    )

    state, scoped = summary_scope(analysis, [post])

    assert state.mode is TiebaTimeScopeMode.EXPLICIT
    assert [reply.reply_id for reply in scoped[0].replies] == ["parent", "child"]
    by_id = {reply.reply_id: reply for reply in scoped[0].replies}
    assert by_id["parent"].context_only is True
    assert by_id["child"].context_only is False
    assert state.eligible_count == 1
    assert state.context_only_count == 1
    assert state.exclusion_reasons == {"out_of_time_range": 1}


@pytest.mark.parametrize(
    ("posted_at", "expected"),
    [
        ("4月1日", None),
        ("2099-04-01", None),
        ("1999-04-01", None),
        ("2026-09-01 10:00 PST", None),
        ("2026-09-01T20:00:00Z", "2026-09-02"),
        ("2026-09-01T10:00:00+08:00", "2026-09-01"),
    ],
)
def test_untrusted_time_text_is_unknown_and_known_timezones_are_converted(
    posted_at: str, expected: str | None
) -> None:
    analysis = parse_tieba_request("搜集华东交通大学吧宿舍讨论")
    post = _post(_reply("r1", "宿舍体验", posted_at))

    state, tagged = apply_time_condition(analysis, [post])

    if expected is None:
        assert tagged[0].replies[0].temporal_scope is TiebaTemporalScope.UNKNOWN
        assert state.unknown_count == 1
    else:
        assert state.unknown_count == 0
        assert tagged[0].replies[0].posted_at == posted_at
        assert tagged[0].replies[0].temporal_scope is TiebaTemporalScope.RECENT
        assert parse_posted_date(posted_at, ANCHOR.date()).isoformat() == expected


def test_old_post_with_recent_reply_uses_reply_actual_time() -> None:
    analysis = parse_tieba_request("搜集华东交通大学吧 2026年9月 宿舍讨论")
    post = _post(
        _reply("old", "以前的宿舍情况", "2020-09-01"),
        _reply("new", "今年九月宿舍已经装空调", "2026-09-10"),
    )

    state, tagged = apply_time_condition(analysis, [post])

    assert state.recent_count == 1
    assert state.historical_count == 1
    assert [reply.temporal_scope for reply in tagged[0].replies] == [
        TiebaTemporalScope.HISTORICAL,
        TiebaTemporalScope.RECENT,
    ]


def _read_result(replies: list[TiebaReply], title: str = "闲聊") -> TiebaReadResult:
    return TiebaReadResult(
        url="https://tieba.baidu.com/p/100",
        status=ReadStatus.READ,
        forum_name=TARGET_FORUM_NAME,
        title=title,
        replies=replies,
        retrieved_at=ANCHOR,
    )


def _read(question: str, result: TiebaReadResult) -> ReadOutcome:
    class Reader:
        def read(self, url: str, **kwargs: object) -> TiebaReadResult:
            return result

    return read_candidates(
        Reader(),
        SearchAttempts(
            hits=(TiebaSearchHit(url=result.url, title=result.title or "", snippet=""),),
            rejected=(),
            records=[],
        ),
        analysis=parse_tieba_request(question),
        stop_event=None,
        deadline_seconds=10,
    )


def test_replies_without_topic_keywords_are_kept_without_campus_scope() -> None:
    outcome = _read(
        "搜集宿舍条件讨论",
        _read_result(
            [
                _reply("r1", "寝室空调挺凉快", "2026-09-01"),
                _reply("r2", "不是这样，去年就装上了", "2026-09-02", parent_reply_id="r1"),
                _reply("r3", "北区食堂饭菜不错", "2026-09-03"),
            ]
        ),
    )

    assert outcome.confirmed
    replies = outcome.confirmed[0].replies
    assert [reply.reply_id for reply in replies] == ["r1", "r2", "r3"]
    assert all(reply.context_only is False for reply in replies)
    assert outcome.confirmed[0].scope_excluded_reasons == {}


def test_campus_conflict_is_excluded_and_unknown_parent_is_context_only() -> None:
    outcome = _read(
        "搜集北区宿舍条件讨论",
        _read_result(
            [
                _reply("parent", "寝室没有空调", "2026-09-01"),
                _reply(
                    "child", "北区今年宿舍已经装空调了", "2026-09-02", parent_reply_id="parent"
                ),
                _reply("south", "南区宿舍条件很好", "2026-09-03"),
                _reply("food", "北区食堂很好吃", "2026-09-04"),
            ],
            title="北区宿舍条件",
        ),
    )

    assert outcome.confirmed
    post = outcome.confirmed[0]
    by_id = {reply.reply_id: reply for reply in post.replies}
    assert set(by_id) == {"parent", "child"}
    assert by_id["parent"].context_only is True
    assert by_id["child"].context_only is False
    assert post.scope_excluded_reasons == {"campus_conflict": 1, "other_topic": 1}


class FakeGateway:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = iter(outputs)
        self.payloads: list[dict[str, object]] = []

    def invoke(
        self,
        capability: str,
        version: str,
        run_context: object,
        payload: dict[str, object],
        **kwargs: object,
    ) -> object:
        self.payloads.append(payload)
        return SimpleNamespace(status=SimpleNamespace(value="success"), output=next(self.outputs))


def _quota() -> RunModelQuota:
    return RunModelQuota(
        model_id="verified-model",
        context_window=16000,
        max_input_tokens=12000,
        verification_basis=QuotaVerificationBasis.FACTORY_MATRIX,
    )


def _summary_outputs(citation_ids: list[str]) -> list[dict[str, object]]:
    claims = [
        {
            "kind": "finding",
            "text": "一条有依据的观点。",
            "citation_ids": citation_ids,
        }
    ]
    return [{"claims": claims}, {"summary": "一段总结。", "claims": claims}, {"supported": [0, 1]}]


def test_context_only_parent_cannot_be_cited() -> None:
    analysis = parse_tieba_request("搜集北区宿舍条件讨论")
    post = _post(
        _reply("parent", "寝室没有空调", "2026-09-01"),
        _reply("child", "北区今年宿舍已经装空调了", "2026-09-02", parent_reply_id="parent"),
    )
    state, scoped = summary_scope(
        analysis.model_copy(update={"campus_terms": ["北区"]}), [post]
    )
    # 父回复本身未点名校区，只能作上下文；模型若引用父回复会被过滤掉。
    gateway = FakeGateway(_summary_outputs(["E1"]))
    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(),
        [_post(post.replies[0].model_copy(update={"context_only": True}), post.replies[1])],
        topic="北区宿舍",
        time_requirement=None,
        time_filter=state,
        model_id="verified-model",
        model_quota=_quota(),
        budget=None,
    )

    evidence = json.loads(gateway.payloads[0]["messages"][1]["content"])["evidence"]
    assert [(item["reply_id"], item["context_only"]) for item in evidence] == [
        ("parent", True),
        ("child", False),
    ]
    assert outcome.summary is None
    assert not outcome.citations


def test_default_supplement_marks_historical_as_citable_and_time_change_source() -> None:
    analysis = parse_tieba_request("搜集华东交通大学吧宿舍讨论")
    post = _post(
        _reply("r1", "近期宿舍有空调", "2026-09-01"),
        _reply("h1", "以前宿舍没有空调", "2024-09-01"),
    )
    state, scoped = summary_scope(analysis, [post])
    gateway = FakeGateway(
        [
            {
                "claims": [
                    {
                        "kind": "time_change",
                        "text": "宿舍从没有空调变为已装空调。",
                        "citation_ids": ["E1", "E2"],
                    }
                ]
            },
            {
                "summary": "宿舍条件随时间发生了变化。",
                "claims": [
                    {
                        "kind": "time_change",
                        "text": "宿舍从没有空调变为已装空调。",
                        "citation_ids": ["E1", "E2"],
                    }
                ],
            },
            {"supported": [0, 1]},
        ]
    )
    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(),
        scoped,
        topic="宿舍",
        time_requirement=None,
        time_filter=state,
        model_id="verified-model",
        model_quota=_quota(),
        budget=None,
    )

    evidence = json.loads(gateway.payloads[0]["messages"][1]["content"])["evidence"]
    assert [
        (item["reply_id"], item["temporal_scope"], item["context_only"])
        for item in evidence
    ] == [
        ("r1", "recent", False),
        ("h1", "historical", False),
    ]
    assert "补充历史" in (outcome.note or "")
    assert {citation.reply_id for citation in outcome.citations} == {"r1", "h1"}


def test_metadata_records_candidates_reads_qualified_evidence_and_exclusions() -> None:
    post = _post(
        _reply("r1", "近期宿舍有空调", "2026-09-01"),
        _reply("h1", "以前宿舍没有空调", "2024-09-01"),
    ).model_copy(update={"scope_excluded_reasons": {"topic_mismatch": 2}})
    state = TiebaTimeFilter(
        note="已分类。",
        mode=TiebaTimeScopeMode.DEFAULT_RECENT,
        recent_count=1,
        historical_count=1,
        eligible_count=1,
        excluded_count=1,
        exclusion_reasons={"out_of_time_range": 1},
    )
    attempts = SearchAttempts(
        hits=(),
        rejected=(),
        records=[],
        diagnostics=SimpleNamespace(raw_hits=9, usable=4),
    )
    reads = ReadOutcome(
        confirmed=[post],
        rejected=[],
        unconfirmed=[],
        attempted_urls=("https://tieba.baidu.com/p/123",),
    )

    metadata = _run_metadata(
        SimpleNamespace(run_id="run-1"),
        attempts=attempts,
        reads=reads,
        checks=[],
        confirmed=[post],
        time_filter=state,
        started_at=ANCHOR,
        finished_at=ANCHOR,
        model_calls=0,
    )

    assert metadata.candidate_count == 9
    assert metadata.usable_candidate_count == 4
    assert metadata.read_post_count == 1
    assert metadata.qualified_evidence_count == 1
    assert metadata.excluded_evidence_count == 3
    assert metadata.exclusion_reasons == {
        "out_of_time_range": 1,
        "topic_mismatch": 2,
    }


def test_no_qualified_evidence_is_not_reported_as_forum_having_no_discussion() -> None:
    post = _post(_reply("h1", "以前的宿舍情况", "2024-09-01"))
    state = TiebaTimeFilter(
        note="显式时间范围。",
        mode=TiebaTimeScopeMode.EXPLICIT,
        recent_count=0,
        historical_count=1,
        eligible_count=0,
        excluded_count=1,
        exclusion_reasons={"out_of_time_range": 1},
    )

    notes = evidence_boundary(
        confirmed=[post],
        unconfirmed=[],
        rejected=[],
        attempts=None,
        reads=None,
        time_filter=state,
    )

    joined = "\n".join(notes)
    assert "没有符合话题、校区和时间条件的合格证据" in joined
    assert "不代表吧内不存在相关讨论" in joined
    assert "时间范围外 1 条" in joined


def test_time_filter_state_can_be_read_without_mutating_posts() -> None:
    analysis = parse_tieba_request("搜集华东交通大学吧宿舍讨论")
    post = _post(_reply("r1", "宿舍体验", "2026-09-01"))
    state, tagged = apply_time_condition(analysis, [post])
    assert state.eligible_count == 1
    assert tagged[0].replies[0].temporal_scope is TiebaTemporalScope.RECENT
    assert isinstance(analysis, TiebaQuestionAnalysis)
