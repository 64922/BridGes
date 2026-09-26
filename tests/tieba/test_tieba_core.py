"""Issue 14 单元合同：解析、查询词、归属判定、时间条件与分段呈现。

这些用例不发起任何外部调用：只验证确定性判断（原词保留、他吧证据排除、
只消费已读文本的分段）与真实样本里观察到的形态。
"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.tieba.contracts import (
    ReadStatus,
    TiebaPostProjection,
    TiebaReply,
    TiebaSearchHit,
)
from bridges.tieba.lexicon import TARGET_FORUM_NAME
from bridges.tieba.parsing import parse_tieba_request, pending_payload
from bridges.tieba.presenting import (
    REPLIES_NOT_OBTAINED,
    build_sections,
    render_empty_content,
    render_result_content,
)
from bridges.tieba.searching import (
    REJECT_OTHER_FORUM_HEADER,
    REJECT_OTHER_FORUM_TITLE,
    canonical_url,
    classify_hits,
    plan_queries,
)
from bridges.tieba.service import _apply_time_condition, _time_filter_state


def test_module_only_question_asks_one_clarification() -> None:
    """整句只有模块用语时问一个问题，并给出可持久化的恢复载荷。"""
    analysis = parse_tieba_request("贴吧里大家都怎么说？")

    assert analysis.topic_terms == []
    assert analysis.clarification is not None
    assert TARGET_FORUM_NAME in analysis.clarification.question
    assert pending_payload(analysis)["original_question"] == "贴吧里大家都怎么说？"


def test_original_question_and_time_condition_are_preserved() -> None:
    """原始问题与时间条件逐字保留，并解析出绝对年份。"""
    analysis = parse_tieba_request("华东交通大学吧里 2025年 转专业的要求是什么？")

    assert analysis.original_question == "华东交通大学吧里 2025年 转专业的要求是什么？"
    assert analysis.time_requirement == "2025年"
    assert analysis.time_year == 2025
    assert "转专业" in analysis.topic_terms
    assert analysis.needs_official_check is True


def test_clarification_answer_resumes_with_original_question() -> None:
    """恢复轮：主题取自用户这一条回答，原问题仍以首次提问为准。"""
    pending = {
        "original_question": "贴吧里怎么说",
        "time_requirement": "",
    }
    resumed = parse_tieba_request("宿舍条件", pending=pending)

    assert resumed.original_question == "贴吧里怎么说"
    assert resumed.topic_terms == ["宿舍", "条件"]
    assert resumed.clarification is None


def test_query_words_keep_original_terms_and_host_hint() -> None:
    """查询词只用原始名词与固定字面量，域名提示保持完整。"""
    analysis = parse_tieba_request("华东交通大学吧 转专业 条件")

    queries = plan_queries(analysis)
    assert queries[0] == "tieba.baidu.com 华东交通大学吧 转专业 条件"
    assert "转专业" in queries[0] and "条件" in queries[0]
    assert queries[1].endswith("贴吧")


def test_thread_urls_are_canonicalised() -> None:
    """移动端与桌面端链接归一到同一个帖子链接。"""
    assert (
        canonical_url("https://c.tieba.baidu.com/p/10745250786?lp=home_main_thread_pb")
        == "https://tieba.baidu.com/p/10745250786"
    )
    assert canonical_url("https://example.com/p/1") == "https://example.com/p/1"


def test_other_forum_title_is_rejected() -> None:
    """搜索结果标题就是别的贴吧名称：剔除并留下依据（真实样本形态）。"""
    hits = (
        TiebaSearchHit(
            url="https://c.tieba.baidu.com/p/5668552372?lp=home_main_thread_pb",
            title="上海交通大学研究生吧",
            snippet="上海交通大学研究生吧・ 华东交通大学吧关注19w 贴子786.6w App",
        ),
    )
    kept, rejected = classify_hits(hits)

    assert kept == ()
    assert len(rejected) == 1
    assert rejected[0].evidence == REJECT_OTHER_FORUM_TITLE
    assert rejected[0].url == "https://tieba.baidu.com/p/5668552372"


def test_other_forum_header_in_snippet_is_rejected() -> None:
    """摘要里出现他吧吧头（吧名+关注+贴子）：剔除，不作为目标吧帖子。"""
    hits = (
        TiebaSearchHit(
            url="https://c.tieba.baidu.com/p/3314041970",
            title="有没有学弟学妹",
            snippet="新生宿舍安排表：上海交通大学研究生吧关注19w 贴子786.4w App内查看",
        ),
    )
    kept, rejected = classify_hits(hits)

    assert kept == ()
    assert REJECT_OTHER_FORUM_HEADER in rejected[0].evidence
    assert "上海交通大学研究生吧" in rejected[0].evidence


def test_glued_snippet_header_mentioning_target_forum_is_not_rejected() -> None:
    """摘要拼接把前文粘进吧名：只要指向本校就不按他吧剔除（宁可多读一次）。"""
    hits = (
        TiebaSearchHit(
            url="https://c.tieba.baidu.com/p/3314041970",
            title="有没有学弟学妹",
            snippet="新生宿舍安排表：中南华东交通大学吧关注19w 贴子786.4w App内查看",
        ),
    )
    kept, rejected = classify_hits(hits)

    assert rejected == ()
    assert kept[0].url == "https://tieba.baidu.com/p/3314041970"


def test_sentence_title_ending_with_forum_mention_is_not_rejected() -> None:
    """标题以「吧」结尾但不是吧名：不当作他吧标题剔除（真实候选不能丢）。"""
    hits = (
        TiebaSearchHit(
            url="https://c.tieba.baidu.com/p/7000000003",
            title="有谁还记得当年在华东交大吧",
            snippet="",
        ),
    )
    kept, rejected = classify_hits(hits)

    assert rejected == ()
    assert len(kept) == 1


def test_non_thread_and_duplicate_urls_are_dropped() -> None:
    """用户主页与热点页不是帖子；同一帖子的多种链接只留一条。"""
    hits = (
        TiebaSearchHit(
            url="https://nani.baidu.com/home/main?id=tb.1.ce8f0206",
            title="华东交大小益君的贴吧",
            snippet="",
        ),
        TiebaSearchHit(
            url="https://tieba.baidu.com/hottopic/browse/hottopic?topic_id=5674439",
            title="专题",
            snippet="",
        ),
        TiebaSearchHit(
            url="https://c.tieba.baidu.com/p/10745250786?mo_device=1",
            title="东北电力和华东交通哪个好",
            snippet="",
        ),
        TiebaSearchHit(
            url="https://tieba.baidu.com/p/10745250786",
            title="东北电力和华东交通哪个好",
            snippet="",
        ),
    )
    kept, rejected = classify_hits(hits)

    assert rejected == ()
    assert [hit.url for hit in kept] == ["https://tieba.baidu.com/p/10745250786"]


def _post(
    *replies: TiebaReply,
    url: str = "https://tieba.baidu.com/p/10745250786",
    title: str = "东北电力和华东交通哪个好",
    pages_read: int = 1,
) -> TiebaPostProjection:
    return TiebaPostProjection(
        thread_id="10745250786",
        url=url,
        title=title,
        affiliation_evidence="已读取帖子页面，页面声明所属贴吧为「华东交通大学吧」",
        read_status=ReadStatus.READ,
        pages_read=pages_read,
        pages_limit=2,
        total_pages=3,
        floor_min=replies[0].floor if replies else None,
        floor_max=replies[-1].floor if replies else None,
        replies_obtained=bool(replies),
        replies=list(replies),
        retrieved_at=datetime(2026, 9, 25, tzinfo=UTC),
    )


def test_sections_only_use_read_replies_and_cite_floor_and_time() -> None:
    """分段只来自真实读到的楼层，并且每条都带楼层与时间出处分。"""
    post = _post(
        TiebaReply(
            floor=3, posted_at="2025-05-25 21:03", content="我去年转专业成功了，条件是学分绩点。"
        ),
        TiebaReply(
            floor=4, posted_at="2025-05-26 08:11", content="但是不同学院要求不一样，别听一楼的。"
        ),
        TiebaReply(
            floor=5, posted_at="2025-05-27 09:00", content="听说今年名额会变少，可能更难。"
        ),
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
                source="tavily（归属未确认）",
            )
        ],
        evidence_boundary=["只纳入有证据确认属于「华东交通大学吧」的帖子。"],
        empty_reason=f"没有取得可确认属于「{TARGET_FORUM_NAME}」的帖子页面。",
    )

    content = render_empty_content(projection)

    assert "归属未确认" in content
    assert "未取得回复内容" in content
    assert "https://tieba.baidu.com/p/10745250786" in content


def test_time_condition_filters_read_replies_when_year_is_known() -> None:
    """时间条件在读到了带时间的楼层时真的参与过滤，并如实报剔除条数。"""
    analysis = parse_tieba_request("华东交通大学吧 2025年 转专业")
    post = _post(
        TiebaReply(floor=1, posted_at="2024-09-01 10:00", content="我是 2024 年转的。"),
        TiebaReply(floor=2, posted_at="2025-03-02 10:00", content="2025 年政策变了。"),
    )

    state, filtered = _apply_time_condition(analysis, [post])

    assert state.applied is True
    assert "保留 1 条、剔除 1 条" in state.note
    assert [reply.floor for reply in filtered[0].replies] == [2]


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
    assert REPLIES_NOT_OBTAINED not in content
