"""画像工单 12：反馈修正闭环在同步控制层的处理。

覆盖验收：
- 事实纠错定点改写原回答采用的条目，旧结论被抑制且不再复活；
- 状态变化没有旧条目可定位时按用户原句记住新状态（真实历史保留）；
- 长期偏好写入长期记忆；本轮要求与表达投诉零写入；
- 指向不明只澄清；单否否定定位不到条目时不猜测写入；
- 目标在生成后被删除/被替代时拒绝定点改写、如实未写入；
- 同一条反馈重放不重复写记忆、不重复记版本；
- 敏感订正文不绕过用途门控。
"""

from __future__ import annotations

from bridges.contracts.atomic_profile import (
    AtomicProfileItemStatus,
    AtomicProfileMemoryBlockedReason,
    AtomicProfileWriteOrigin,
)
from bridges.contracts.feedback_correction import (
    FeedbackAdjustment,
    FeedbackCorrectionKind,
    FeedbackCorrectionRoute,
    FeedbackMemoryKind,
    FeedbackMemoryStatus,
)
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutcome,
    ProfileExtractionStatus,
)
from bridges.profiles import (
    AtomicProfileService,
    AutomaticProfileService,
    FourDimensionProfileService,
    InMemoryAtomicProfileRepository,
    InMemoryAutomaticProfileRepository,
    InMemoryFourDimensionProfileRepository,
    RuleBasedAutomaticProfileExtractor,
)

ACCOUNT = "account-alice"


def _services() -> tuple[AutomaticProfileService, AtomicProfileService]:
    four_dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    atomic = AtomicProfileService(
        four_dimensions, InMemoryAtomicProfileRepository()
    )
    automatic = AutomaticProfileService(
        four_dimension_service=four_dimensions,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
        atomic_profile_service=atomic,
    )
    return automatic, atomic


def _seed(automatic: AutomaticProfileService, content: str, number: int) -> None:
    automatic.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id=f"message-seed-{number}",
        content=content,
        run_id=f"run-seed-{number}",
        mode="companion",
    )


def _feedback(
    automatic: AutomaticProfileService,
    *,
    route: FeedbackCorrectionRoute,
    message_id: str = "message-feedback-1",
    content: str = "之前说错了，我不是大四，是大三。",
    run_id: str = "run-feedback-1",
):
    return automatic.process_synchronous_controls(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id=message_id,
        content=content,
        run_id=run_id,
        mode="companion",
        feedback_route=route,
    )


def _item(atomic: AtomicProfileService, text: str):
    return next(item for item in atomic.list_items(ACCOUNT) if item.text == text)


def test_located_fact_correction_rewrites_and_suppresses_old_value() -> None:
    automatic, atomic = _services()
    _seed(automatic, "我是大四学生", 1)
    target = atomic.list_items(ACCOUNT)[0]
    target_id, target_version, target_text = (
        target.profile_item_id,
        target.version,
        target.text,
    )

    result = _feedback(
        automatic,
        route=FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.FACT_CORRECTION,
            target_item_id=target_id,
            target_item_version=target_version,
            target_item_text=target_text,
            new_fact_text="大三",
            has_replacement=True,
        ),
    )

    assert result is not None and result.feedback is not None
    feedback = result.feedback
    assert feedback.memory_kind is FeedbackMemoryKind.CORRECTION
    assert feedback.memory_status is FeedbackMemoryStatus.WRITTEN
    assert feedback.applied_item_id == target_id
    assert result.run.outcome == ProfileExtractionOutcome.SUCCEEDED_FEEDBACK
    updated = _item(atomic, "大三")
    assert updated.profile_item_id == target_id
    assert updated.version == target_version + 1
    assert updated.user_edited_at is not None
    assert "大四学生" not in {item.text for item in atomic.list_items(ACCOUNT)}

    # 旧结论被抑制：重复抽取旧说法的消息不复活旧条目。
    _seed(automatic, "我是大四学生", 2)
    assert "大四学生" not in {item.text for item in atomic.list_items(ACCOUNT)}


def test_feedback_replay_does_not_write_again() -> None:
    automatic, atomic = _services()
    _seed(automatic, "我是大四学生", 1)
    target = atomic.list_items(ACCOUNT)[0]
    route = FeedbackCorrectionRoute(
        kind=FeedbackCorrectionKind.FACT_CORRECTION,
        target_item_id=target.profile_item_id,
        target_item_version=target.version,
        target_item_text=target.text,
        new_fact_text="大三",
        has_replacement=True,
    )
    _feedback(automatic, route=route)
    after_first = _item(atomic, "大三")

    replay = _feedback(
        automatic, route=route, message_id="message-feedback-1", run_id="run-replay"
    )

    assert replay is not None
    assert replay.feedback is None
    assert replay.run.status == ProfileExtractionStatus.SUCCEEDED
    assert _item(atomic, "大三").version == after_first.version


def test_state_change_without_target_is_remembered() -> None:
    automatic, atomic = _services()

    result = _feedback(
        automatic,
        route=FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.STATE_CHANGE,
            new_fact_text="现在升大四了。",
            has_replacement=True,
        ),
        content="现在升大四了。",
    )

    assert result is not None and result.feedback is not None
    assert result.feedback.memory_kind is FeedbackMemoryKind.STATE_CHANGE
    assert result.feedback.memory_status is FeedbackMemoryStatus.WRITTEN
    stored = _item(atomic, "现在升大四了。")
    assert stored.write_origin is AtomicProfileWriteOrigin.USER


