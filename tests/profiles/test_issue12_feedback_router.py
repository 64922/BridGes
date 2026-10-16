"""画像工单 12：确定性反馈路由单元测试。

覆盖验收：
- 事实纠错定位到原回答实际采用的条目并带回用户明确的新值；
- 「今天简短」只算本轮要求，「以后先给结论」才算长期偏好；
- 表达投诉只路由为表达质量调整，不落到事实纠正；
- 「不是这个意思」等指向不明只标记澄清，不猜测条目、不生成新值；
- 普通新话题不误判为反馈，没有前置回答不路由。
"""

from __future__ import annotations

from bridges.contracts.feedback_correction import (
    FeedbackAdjustment,
    FeedbackCorrectionKind,
)
from bridges.profiles.feedback import AdoptedFeedbackTarget, route_feedback

ADOPTED = (
    AdoptedFeedbackTarget(
        profile_item_id="item-grade",
        version=3,
        text="我是在读大四学生",
        relation="grade",
    ),
    AdoptedFeedbackTarget(
        profile_item_id="item-degree",
        version=2,
        text="学历为研究生",
        relation="identity",
    ),
    AdoptedFeedbackTarget(
        profile_item_id="item-hobby",
        version=1,
        text="喜欢跑步",
        relation="interest",
    ),
)


def _route(text: str, *, previous: str | None = "上一轮回答"):
    return route_feedback(text, previous_answer=previous, adopted_items=ADOPTED)


def test_fact_correction_locates_adopted_item_and_new_value() -> None:
    route = _route("你记错了，我不是研究生，我是本科生。")
    assert route is not None
    assert route.kind is FeedbackCorrectionKind.FACT_CORRECTION
    assert route.target_item_id == "item-degree"
    assert route.target_item_version == 2
    assert route.new_fact_text == "我是本科生"
    assert route.has_replacement
    assert not route.requires_clarification


def test_fact_correction_grade_value_keeps_user_wording() -> None:
    route = _route("之前说错了，我不是大四，是大三。")
    assert route is not None
    assert route.kind is FeedbackCorrectionKind.FACT_CORRECTION
    assert route.target_item_id == "item-grade"
    assert route.new_fact_text == "大三"


def test_state_change_without_prior_adoption_keeps_whole_request() -> None:
    route = _route("现在升大四了。")
    assert route is not None
    assert route.kind is FeedbackCorrectionKind.STATE_CHANGE
    assert route.new_fact_text == "现在升大四了。"
    assert route.has_replacement


def test_state_change_locates_stale_grade_item() -> None:
    adopted = (
        AdoptedFeedbackTarget(
            profile_item_id="item-junior",
            version=1,
            text="我是在读大三学生",
            relation="grade",
        ),
    )
    route = route_feedback(
        "现在升大四了。", previous_answer="上一轮回答", adopted_items=adopted
    )
    assert route is not None
    assert route.kind is FeedbackCorrectionKind.STATE_CHANGE
    assert route.target_item_id == "item-junior"
    assert route.new_fact_text == "现在升大四了。"


def test_turn_request_stays_turn_scoped() -> None:
    route = _route("今天赶时间，简短一点。")
    assert route is not None
    assert route.kind is FeedbackCorrectionKind.TURN_REQUEST
    assert route.adjustment is FeedbackAdjustment.SHORTER
    assert route.preference_text is None


def test_long_term_preference_is_routed_with_preference_text() -> None:
    route = _route("以后都先给结论。")
    assert route is not None
    assert route.kind is FeedbackCorrectionKind.LONG_TERM_PREFERENCE
    assert route.adjustment is FeedbackAdjustment.CONCLUSION_FIRST
    assert route.preference_text == "以后都先给结论。"

    tables = _route("以后别再用表格了。")
    assert tables is not None
    assert tables.kind is FeedbackCorrectionKind.LONG_TERM_PREFERENCE
    assert tables.adjustment is FeedbackAdjustment.NO_TABLES


def test_expression_complaint_does_not_route_as_fact() -> None:
    route = _route("身份没错，但解释太难了。")
    assert route is not None
    assert route.kind is FeedbackCorrectionKind.EXPRESSION_QUALITY
    assert route.adjustment is FeedbackAdjustment.SIMPLER
    assert route.target_item_id is None
    assert route.new_fact_text is None


def test_turn_and_expression_do_not_apply_future_scope() -> None:
    route = _route("这次只说重点，下次继续详细讲。")
    assert route is not None
    assert route.kind is FeedbackCorrectionKind.TURN_REQUEST
    assert route.preference_text is None


def test_vague_negation_only_requests_clarification() -> None:
    route = _route("不是这个意思。")
    assert route is not None
    assert route.kind is FeedbackCorrectionKind.UNCLEAR
    assert route.requires_clarification
    assert route.target_item_id is None
    assert route.new_fact_text is None
    assert not route.produces_corrected_answer


def test_blame_without_object_requests_clarification() -> None:
    route = _route("你记错了。")
    assert route is not None
    assert route.kind is FeedbackCorrectionKind.UNCLEAR
    assert route.requires_clarification


def test_ordinary_statement_is_not_feedback() -> None:
    assert _route("我喜欢跑步，也喜欢游泳。") is None
    assert _route("我准备考研，最近也在找工作。") is None
    assert _route("我没有考上研究生，准备再考一次。") is None
    assert _route("帮我看看这份实习要不要接。") is None


def test_without_previous_answer_no_feedback_route() -> None:
    assert _route("不是这个意思。", previous=None) is None
    assert _route("以后都先给结论。", previous=None) is None


def test_correction_target_not_found_without_replacement_stays_unresolved() -> None:
    route = _route("你记错了，我不是艺术生。")
    assert route is not None
    assert route.kind in {
        FeedbackCorrectionKind.FACT_CORRECTION,
        FeedbackCorrectionKind.UNCLEAR,
    }
    if route.kind is FeedbackCorrectionKind.FACT_CORRECTION:
        assert route.target_item_id is None
        assert route.new_fact_text == "我不是艺术生"
        # 单纯否定旧值、又定位不到条目：不得当作新事实写入。
        assert not route.has_replacement
    else:
        assert route.requires_clarification
