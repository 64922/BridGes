"""Issue 20：真实闭环评测回传项的最小回归。

覆盖偏好不覆盖身份、同会话反馈定位、affirm-then-negate、生命周期信号与
同句新事实共存、跑步活动合并、一次性拒绝、抽取预算错误可重试。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from bridges.ai import ModelGateway
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.atomic_profile import (
    AtomicProfileFactRelation,
    AtomicProfileGoalState,
    AtomicProfileItemStatus,
)
from bridges.contracts.feedback_correction import (
    FeedbackAdjustment,
    FeedbackCorrectionKind,
    FeedbackCorrectionRoute,
    FeedbackMemoryStatus,
)
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutcome,
    ProfileExtractionOutput,
    ProfileExtractionStatus,
)
from bridges.contracts.profiles import FourDimension
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    parse_explicit_change,
    parse_fact_identity,
)
from bridges.profiles.automatic import (
    AutomaticProfileError,
    AutomaticProfileService,
    GatewayAutomaticProfileExtractor,
    InMemoryAutomaticProfileRepository,
)
from bridges.profiles.commit import ProfileFactSubmission
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.signals import ProfileSignalClassifier

ACCOUNT = "account-alice"
ANCHOR = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


class _BudgetGateway:
    def invoke(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(
            status=ModelCallStatus.BLOCKED,
            output=None,
            error_code="output_budget_exceeded",
            error_message="profile extraction output budget exceeded",
            lock=None,
        )


def _services() -> tuple[
    AtomicProfileService,
    AutomaticProfileService,
    InMemoryAtomicProfileRepository,
]:
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    repository = InMemoryAtomicProfileRepository()
    atomic = AtomicProfileService(four, repository)
    automatic = AutomaticProfileService(
        four_dimension_service=four,
        repository=InMemoryAutomaticProfileRepository(),
        atomic_profile_service=atomic,
    )
    return atomic, automatic, repository


def test_communication_preference_does_not_supersede_identity() -> None:
    atomic, automatic, _ = _services()
    atomic.remember(ACCOUNT, "计算机专业", source_message_id="m-major")
    atomic.remember(ACCOUNT, "大二", source_message_id="m-grade")
    identities = {
        item.text
        for item in atomic.list_items(ACCOUNT)
        if item.fact_relation
        in {AtomicProfileFactRelation.MAJOR, AtomicProfileFactRelation.GRADE}
    }
    assert identities == {"计算机专业", "大二"}

    preference_text = "这个专业以后先给我结论，再展开解释。"
    result = automatic.process_synchronous_controls(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="m-preference",
        content=preference_text,
        run_id="run-preference",
        mode="companion",
        feedback_route=FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.LONG_TERM_PREFERENCE,
            adjustment=FeedbackAdjustment.CONCLUSION_FIRST,
            preference_text=preference_text,
        ),
    )

    assert result is not None and result.feedback is not None
    assert result.feedback.memory_status is FeedbackMemoryStatus.WRITTEN
    active = {item.text: item for item in atomic.list_items(ACCOUNT)}
    assert identities <= set(active)
    assert all(active[text].status is AtomicProfileItemStatus.ACTIVE for text in identities)
    assert any("以后" in text and "结论" in text for text in active)
    assert parse_fact_identity(preference_text).relation is (
        AtomicProfileFactRelation.STATEMENT
    )


def test_future_study_goal_is_not_misread_as_communication_preference() -> None:
    identity = parse_fact_identity("我以后想详细研究机器学习")
    assert identity.relation is AtomicProfileFactRelation.RESEARCH


def test_incidental_activity_mentions_are_not_activity_identity() -> None:
    assert parse_fact_identity("跑步伤膝盖").relation is (
        AtomicProfileFactRelation.STATEMENT
    )
    assert parse_fact_identity("我每天跑步时听音乐").relation is (
        AtomicProfileFactRelation.STATEMENT
    )


def test_affirm_then_negate_correction_supersedes_old_value() -> None:
    atomic, _, repository = _services()
    old = atomic.commit_fact(
        ACCOUNT,
        ProfileFactSubmission(
            text="我每天晚上有三个小时可以学习",
            evidence_message_id="m-old",
        ),
    )
    assert old is not None

    correction = "你记错了，我每晚只有半小时学线性代数，不是三小时。"
    assert parse_explicit_change(correction) == (
        ["三小时"],
        "我每晚只有半小时学线性代数",
    )
    new = atomic.commit_fact(
        ACCOUNT,
        ProfileFactSubmission(
            text="我每晚只有半小时学习线性代数",
            evidence_quote="我每晚只有半小时学习线性代数",
            evidence_message_id="m-new",
            source_text=correction,
        ),
    )

    assert new is not None
    assert [item.text for item in atomic.list_items(ACCOUNT)] == [
        "我每晚只有半小时学习线性代数"
    ]
    retired = next(
        item
        for item in repository.list_items(ACCOUNT, include_withdrawn=True)
        if item.profile_item_id == old.profile_item_id
    )
    assert retired.status is AtomicProfileItemStatus.SUPERSEDED
    assert retired.superseded_by_id == new.profile_item_id


def test_correction_source_text_only_applies_to_replacement_candidate() -> None:
    atomic, _, _ = _services()
    old = atomic.commit_fact(
        ACCOUNT,
        ProfileFactSubmission(
            text="我是研究生",
            dimension=FourDimension.ACADEMIC_STATUS,
            evidence_message_id="m-old-identity",
        ),
    )
    assert old is not None

    unrelated = atomic.commit_fact(
        ACCOUNT,
        ProfileFactSubmission(
            text="我每天晚上有三个小时可以学习",
            evidence_message_id="m-time",
            source_text="你记错了，我不是研究生，我是本科生。",
        ),
    )

    assert unrelated is not None
    assert old.profile_item_id in {
        item.profile_item_id for item in atomic.list_items(ACCOUNT)
    }


class _MixedSignalExtractor:
    version = "issue20-mixed-v1"

    def extract(
        self,
        *,
        message_id: str,
        content: str,
        **_ignored: object,
    ) -> ProfileExtractionOutput:
        value = "考研英语" if "考研英语" in content else "期末考试"
        return ProfileExtractionOutput.model_validate(
            {
                "items": [
                    {
                        "dimension": FourDimension.STAGE_GOAL.value,
                        "normalized_value": value,
                        "fact_text": value,
                        "evidence_ref": message_id,
                        "reliability": 0.9,
                        "action": "create",
                    }
                ]
            }
        )


class _MixedHarness:
    def __init__(self, messages: dict[str, Any]) -> None:
        four = FourDimensionProfileService(
            InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
        )
        self.atomic = AtomicProfileService(
            four, InMemoryAtomicProfileRepository()
        )
        self.service = AutomaticProfileService(
            four_dimension_service=four,
            repository=InMemoryAutomaticProfileRepository(),
            extractor=_MixedSignalExtractor(),
            message_reader=lambda account_id, message_id: messages.get(message_id),
            atomic_profile_service=self.atomic,
        )

    def preprocess(self, message_id: str, content: str) -> Any:
        return self.service.preprocess_message(
            ACCOUNT,
            conversation_id="conversation-1",
            message_id=message_id,
            content=content,
            run_id=f"run-{message_id}",
            mode="study",
        )

    def schedule(self, message_id: str, content: str) -> Any:
        return self.service.schedule_message_extraction(
            ACCOUNT,
            conversation_id="conversation-1",
            message_id=message_id,
            content=content,
            run_id=f"run-{message_id}",
        )

    def retry(self) -> str:
        return self.service.run_retry_tick()


@pytest.mark.parametrize(
    ("signal_text", "setup_contents", "expected_state"),
    [
        ("先暂停考研英语，我要准备期末考试。", (), AtomicProfileGoalState.PAUSED),
        ("先暂停考研英语然后我要准备期末考试。", (), AtomicProfileGoalState.PAUSED),
        ("考研英语考完了，我要准备期末考试。", (), AtomicProfileGoalState.COMPLETED),
        (
            "继续考研英语，我要准备期末考试。",
            (("m-pause", "先暂停考研英语"),),
            AtomicProfileGoalState.ACTIVE,
        ),
    ],
)
def test_lifecycle_signal_keeps_same_sentence_fact(
    signal_text: str,
    setup_contents: tuple[tuple[str, str], ...],
    expected_state: AtomicProfileGoalState,
) -> None:
    messages = {
        "m-goal": SimpleNamespace(content="我在准备考研英语", created_at=ANCHOR),
        "m-mixed": SimpleNamespace(content=signal_text, created_at=ANCHOR),
    }
    for message_id, content in setup_contents:
        messages[message_id] = SimpleNamespace(content=content, created_at=ANCHOR)
    harness = _MixedHarness(messages)
    harness.preprocess("m-goal", "我在准备考研英语")
    for message_id, content in setup_contents:
        harness.preprocess(message_id, content)

    result = harness.preprocess("m-mixed", signal_text)

    assert result.run.outcome is ProfileExtractionOutcome.SUCCEEDED_LIFECYCLE_SIGNAL
    goal = next(
        item
        for item in harness.atomic.list_items(ACCOUNT)
        if item.fact_object == "考研英语"
    )
    assert goal.goal_state is expected_state

    run = harness.schedule("m-mixed", signal_text)
    assert run is not None and run.status is ProfileExtractionStatus.PENDING
    harness.retry()

    texts = {item.text for item in harness.atomic.list_items(ACCOUNT)}
    assert "期末考试" in texts
    assert goal.goal_state is expected_state


def test_running_activity_items_merge_and_keep_sources() -> None:
    atomic, _, _ = _services()

    atomic.remember(ACCOUNT, "我喜欢跑步。", source_message_id="m-run-1")
    atomic.remember(ACCOUNT, "我基本每周三次慢跑。", source_message_id="m-run-2")
    atomic.remember(ACCOUNT, "我常去慢跑。", source_message_id="m-run-3")

    items = atomic.list_items(ACCOUNT)
    assert len(items) == 1
    item = items[0]
    assert item.fact_relation is AtomicProfileFactRelation.INTEREST
    assert item.fact_object == "跑步"
    assert set(item.source_message_ids) == {"m-run-1", "m-run-2", "m-run-3"}


def test_one_off_refusal_is_not_written_as_long_term_profile() -> None:
    atomic, _, _ = _services()
    classifier = ProfileSignalClassifier()
    refusal = "我不用这个刷题计划。"
    classification = classifier.extraction_classification(refusal)

    assert classifier.is_schedulable(refusal, classification) is False
    assert (
        atomic.commit_fact(ACCOUNT, ProfileFactSubmission(text=refusal)) is None
    )
    assert atomic.list_items(ACCOUNT) == []

    stable_preference = atomic.commit_fact(
        ACCOUNT, ProfileFactSubmission(text="我不吃辣。")
    )
    assert stable_preference is not None


def test_output_budget_exceeded_is_retryable() -> None:
    with pytest.raises(AutomaticProfileError) as exc_info:
        GatewayAutomaticProfileExtractor(
            cast(ModelGateway, _BudgetGateway())
        ).extract(
            account_id=ACCOUNT,
            conversation_id="conversation-1",
            message_id="m-budget",
            content="我在准备期末考试",
            run_id="run-budget",
        )

    assert exc_info.value.code == "output_budget_exceeded"
    assert exc_info.value.retryable is True


def test_output_budget_exceeded_keeps_retry_task_pending() -> None:
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    atomic = AtomicProfileService(four, InMemoryAtomicProfileRepository())
    service = AutomaticProfileService(
        four_dimension_service=four,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=GatewayAutomaticProfileExtractor(
            cast(ModelGateway, _BudgetGateway())
        ),
        atomic_profile_service=atomic,
    )

    result = service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="m-budget-task",
        content="我在准备期末考试",
        run_id="run-budget-task",
        mode="companion",
    )

    assert result.run.status is ProfileExtractionStatus.PENDING
    assert result.run.last_error == "output_budget_exceeded"
