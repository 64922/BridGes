"""改进工单 19：用途明确的完整画像切片（确定性机制测试）。

覆盖：无词面交集的默认偏好与背景按用途召回；无关爱好不注入；现实约束
产生可执行计划依据；本轮明确要求覆盖默认偏好且不改长期值；完整尾部条件
不被截断；同轮冻结版本与删除后的下次重筛；账户隔离与关画像基线。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bridges.contracts.atomic_profile import (
    AtomicProfileFactRelation,
    AtomicProfileItemStatus,
)
from bridges.contracts.profile_adoption import (
    AdoptedProfileSlice,
    ProfileSlicePurpose,
    ProfileTaskKind,
)
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.purpose import (
    DECISION_EXPLANATION_START,
    DECISION_PLAN_TIME,
    build_purpose,
    classify_task_kind,
    detect_explicit_request,
    is_overridden,
)

ALICE = "account-alice"
BOB = "account-bob"
ANCHOR = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _atomic() -> AtomicProfileService:
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    return AtomicProfileService(four, InMemoryAtomicProfileRepository())


def _services() -> tuple[FourDimensionProfileService, AtomicProfileService]:
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    return four, AtomicProfileService(four, InMemoryAtomicProfileRepository())


def _purpose(query: str, *, mode: str = "companion") -> ProfileSlicePurpose:
    return build_purpose(mode=mode, query=query)


def _adopted_texts(slice_: AdoptedProfileSlice) -> list[str]:
    return [item.fact_text for item in slice_.adopted_items]


def _excluded_reasons(slice_: AdoptedProfileSlice) -> dict[str, str]:
    return {item.fact_text: item.exclusion_reason for item in slice_.excluded_items}


# ---------------------------------------------------------------------------
# 规则本身
# ---------------------------------------------------------------------------


def test_task_kind_and_explicit_request_classification() -> None:
    assert classify_task_kind("解释一下贝叶斯定理") is ProfileTaskKind.EXPLAIN
    assert classify_task_kind("帮我安排一周的复习计划") is ProfileTaskKind.PLAN
    assert classify_task_kind("推荐几本概率入门书") is ProfileTaskKind.RECOMMEND
    assert classify_task_kind("帮我出几道题练习") is ProfileTaskKind.PRACTICE
    assert classify_task_kind("帮我复盘这次考试") is ProfileTaskKind.REVIEW
    assert classify_task_kind("今天天气不错") is ProfileTaskKind.GENERAL

    assert detect_explicit_request("直接给详细推导") == "detailed"
    assert detect_explicit_request("请简短回答") == "concise"
    assert detect_explicit_request("只给答案，不要解释") == "answer_only"
    assert detect_explicit_request("正常讲讲就行") is None

    # 本轮明确要求覆盖默认：详细推导覆盖“简短”和“先例子”的默认。
    from bridges.profiles.purpose import expression_preference_tags

    tags = expression_preference_tags("我喜欢简短回答")
    assert is_overridden(tags, "detailed") is True
    assert is_overridden(tags, "concise") is False


# ---------------------------------------------------------------------------
# 验收 1：贝叶斯问题可取入门基础与先例后公式偏好，无关跑步爱好不注入
# ---------------------------------------------------------------------------


def test_bayesian_question_adopts_background_and_preference_without_word_overlap():
    service = _atomic()
    service.remember(ALICE, "我正在学习概率")
    service.remember(ALICE, "我喜欢先看例子再看公式")
    service.remember(ALICE, "我喜欢跑步")

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=_purpose("解释贝叶斯定理"),
        run_id="assistant-1",
    )

    adopted = set(_adopted_texts(slice_))
    assert adopted == {"我正在学习概率", "我喜欢先看例子再看公式"}
    assert _excluded_reasons(slice_)["我喜欢跑步"] == "与当前问题无关"

    by_text = {item.fact_text: item for item in slice_.adopted_items}
    background = by_text["我正在学习概率"]
    assert background.relation is AtomicProfileFactRelation.LEARNING
    assert DECISION_EXPLANATION_START in background.applicable_to
    preference = by_text["我喜欢先看例子再看公式"]
    assert "explanation_order" in preference.applicable_to
    assert preference.is_default is True


def test_compile_chat_slice_is_purpose_aware_and_keeps_legacy_no_question_mode():
    service = _atomic()
    service.remember(ALICE, "我正在学习概率")
    service.remember(ALICE, "我喜欢先看例子再看公式")
    service.remember(ALICE, "我喜欢跑步")

    slice_ = service.compile_chat_slice(
        ALICE, run_id="assistant-1", current_question="解释贝叶斯定理"
    )
    included = {item.value_or_rule for item in slice_.included_items}
    assert included == {"我正在学习概率", "我喜欢先看例子再看公式"}
    reasons = {
        item.value_or_rule: item.exclusion_reason for item in slice_.unused_items
    }
    assert reasons["我喜欢跑步"] == "与当前问题无关"

    # 没有当前问题时保持原行为：全部有效条目进入候选（零条也合法）。
    no_question = service.compile_chat_slice(ALICE, run_id="assistant-2")
    assert len(no_question.included_items) == 3


# ---------------------------------------------------------------------------
# 验收 2：每天 30 分钟约束产生可执行计划；详细推导覆盖简短默认且不改长期值
# ---------------------------------------------------------------------------


def test_plan_adopts_time_constraint_goal_and_default_preference():
    service = _atomic()
    service.remember(ALICE, "我每天能学习30分钟")
    service.remember(ALICE, "我的目标是今年通过雅思考试")
    service.remember(ALICE, "我喜欢简短回答")

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=_purpose("帮我制定一周的雅思学习计划"),
        run_id="assistant-plan",
    )

    assert set(_adopted_texts(slice_)) == {
        "我每天能学习30分钟",
        "我的目标是今年通过雅思考试",
        "我喜欢简短回答",
    }
    by_text = {item.fact_text: item for item in slice_.adopted_items}
    constraint = by_text["我每天能学习30分钟"]
    assert DECISION_PLAN_TIME in constraint.applicable_to
    assert any("无明确期限" in condition for condition in constraint.conditions)


def test_detailed_request_overrides_brevity_default_for_this_turn_only():
    service = _atomic()
    item = service.remember(ALICE, "我喜欢简短回答")

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=_purpose("直接给详细推导"),
        run_id="assistant-detail",
        now=ANCHOR,
    )

    assert _adopted_texts(slice_) == []
    reasons = _excluded_reasons(slice_)
    assert "本轮明确要求优先" in reasons["我喜欢简短回答"]

    # 长期值不被改写：正文与版本保持不变，下一轮仍可召回。
    reloaded = service.get_item(ALICE, item.profile_item_id)
    assert reloaded.text == "我喜欢简短回答"
    assert reloaded.version == item.version

    normal = service.compile_adopted_slice(
        ALICE,
        purpose=_purpose("解释一下概率"),
        run_id="assistant-normal",
        now=ANCHOR,
    )
    assert _adopted_texts(normal) == ["我喜欢简短回答"]


# ---------------------------------------------------------------------------
# 验收 3：完整尾部条件保留；预算不足整条排除
# ---------------------------------------------------------------------------


def test_complete_tail_condition_survives_slice_compilation():
    service = _atomic()
    prefix = "学习讲解时先给一个完整直观的例子并说明具体条件。" * 4
    full_text = prefix + "但考试冲刺时不要使用这种方式。"
    service.remember(ALICE, full_text)

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=_purpose("学习讲解时怎么安排顺序"),
        run_id="assistant-tail",
    )

    assert _adopted_texts(slice_) == [full_text]
    assert slice_.adopted_items[0].fact_text.endswith("但考试冲刺时不要使用这种方式。")


# ---------------------------------------------------------------------------
# 验收 4：异步未完成不等待、同轮版本冻结、删除后下一次重新筛选
# ---------------------------------------------------------------------------


def test_same_turn_snapshot_is_frozen_for_later_nodes():
    service = _atomic()
    service.remember(ALICE, "我正在学习概率")
    slice_ = service.compile_adopted_slice(
        ALICE, purpose=_purpose("解释贝叶斯定理"), run_id="assistant-1", now=ANCHOR
    )
    version = slice_.revocation_version
    assert version is not None

    # 生成中途后台完成了一次新提取：不重新查询，采用结果不变。
    service.remember(ALICE, "我现在也喜欢用图示", source_message_id="m-late")
    assert _adopted_texts(slice_) == ["我正在学习概率"]
    assert slice_.select_subset() == slice_.adopted_items
    selectable = {
        item.fact_text for item in slice_.select_subset("explanation_start")
    }
    assert "我正在学习概率" in selectable

    # 冻结版本与当前版本分开：新提取使旧切片可被判定为过期，由调用方重编译。
    assert service.is_slice_current(ALICE, version) is False


def test_same_turn_automatic_extraction_waits_for_next_turn():
    four, service = _services()
    record = four.upsert_automatic_record(
        ALICE,
        dimension=FourDimension.STAGE_GOAL,
        content="今年通过雅思考试",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
        evidence_message_id="message-1",
    )
    service.mirror_record(
        ALICE,
        record,
        fact_text="我的目标是今年通过雅思考试",
        evidence_message_id="message-1",
    )

    this_turn = service.compile_adopted_slice(
        ALICE,
        purpose=_purpose("帮我安排雅思考试的复习计划"),
        run_id="assistant-1",
        current_user_message_id="message-1",
        now=ANCHOR,
    )
    assert this_turn.adopted_items == []
    assert all(
        "下一轮" in item.exclusion_reason for item in this_turn.excluded_items
    )

    next_turn = service.compile_adopted_slice(
        ALICE,
        purpose=_purpose("帮我安排雅思考试的复习计划"),
        run_id="assistant-2",
        current_user_message_id="message-2",
        now=ANCHOR,
    )
    assert _adopted_texts(next_turn) == ["我的目标是今年通过雅思考试"]


def test_deletion_is_rescreened_on_the_next_compilation():
    service = _atomic()
    item = service.remember(ALICE, "我正在学习概率")
    first = service.compile_adopted_slice(
        ALICE, purpose=_purpose("解释贝叶斯定理"), run_id="assistant-1"
    )
    assert _adopted_texts(first) == ["我正在学习概率"]

    service.delete_item(ALICE, item.profile_item_id, item.version)
    assert (
        service.get_item(ALICE, item.profile_item_id).status
        is AtomicProfileItemStatus.WITHDRAWN
    )
    second = service.compile_adopted_slice(
        ALICE, purpose=_purpose("解释贝叶斯定理"), run_id="assistant-2"
    )
    assert second.adopted_items == []


# ---------------------------------------------------------------------------
# 验收 5/6：零条安全基线、整条预算与账户隔离
# ---------------------------------------------------------------------------


def test_empty_profile_yields_zero_adopted_items_safely():
    service = _atomic()
    slice_ = service.compile_adopted_slice(
        ALICE, purpose=_purpose("解释贝叶斯定理"), run_id="assistant-1"
    )
    assert slice_.adopted_items == []
    assert slice_.excluded_items == []


def test_adopted_slice_is_account_isolated():
    service = _atomic()
    service.remember(ALICE, "我正在学习概率")
    service.remember(BOB, "我正在学习统计")

    alice_slice = service.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="解释概率"),
        run_id="run-a",
    )
    bob_slice = service.compile_adopted_slice(
        BOB,
        purpose=build_purpose(mode="companion", query="解释概率"),
        run_id="run-b",
    )
    assert _adopted_texts(alice_slice) == ["我正在学习概率"]
    assert _adopted_texts(bob_slice) == ["我正在学习统计"]


def test_single_value_attribute_updates_are_reflected_in_slice():
    service = _atomic()
    first = service.remember(ALICE, "我是大二学生")
    service.remember(ALICE, "我是大三学生")

    slice_ = service.compile_adopted_slice(
        ALICE, purpose=_purpose("解释贝叶斯定理"), run_id="assistant-1"
    )
    assert _adopted_texts(slice_) == ["我是大三学生"]
    assert service.get_item(ALICE, first.profile_item_id).status is (
        AtomicProfileItemStatus.SUPERSEDED
    )


def test_expired_item_is_excluded_with_reason():
    service = _atomic()
    item = service.remember(ALICE, "我下周有考试", source_at=ANCHOR - timedelta(days=30))
    assert item.valid_until is not None

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=_purpose("帮我准备考试"),
        run_id="assistant-1",
        now=ANCHOR,
    )
    assert slice_.adopted_items == []
    assert any(
        "已过期" in item.exclusion_reason for item in slice_.excluded_items
    )
