"""Issue 14 单元合同：问题解析、查询词与归属判定。"""

from __future__ import annotations

import pytest

from bridges.tieba.contracts import (
    TiebaSearchHit,
)
from bridges.tieba.lexicon import TARGET_FORUM_NAME
from bridges.tieba.parsing import parse_tieba_request, pending_payload
from bridges.tieba.searching import (
    REJECT_NOT_A_THREAD,
    REJECT_OTHER_FORUM_HEADER,
    REJECT_OTHER_FORUM_TITLE,
    canonical_url,
    classify_hits,
    classify_hits_with_diagnostics,
    plan_queries,
)


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


def test_auth_continue_resumes_original_topic_terms() -> None:
    resumed = parse_tieba_request(
        "继续",
        pending={"original_question": "华东交通大学吧宿舍条件怎么样？"},
    )
    assert resumed.original_question == "华东交通大学吧宿舍条件怎么样？"
    assert "宿舍" in resumed.topic_terms


def test_query_words_keep_original_terms_and_host_hint() -> None:
    """查询词只用原始名词与固定字面量，域名提示保持完整。"""
    analysis = parse_tieba_request("华东交通大学吧 转专业 条件")

    queries = plan_queries(analysis)
    assert queries[0] == "site:tieba.baidu.com/p/ 华东交通大学吧 转专业 条件"
    assert "转专业" in queries[0] and "条件" in queries[0]
    assert "转专业流程" in queries[1]
    assert queries[-1].endswith("贴吧")


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("中秋节放假", ["放假", "中秋节"]),
        ("华东交通大学吧 中秋节 放假 安排", ["放假", "中秋节", "安排"]),
        ("宿舍 上铺 下铺 尺寸", ["宿舍", "上铺", "下铺", "尺寸"]),
        ("食堂 中午 排队吗", ["食堂", "中午", "排队"]),
        ("宿舍 外语 四级 报名", ["报名", "宿舍", "外语", "四级"]),
        ("家里 打钱 到账 要多久", ["家里", "打钱", "到账", "多久"]),
        ("校内 下课后 有 热水 吗", ["热水", "校内", "下课后"]),
    ],
)
def test_topic_terms_keep_real_phrases_containing_positional_chars(
    question: str, expected: list[str]
) -> None:
    """方位类单字（中／上／下／里／内／外）是真实名词的成分，不能按字删除。

    「中秋节」被删成「秋节」不是节日特例，而是按字删结构词的通病：同样会
    把「上铺／下铺／中午／外语／家里」拆掉。这里逐个钉住原始名词。
    """
    analysis = parse_tieba_request(question)

    # 同长度词的先后顺序由集合遍历决定，这里比较词集本身。
    assert sorted(analysis.topic_terms) == sorted(expected)
    # 检索查询词逐字带上原始名词，不含被拆剩的半截词。
    queries = plan_queries(analysis)
    for query in queries:
        assert "秋节" not in query.split()
    for term in expected:
        assert term in queries[0]
    # 备用查询允许减少放假、条件等泛词，保留完整事件与地点词；没有产生
    # 真正不同的备用表达时，计划不会用装饰词凑一条重复查询。
    if len(queries) > 1:
        for term in expected:
            if term in {"中秋节", "上铺", "下铺", "中午", "外语", "家里", "下课后"}:
                assert term in queries[-1]


def test_holiday_question_requests_official_check_without_inventing_a_year() -> None:
    """放假安排走官方核验路径，且不凭空补年份（原句没有年份就保持没有）。"""
    analysis = parse_tieba_request("华东交通大学吧 中秋节放假")

    assert analysis.needs_official_check is True
    assert "放假" in analysis.official_topics
    assert analysis.time_requirement is None
    assert analysis.time_year is None
    assert all("20" not in query for query in plan_queries(analysis))


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


def test_non_thread_and_duplicate_urls_are_diagnosed() -> None:
    """用户主页与热点页不是帖子：留下剔除依据；同一帖子的多种链接只留一条。"""
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
    kept, rejected, diagnostics = classify_hits_with_diagnostics(hits)

    assert [hit.url for hit in kept] == ["https://tieba.baidu.com/p/10745250786"]
    assert [item.evidence for item in rejected] == [
        REJECT_NOT_A_THREAD,
        REJECT_NOT_A_THREAD,
    ]
    assert diagnostics.raw_hits == 4
    assert diagnostics.not_a_thread == 2
    assert diagnostics.duplicate == 1
    assert diagnostics.usable == 1
    # 兼容入口返回同样两组结果（他吧剔除同样计数准确）。
    assert classify_hits(hits) == (kept, rejected)