def test_long_term_preference_is_written_and_session_request_is_not() -> None:
    automatic, atomic = _services()

    preference = _feedback(
        automatic,
        route=FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.LONG_TERM_PREFERENCE,
            adjustment=FeedbackAdjustment.CONCLUSION_FIRST,
            preference_text="以后都先给结论。",
        ),
        message_id="message-preference",
        content="以后都先给结论。",
    )
    session = _feedback(
        automatic,
        route=FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.TURN_REQUEST,
            adjustment=FeedbackAdjustment.SHORTER,
        ),
        message_id="message-session",
        content="今天赶时间，简短一点。",
    )

    assert preference is not None and preference.feedback is not None
    assert preference.feedback.memory_status is FeedbackMemoryStatus.WRITTEN
    assert "以后都先给结论。" in {item.text for item in atomic.list_items(ACCOUNT)}
    assert session is not None and session.feedback is not None
    assert session.feedback.memory_status is FeedbackMemoryStatus.NOT_APPLICABLE
    assert [item.text for item in atomic.list_items(ACCOUNT)] == ["以后都先给结论。"]


def test_expression_complaint_and_unclear_write_nothing() -> None:
    automatic, atomic = _services()

    expression = _feedback(
        automatic,
        route=FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.EXPRESSION_QUALITY,
            adjustment=FeedbackAdjustment.SIMPLER,
        ),
        content="身份没错，但解释太难了。",
    )
    unclear = _feedback(
        automatic,
        route=FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.UNCLEAR,
            requires_clarification=True,
        ),
        message_id="message-unclear",
        content="不是这个意思。",
    )

    assert expression is not None and expression.feedback is not None
    assert expression.feedback.memory_status is FeedbackMemoryStatus.NOT_APPLICABLE
    assert unclear is not None and unclear.feedback is not None
    assert unclear.feedback.clarification_required is True
    assert unclear.feedback.memory_status is FeedbackMemoryStatus.NOT_APPLICABLE
    assert atomic.list_items(ACCOUNT) == []


def test_unlocated_negation_only_correction_stays_unresolved() -> None:
    automatic, atomic = _services()

    result = _feedback(
        automatic,
        route=FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.FACT_CORRECTION,
            new_fact_text="我不是艺术生",
            has_replacement=False,
        ),
        content="你记错了，我不是艺术生。",
    )

    assert result is not None and result.feedback is not None
    assert result.feedback.memory_status is FeedbackMemoryStatus.UNRESOLVED
    assert result.feedback.applied_item_id is None
    assert atomic.list_items(ACCOUNT) == []


def test_deleted_target_is_reported_not_guessed() -> None:
    automatic, atomic = _services()
    _seed(automatic, "我是大四学生", 1)
    target = atomic.list_items(ACCOUNT)[0]
    atomic.delete_item(ACCOUNT, target.profile_item_id, target.version)

    result = _feedback(
        automatic,
        route=FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.FACT_CORRECTION,
            target_item_id=target.profile_item_id,
            target_item_version=target.version,
            target_item_text=target.text,
            new_fact_text="大三",
            has_replacement=True,
        ),
    )

    assert result is not None and result.feedback is not None
    assert result.feedback.memory_status is FeedbackMemoryStatus.NO_ACTIVE_RECORD
    assert result.feedback.applied_item_id is None
    assert atomic.list_items(ACCOUNT) == []


def test_stale_version_is_reported_as_failed() -> None:
    automatic, atomic = _services()
    _seed(automatic, "我是大四学生", 1)
    target = atomic.list_items(ACCOUNT)[0]
    target_id, target_version, target_text = (
        target.profile_item_id,
        target.version,
        target.text,
    )
    atomic.remember(ACCOUNT, "我是大四学生")  # 版本 +1，模拟生成期间的新写入

    result = _feedback(
        automatic,
        route=FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.FACT_CORRECTION,
            target_item_id=target_id,
            target_item_version=target_version,
            target_item_text=target_text,
            new_fact_text="大三",
            has_replacement=True,
        ),
    )

    assert result is not None and result.feedback is not None
    assert result.feedback.memory_status is FeedbackMemoryStatus.FAILED
    assert result.run.outcome == ProfileExtractionOutcome.FEEDBACK_FAILED
    unchanged = next(
        item for item in atomic.list_items(ACCOUNT) if item.profile_item_id == target_id
    )
    assert unchanged.text == "我是大四学生"
    assert unchanged.status is AtomicProfileItemStatus.ACTIVE


def test_sensitive_replacement_does_not_bypass_purpose_gate() -> None:
    automatic, atomic = _services()
    _seed(automatic, "我是大四学生", 1)
    target = atomic.list_items(ACCOUNT)[0]

    result = _feedback(
        automatic,
        route=FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.FACT_CORRECTION,
            target_item_id=target.profile_item_id,
            target_item_version=target.version,
            target_item_text=target.text,
            new_fact_text="我有抑郁症",
            has_replacement=True,
        ),
        content="你记错了，我不是大四学生，我有抑郁症。",
    )

    assert result is not None and result.feedback is not None
    assert result.feedback.memory_status is FeedbackMemoryStatus.UNRESOLVED
    assert (
        result.feedback.blocked_reason
        is AtomicProfileMemoryBlockedReason.PURPOSE_REQUIRED
    )
    assert result.run.source_snapshot == ""
    assert _item(atomic, "大四学生").text == "大四学生"
