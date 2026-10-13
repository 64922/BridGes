"""Issue 14 单元合同：实际读取、时间范围和用户结果呈现。"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.contracts import (
    TiebaReply,
    TiebaSearchHit,
)
from bridges.tieba.evidence_official import official_candidates as _official_candidates
from bridges.tieba.evidence_time import (
    apply_time_condition as _apply_time_condition,
)
from bridges.tieba.evidence_time import (
    time_filter_state as _time_filter_state,
)
from bridges.tieba.lexicon import TARGET_FORUM_NAME
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.presenting import (
    REPLIES_NOT_OBTAINED,
    build_sections,
    render_empty_content,
    render_result_content,
)
from tests.tieba.support import _post


def test_sections_only_use_read_replies_and_cite_floor_and_time() -> None:
    """分段只来自真实读到的楼层，并且每条都带楼层与时间出处分。"""
    post = _post(
        TiebaReply(
            floor=3, posted_at="2025-05-25 21:03", content="我去年转专业成功了，条件是学分绩点。"
        ),
        TiebaReply(
            floor=4, posted_at="2025-05-26 08:11", content="但是不同学院要求不一样，别听一楼的。"
        ),
        TiebaReply(floor=5, posted_at="2025-05-27 09:00", content="听说今年名额会变少，可能更难。"),
    )

    sections = build_sections([post])
    joined = "\n".join(sections)

    assert "可核验的个人经历" in joined
    assert "不同看法" in joined
    assert "不确定点" in joined
    assert "第 3 楼" in joined and "2025-05-25 21:03" in joined
    assert "普遍" not in joined


def test_no_replies_means_no_sections_and_explicit_missing_note() -> None:
    """拿不到回复时不产出任何个人经历或共同看法，只说明未取得回复内容。"""
    post = _post().model_copy(
        update={
            "replies_obtained": False,
            "read_error_message": "页面要求登录或触发了访问验证，未取得回复内容。",
        }
    )

    assert build_sections([post]) == []


def test_render_marks_unconfirmed_links_and_missing_replies() -> None:
    """正文里明确写出「未取得回复内容」与归属未确认。"""
    from bridges.tieba.contracts import (
        TiebaCandidateLink,
        TiebaResearchProjection,
        TiebaResearchStatus,
        TiebaTimeFilter,
    )

    projection = TiebaResearchProjection(
        status=TiebaResearchStatus.LINKS_ONLY,
        topic="宿舍",
        original_question="华东交通大学吧 宿舍 条件",
        topic_terms=["宿舍", "条件"],
        time_filter=TiebaTimeFilter(
            requirement=None, year=None, applied=False, note="本轮问题没有提出时间条件。"
        ),
        candidate_links=[
            TiebaCandidateLink(
                url="https://tieba.baidu.com/p/10745250786",
                title="东北电力和华东交通哪个好",
                source="tavily",
                unconfirmed_reason="页面不可读：访问受限，未绕过",
            )
        ],
        evidence_boundary=["只纳入有证据确认属于「华东交通大学吧」的帖子。"],
        empty_reason=f"没有取得可确认属于「{TARGET_FORUM_NAME}」的帖子页面。",
    )

    content = render_empty_content(projection)

    assert "归属未确认" in content
    assert "未取得回复内容" in content
    assert "https://tieba.baidu.com/p/10745250786" in content


def test_explicit_year_classifies_replies_without_dropping_historical_context() -> None:
    """明确年份按实际发言时间分类，旧回复保留为上下文。"""
    analysis = parse_tieba_request("华东交通大学吧 2025年 转专业")
    post = _post(
        TiebaReply(floor=1, posted_at="2024-09-01 10:00", content="我是 2024 年转的。"),
        TiebaReply(floor=2, posted_at="2025-03-02 10:00", content="2025 年政策变了。"),
    )

    state, filtered = _apply_time_condition(analysis, [post])

    assert state.applied is True
    assert state.recent_count == 1
    assert state.historical_count == 1
    assert [reply.floor for reply in filtered[0].replies] == [1, 2]
    assert [reply.temporal_scope.value for reply in filtered[0].replies] == ["historical", "recent"]


def test_default_time_window_uses_reply_time_even_when_original_post_is_old() -> None:
    analysis = parse_tieba_request("华东交通大学吧宿舍怎么样？")
    post = _post(
        TiebaReply(
            floor=None,
            posted_at="2024-02-01",
            is_original_poster=True,
            content="旧帖主题。",
        ),
        TiebaReply(floor=9, posted_at="2026-09-20", content="最近的回复补充了情况。"),
    )
    state, tagged = _apply_time_condition(analysis, [post])
    assert state.since == datetime(2025, 9, 25, tzinfo=UTC)
    assert state.recent_count == 1 and state.historical_count == 1
    assert [reply.temporal_scope.value for reply in tagged[0].replies] == ["historical", "recent"]


def test_time_condition_without_times_says_it_was_not_applied() -> None:
    """没有可核对时间时如实说明时间条件没有参与过滤。"""
    analysis = parse_tieba_request("华东交通大学吧 2025年 转专业")
    state = _time_filter_state(analysis, [_post()])

    assert state.applied is False
    assert "没有读到带发帖时间的楼层" in state.note


def test_render_result_content_states_read_scope() -> None:
    """结果正文写出实际读取页数、楼层范围与帖子链接。"""
    post = _post(
        TiebaReply(floor=1, posted_at="2025-05-25 21:03", content="主帖内容。"),
        TiebaReply(floor=9, posted_at="2025-05-26 08:11", content="我是过来人，说说体验。"),
    )
    from bridges.tieba.contracts import (
        TiebaResearchProjection,
        TiebaResearchStatus,
        TiebaTimeFilter,
    )

    projection = TiebaResearchProjection(
        status=TiebaResearchStatus.SUCCESS,
        topic="转专业",
        original_question="华东交通大学吧 转专业",
        topic_terms=["转专业"],
        summary="近期回复提到部分课程转专业名额有限。",
        summary_note="另有历史回复仅供解释近期楼中楼，没有作为总结依据。",
        time_filter=TiebaTimeFilter(
            requirement=None, year=None, applied=False, note="本轮问题没有提出时间条件。"
        ),
        confirmed_posts=[post],
        sections=build_sections([post]),
    )

    content = render_result_content(projection)

    assert "已读取 1/2 页" in content
    assert "楼层 1–9" in content
    assert post.url in content
    assert "【讨论总结】" in content
    assert "总结说明：另有历史回复仅供解释近期楼中楼" in content
    assert REPLIES_NOT_OBTAINED not in content


def test_official_candidates_prefer_topical_current_year_pages() -> None:
    """官方候选排序：标题点题且带当前年份的通知排前面，旧活动报道不占首位。"""
    from bridges.tieba.searching import SearchOutcome, query_record

    outcome = SearchOutcome(
        record=query_record(
            query="site:ecjtu.edu.cn 华东交通大学 放假 中秋节",
            status=ModuleQueryStatus.SUCCESS,
            evidence_count=3,
        ),
        hits=(
            TiebaSearchHit(
                url="https://jxjy.ecjtu.edu.cn/info/1058/2190.htm",
                title="国庆悦赏山河美，花式表白我的国｜红色景点打卡来啦！",
                snippet="",
            ),
            TiebaSearchHit(
                url="https://lib.ecjtu.edu.cn",
                title="华东交通大学图书馆",
                snippet="",
            ),
            TiebaSearchHit(
                url="https://lib.ecjtu.edu.cn/info/1076/7501.htm",
                title="关于2026年中秋节国庆节假期图书馆开放安排的通知",
                snippet="",
            ),
            TiebaSearchHit(
                url="https://www.zhihu.com/question/1",
                title="非官方域名不参与",
                snippet="",
            ),
        ),
        candidates=(),
        rejected=(),
    )

    ordered = _official_candidates(outcome, ("放假", "中秋节"), preferred_year=2026)

    assert ordered[0] == "https://lib.ecjtu.edu.cn/info/1076/7501.htm"
    assert "https://www.zhihu.com/question/1" not in ordered


def test_empty_state_still_shows_official_check_section() -> None:
    """空态（贴吧没有可用帖子）也要给出官方核验结果，且与吧友讨论分列。"""
    from bridges.tieba.contracts import (
        TiebaOfficialCheck,
        TiebaResearchProjection,
        TiebaResearchStatus,
        TiebaTimeFilter,
    )

    projection = TiebaResearchProjection(
        status=TiebaResearchStatus.EMPTY,
        topic="放假 中秋节",
        original_question="中秋节放假",
        topic_terms=["放假", "中秋节"],
        time_filter=TiebaTimeFilter(
            requirement=None, year=None, applied=False, note="本轮问题没有提出时间条件。"
        ),
        official_check_requested=True,
        official_checks=[
            TiebaOfficialCheck(
                title="关于2026年中秋节国庆节假期图书馆开放安排的通知",
                url="https://lib.ecjtu.edu.cn/info/1076/7501.htm",
                host="lib.ecjtu.edu.cn",
                fetched_at=datetime(2026, 9, 27, tzinfo=UTC),
                status="verified",
                excerpt="中秋节国庆节假期图书馆开放安排：10 月 1 日至 7 日闭馆。",
                matched_terms=["中秋节"],
            )
        ],
        empty_reason="共取得 5 条原始搜索结果（非帖子链接 5 条），没有可确认属于该吧的帖子。",
    )

    content = render_empty_content(projection)

    assert "共取得 5 条原始搜索结果" in content
    assert "官方原文摘录" in content
    assert "不能替代官方规定" in content
