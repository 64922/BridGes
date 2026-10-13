"""用户第二次反馈：原始通知的列表必须随引导句一同交付。"""

from bridges.tieba.official import extract_page_text, locate_excerpt
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import plan_queries


def test_holiday_notice_includes_the_arrangements_after_the_intro() -> None:
    # 来自 2026 年通知的公开正文结构，导航与下一篇放在正文容器之外。
    html = """<html><body><nav>开放时间</nav><div id="vsb_content">
    <div class="v_news_content"><p>各位读者：</p>
    <p>根据学校通知，图书馆中秋节国庆节假期开放时间安排如下：</p>
    <p>1.9月25日、10月1日至3日，南馆借阅室、北馆二层及以上借阅室开放时间为8:00-17:00，南、北馆各自习室开放时间为7:00-22:00；</p>
    <p>2.9月26日至30日、10月4日至7日，借阅室开放时间为8:00-22:00。</p>
    <p>4.网络电子文献每天24小时开放。</p></div></div>
    <p>下一篇：其他通知</p></body></html>"""
    excerpt, _ = locate_excerpt(extract_page_text(html), ("国庆节", "假期"))
    assert excerpt is not None
    assert "10月1日至3日" in excerpt and "8:00-17:00" in excerpt
    assert "10月4日至7日" in excerpt and "24小时" in excerpt
    assert "下一篇" not in excerpt


def test_holiday_fallback_covers_common_forum_wording() -> None:
    queries = plan_queries(parse_tieba_request("我想了解国庆节假期的讨论"))
    assert len(queries) == 2
    assert "国庆" in queries[1].split()
    assert "site:tieba.baidu.com/p/" in queries[1]


def test_official_only_is_partial_delivery_with_a_specific_discussion_gap() -> None:
    from bridges.chat.turn_result import derive_turn_result
    from bridges.contracts.chat import ChatMessageStatus, TurnOutcome

    result = derive_turn_result(
        status=ChatMessageStatus.DONE,
        projections={
            "tieba_research": {
                "status": "empty",
                "official_checks": [
                    {
                        "status": "verified",
                        "excerpt": "10月1日至3日，开放时间8:00-17:00。",
                        "applicability": {"applicable": True},
                    }
                ],
            }
        },
    )
    assert result.outcome is TurnOutcome.PARTIAL
    assert result.delivered and result.delivered[0].module_id == "tieba"
    assert any("讨论" in gap for gap in result.gaps)


def test_old_holiday_notice_is_not_applicable_without_an_explicit_year() -> None:
    from datetime import UTC, datetime

    from bridges.tieba.contracts import TiebaOfficialCheck
    from bridges.tieba.evidence_official import assess_applicability

    check = TiebaOfficialCheck(
        title="关于2018年国庆节图书馆开放安排的通知-华东交通大学",
        url="https://lib.ecjtu.edu.cn/info/1076/1798.htm",
        host="lib.ecjtu.edu.cn",
        status="verified",
        fetched_at=datetime(2026, 10, 7, tzinfo=UTC),
        excerpt="国庆节期间图书馆开放时间为8:00-17:00。",
        matched_terms=["国庆节"],
    )
    applicability = assess_applicability(check, parse_tieba_request("我想了解国庆节假期的讨论"))
    assert not applicability.applicable
    assert "历史" in applicability.note


def test_other_subject_in_the_right_forum_is_not_a_holiday_discussion() -> None:
    from datetime import UTC, datetime

    from bridges.tieba.contracts import ReadStatus, TiebaReadResult
    from bridges.tieba.relevance import post_matches_topic

    post = TiebaReadResult(
        url="https://tieba.baidu.com/p/10745250786",
        status=ReadStatus.READ,
        forum_name="华东交通大学吧",
        title="东北电力和华东交通哪个好",
        retrieved_at=datetime.now(UTC),
    )
    analysis = parse_tieba_request("我想了解国庆节假期的讨论")
    assert not post_matches_topic(post, analysis)
    assert post_matches_topic(post.model_copy(update={"title": "国庆放几天"}), analysis)


def test_holiday_selection_does_not_mix_current_and_dated_historical_notices() -> None:
    from bridges.contracts.modules import ModuleQueryStatus
    from bridges.tieba.contracts import TiebaSearchHit
    from bridges.tieba.evidence_official import official_candidates
    from bridges.tieba.searching import SearchOutcome, query_record

    hits = tuple(
        TiebaSearchHit(
            title=f"关于{year}年国庆节图书馆开放安排",
            snippet="",
            url=f"https://lib.ecjtu.edu.cn/info/1076/{year}.htm",
        )
        for year in (2018, 2026)
    )
    outcome = SearchOutcome(
        record=query_record(query="国庆节", status=ModuleQueryStatus.SUCCESS, evidence_count=2),
        hits=hits,
        candidates=(),
        rejected=(),
    )
    assert official_candidates(outcome, ("国庆节", "假期"), preferred_year=2026) == [hits[1].url]
