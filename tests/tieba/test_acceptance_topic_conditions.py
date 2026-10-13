"""独立验收：讨论来源意图及实际发言的校区、同义词条件。"""

from datetime import UTC, datetime

import pytest

from bridges.tieba.contracts import ReadStatus, TiebaReadResult, TiebaReply, TiebaSearchHit
from bridges.tieba.evidence import read_candidates
from bridges.tieba.evidence_search import SearchAttempts
from bridges.tieba.evidence_time import apply_time_condition
from bridges.tieba.evidence_verification import build_evidence_plan
from bridges.tieba.parsing import parse_tieba_request


@pytest.mark.parametrize("topic", ["转专业", "住宿费", "放假安排", "管理规定"])
def test_discussion_only_does_not_add_official_route(topic: str) -> None:
    analysis = parse_tieba_request(f"搜集华东交通大学吧关于{topic}的讨论")
    assert not analysis.needs_official_check
    assert not build_evidence_plan(analysis, official_blocked_reason=None).official_queries


@pytest.mark.parametrize("instruction", ["核实转专业要求", "确认宿舍管理规定", "查证放假消息真伪"])
def test_explicit_verification_adds_official_route(instruction: str) -> None:
    analysis = parse_tieba_request(f"搜集华东交通大学吧讨论并{instruction}")
    assert analysis.needs_official_check
    assert build_evidence_plan(analysis, official_blocked_reason=None).official_queries


def _read(question: str, replies: list[TiebaReply], title: str = "闲聊"):
    url = "https://tieba.baidu.com/p/100"

    class Reader:
        def read(self, url: str, **kwargs: object) -> TiebaReadResult:
            return TiebaReadResult(
                url=url,
                status=ReadStatus.READ,
                forum_name="华东交通大学吧",
                title=title,
                replies=replies,
                retrieved_at=datetime.now(UTC),
            )

    return read_candidates(
        Reader(),
        SearchAttempts(
            hits=(TiebaSearchHit(url=url, title=title, snippet=""),), rejected=(), records=[]
        ),
        analysis=parse_tieba_request(question),
        stop_event=None,
        deadline_seconds=10,
    )


def test_requested_campus_excludes_other_campus_only_discussion() -> None:
    outcome = _read("搜集北区宿舍条件讨论", [TiebaReply(content="南区宿舍挺好")])
    assert not outcome.confirmed
    assert outcome.rejected


def test_campus_filter_excludes_conflicting_replies_even_when_title_matches() -> None:
    outcome = _read(
        "搜集北区宿舍条件讨论",
        [
            TiebaReply(content="北区寝室比较旧", reply_id="1"),
            TiebaReply(content="南区宿舍挺好", reply_id="2"),
            TiebaReply(content="今年修好了", reply_id="3", parent_reply_id="1"),
        ],
        title="北区宿舍条件",
    )
    assert [reply.reply_id for reply in outcome.confirmed[0].replies] == ["1", "3"]


def test_campus_match_does_not_keep_a_reply_about_a_different_topic() -> None:
    outcome = _read(
        "搜集北区宿舍条件讨论",
        [
            TiebaReply(content="北区宿舍条件比较好", reply_id="1"),
            TiebaReply(content="北区食堂也很好吃", reply_id="2"),
        ],
        title="北区校园生活",
    )
    assert [reply.reply_id for reply in outcome.confirmed[0].replies] == ["1"]
    assert "话题条件" in outcome.confirmed[0].read_error_message


def test_campus_and_topic_title_do_not_keep_an_unrelated_reply() -> None:
    outcome = _read(
        "搜集北区宿舍条件讨论",
        [
            TiebaReply(content="北区宿舍条件比较好", reply_id="1"),
            TiebaReply(content="苹果手机续航不错", reply_id="2"),
        ],
        title="北区宿舍条件",
    )
    assert [reply.reply_id for reply in outcome.confirmed[0].replies] == ["1"]


def test_synonym_in_reply_is_confirmed_without_title_hit() -> None:
    outcome = _read("搜集宿舍条件讨论", [TiebaReply(content="寝室有空调", reply_id="1")])
    assert outcome.confirmed


def test_campus_name_alone_does_not_confirm_unrelated_topic() -> None:
    outcome = _read("搜集北区宿舍条件讨论", [TiebaReply(content="北区食堂好吃")])
    assert not outcome.confirmed


def test_campus_specific_correction_keeps_unknown_campus_parent_context() -> None:
    outcome = _read(
        "搜集北区宿舍条件讨论",
        [
            TiebaReply(content="寝室没有空调", reply_id="1"),
            TiebaReply(content="北区今年宿舍已装空调", reply_id="2", parent_reply_id="1"),
        ],
    )
    assert [reply.reply_id for reply in outcome.confirmed[0].replies] == ["1", "2"]


def test_reply_date_without_year_does_not_become_recent_evidence() -> None:
    outcome = _read(
        "搜集宿舍条件讨论",
        [TiebaReply(content="宿舍挺好", posted_at="4月1日")],
    )
    time_filter, posts = apply_time_condition(
        parse_tieba_request("搜集宿舍条件讨论"),
        outcome.confirmed,
    )
    assert time_filter.unknown_count == 1
    assert time_filter.recent_count == 0
    assert posts[0].replies[0].posted_at == "4月1日"
