"""Issue 15：默认自动画像抽取与回答前注入的公共接缝测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from bridges.ai import ModelGateway
from bridges.ai.ports import ModelRunLockRecorder
from bridges.contracts.ai import (
    BusinessRef,
    ModelCallStatus,
    ModelRunLock,
    PersistedModelRunLock,
)
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutcome,
    ProfileExtractionOutput,
    ProfileExtractionRun,
    ProfileExtractionSource,
    ProfileExtractionStatus,
)
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
from bridges.profiles import (
    AutomaticProfileService,
    FourDimensionProfileService,
    GatewayAutomaticProfileExtractor,
    InMemoryAutomaticProfileRepository,
    InMemoryFourDimensionProfileRepository,
    RuleBasedAutomaticProfileExtractor,
    SqliteAutomaticProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles.signals import (
    PROFILE_SIGNAL_CLASSIFIER_VERSION,
    ProfileSignalCategory,
    ProfileSignalClassification,
    ProfileSignalClassifier,
)
from bridges.storage import BridgesDatabase


class _GoalExtractor:
    version = "test-extractor-v1"

    def extract(self, **_: object) -> ProfileExtractionOutput:
        return ProfileExtractionOutput.model_validate(
            {
                "items": [
                    {
                        "dimension": FourDimension.STAGE_GOAL.value,
                        "normalized_value": "今年通过雅思考试",
                        "evidence_ref": "message-1",
                        "reliability": 0.99,
                        "action": "create",
                    }
                ]
            }
        )


class _BrokenExtractor:
    version = "broken-v1"

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, **_: object) -> ProfileExtractionOutput:
        self.calls += 1
        raise RuntimeError("extractor unavailable")


class _KnowledgeExtractor:
    version = "knowledge-v1"

    def extract(self, *, message_id: str, **_: object) -> ProfileExtractionOutput:
        return ProfileExtractionOutput.model_validate(
            {
                "items": [
                    {
                        "dimension": FourDimension.KNOWLEDGE_INTEREST.value,
                        "normalized_value": "物理",
                        "evidence_ref": message_id,
                        "reliability": 0.9,
                        "action": "create",
                    }
                ]
            }
        )


class _TwoItemExtractor:
    version = "two-item-v1"

    def extract(self, *, message_id: str, **_: object) -> ProfileExtractionOutput:
        return ProfileExtractionOutput.model_validate(
            {
                "items": [
                    {
                        "dimension": FourDimension.STAGE_GOAL.value,
                        "normalized_value": "完成目标 A",
                        "evidence_ref": message_id,
                        "reliability": 0.9,
                        "action": "create",
                    },
                    {
                        "dimension": FourDimension.ACADEMIC_STATUS.value,
                        "normalized_value": "大学生",
                        "evidence_ref": message_id,
                        "reliability": 0.9,
                        "action": "create",
                    },
                ]
            }
        )


class _ChangingGoalExtractor:
    version = "changing-goal-v1"

    def extract(
        self, *, content: str, message_id: str, **_: object
    ) -> ProfileExtractionOutput:
        value = "新目标" if "新目标" in content else "旧目标"
        return ProfileExtractionOutput.model_validate(
            {
                "items": [
                    {
                        "dimension": FourDimension.STAGE_GOAL.value,
                        "normalized_value": value,
                        "evidence_ref": message_id,
                        "reliability": 0.95,
                        "action": "create",
                    }
                ]
            }
        )


class _FailingFourDimensionRepository(InMemoryFourDimensionProfileRepository):
    def __init__(self) -> None:
        super().__init__()
        self._saves = 0

    def save_record(self, record):  # type: ignore[no-untyped-def]
        self._saves += 1
        if self._saves == 2:
            raise RuntimeError("四维提交失败")
        return super().save_record(record)


def _fake_lock(
    lock_id: str,
    *,
    status: ModelCallStatus,
    error_code: str | None = None,
    run_id: str = "run-1",
    account_id: str = "account-alice",
) -> ModelRunLock:
    """构造与真实网关同形的不可变运行锁（Issue 13）。"""

    return ModelRunLock(
        lock_id=lock_id,
        run_id=run_id,
        account_id=account_id,
        project_id="conversation-1",
        capability_name="qwen_profile_extraction",
        capability_version="1",
        actual_model_id="qwen3.7-plus-2026-05-26",
        region="cn-beijing",
        parameters={"temperature": 0, "max_tokens": 512},
        prompt_version="2026-08-12",
        input_output_contract="qwen_profile_extraction:profile-message-v1->profile-extraction-v2",
        status=status,
        error_code=error_code,
        created_at=datetime.now(UTC),
    )


class _LockAwareCountingGateway:
    """记录调用次数并按调用序号返回真实形制锁的假网关。

    ``fail="transient"`` 时所有调用都失败；``fail_once=True`` 只让第一次
    调用失败（用于队列重试恢复用例）。
    """

    def __init__(
        self,
        output: dict[str, object] | None = None,
        *,
        fail: str | None = None,
        fail_once: bool = False,
        with_lock: bool = True,
    ) -> None:
        self.calls = 0
        self._output = output if output is not None else {"items": []}
        self._fail = fail
        self._fail_once = fail_once
        self._with_lock = with_lock

    def invoke(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.calls += 1
        failing = self._fail is not None and (
            not self._fail_once or self.calls == 1
        )
        # 真实网关签名：invoke(capability_name, capability_version,
        # run_context, payload=...)，run_context 是第三个位置参数。
        run_context = args[2] if len(args) > 2 else None
        lock = (
            _fake_lock(
                f"fake-lock-{self.calls}",
                status=(
                    ModelCallStatus.RETRYABLE_FAIL
                    if failing
                    else ModelCallStatus.SUCCESS
                ),
                error_code=self._fail if failing else None,
                run_id=(
                    run_context.run_id
                    if run_context is not None
                    else "run-1"
                ),
                account_id=(
                    run_context.account_id
                    if run_context is not None
                    else "account-alice"
                ),
            )
            if self._with_lock
            else None
        )
        if failing:
            return SimpleNamespace(
                status=ModelCallStatus.RETRYABLE_FAIL,
                output=None,
                error_code=self._fail,
                error_message="temporary provider failure",
                lock=lock,
            )
        return SimpleNamespace(
            status=ModelCallStatus.SUCCESS,
            output=self._output,
            error_code=None,
            error_message=None,
            lock=lock,
        )


class _RecordingLockRecorder(ModelRunLockRecorder):
    """收集 record() 调用的内存替身；只用于断言锁与业务关联。"""

    def __init__(self) -> None:
        self.records: list[tuple[ModelRunLock, BusinessRef]] = []

    def record(
        self,
        lock: ModelRunLock,
        *,
        business_ref: BusinessRef,
    ) -> PersistedModelRunLock:
        self.records.append((lock, business_ref))
        return PersistedModelRunLock.model_validate(lock.model_dump())

    def record_many(
        self, requests: list[Any]
    ) -> list[PersistedModelRunLock]:
        for request in requests:
            self.record(request.lock, business_ref=request.business_ref)
        return []

    def get_lock(
        self, lock_id: str, account_id: str
    ) -> PersistedModelRunLock | None:
        del lock_id, account_id
        return None

    def list_locks_by_run(
        self, account_id: str, run_id: str
    ) -> list[PersistedModelRunLock]:
        del account_id, run_id
        return []

    def list_locks_by_business_ref(
        self, account_id: str, object_type: str, object_id: str
    ) -> list[PersistedModelRunLock]:
        del account_id, object_type, object_id
        return []


def test_explicit_goal_is_committed_before_same_round_slice_is_compiled() -> None:
    target_repository = InMemoryFourDimensionProfileRepository()
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=target_repository,
    )
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=_GoalExtractor(),
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的阶段目标是今年通过雅思考试",
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == "succeeded"
    records = target_service.list_records("account-alice")
    assert [(record.dimension, record.content) for record in records] == [
        (FourDimension.STAGE_GOAL, "今年通过雅思考试")
    ]

    slice_ = service.compile_chat_slice(
        "account-alice",
        mode="companion",
        run_id="assistant-1",
        project_id="conversation-1",
    )
    assert [item.value_or_rule for item in slice_.included_items] == [
        "今年通过雅思考试"
    ]
    assert slice_.length_budget == 6


def test_privacy_notice_is_account_scoped_and_shown_once() -> None:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=_GoalExtractor(),
    )

    first = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的阶段目标是今年通过雅思考试",
        run_id="run-1",
        mode="companion",
    )
    replay = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的阶段目标是今年通过雅思考试",
        run_id="run-2",
        mode="companion",
    )

    assert first.privacy_notice is not None
    assert replay.privacy_notice is None


def test_extraction_failure_has_one_bounded_retry_task() -> None:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    extractor = _BrokenExtractor()
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=extractor,
    )

    first = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的阶段目标是今年通过雅思考试",
        run_id="run-1",
        mode="companion",
    )
    replay = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的阶段目标是今年通过雅思考试",
        run_id="run-2",
        mode="companion",
    )

    assert first.run.status == "pending"
    assert replay.run.extraction_id == first.run.extraction_id
    assert len(service.list_retry_tasks("account-alice")) == 1
    for _ in range(3):
        service.run_retry_tick()
    task = service.list_retry_tasks("account-alice")[0]
    assert task.status == "exhausted"
    assert extractor.calls == 4
    assert target_service.list_records("account-alice") == []


def test_tombstone_exhausts_retry_without_replaying_old_message() -> None:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    extractor = _BrokenExtractor()
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=extractor,
    )

    service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的阶段目标是今年通过雅思考试",
        run_id="run-1",
        mode="companion",
    )
    service.mark_message_tombstone("account-alice", "message-1")

    service.run_retry_tick()

    task = service.list_retry_tasks("account-alice")[0]
    assert task.status == "exhausted"
    assert extractor.calls == 1
    repository = service._repository  # type: ignore[attr-defined]
    assert (
        repository.list_observations(  # type: ignore[union-attr]
            "account-alice",
            FourDimension.STAGE_GOAL,
            "今年通过雅思考试",
            since=datetime.now(UTC) - timedelta(days=1),
        )
        == []
    )


def test_repeated_non_explicit_knowledge_needs_two_messages_within_window() -> None:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=_KnowledgeExtractor(),
    )

    for index in (1, 2):
        service.preprocess_message(
            "account-alice",
            conversation_id="conversation-1",
            message_id=f"message-{index}",
            content="我正在学习物理",
            run_id=f"run-{index}",
            mode="study",
        )

    records = target_service.list_records("account-alice")
    assert [(record.dimension, record.content) for record in records] == [
        (FourDimension.KNOWLEDGE_INTEREST, "物理")
    ]


@pytest.mark.parametrize(
    ("content", "dimension", "value"),
    [
        ("我目前是大学生", FourDimension.ACADEMIC_STATUS, "大学生"),
        ("我对物理感兴趣", FourDimension.KNOWLEDGE_INTEREST, "物理"),
        ("我喜欢跑步", FourDimension.HOBBY, "跑步"),
        ("我的阶段目标是完成论文", FourDimension.STAGE_GOAL, "完成论文"),
    ],
)
def test_rule_extractor_routes_explicit_self_signals_to_four_dimensions(
    content: str, dimension: FourDimension, value: str
) -> None:
    output = RuleBasedAutomaticProfileExtractor().extract(
        account_id="account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
    )

    assert [(item.dimension, item.normalized_value) for item in output.items] == [
        (dimension, value)
    ]


@pytest.mark.parametrize(
    ("content", "category"),
    [
        ("大三人工智能专业", ProfileSignalCategory.HIGH_CONFIDENCE_SELF),
        ("目标考211相关专业/考研", ProfileSignalCategory.HIGH_CONFIDENCE_SELF),
        ("想学习Transformer", ProfileSignalCategory.HIGH_CONFIDENCE_SELF),
        ("找Transformer论文", ProfileSignalCategory.BEHAVIOR_OBSERVATION),
        ("我朋友是大三人工智能专业", ProfileSignalCategory.FORBIDDEN),
        ("第三方说：我想学习 Transformer", ProfileSignalCategory.FORBIDDEN),
        ("我不想学习 Transformer", ProfileSignalCategory.FORBIDDEN),
        ("引用：我想学习 Transformer", ProfileSignalCategory.FORBIDDEN),
        ("我最近焦虑", ProfileSignalCategory.FORBIDDEN),
        ("我喜欢佛教", ProfileSignalCategory.FORBIDDEN),
        ("我对糖尿病感兴趣", ProfileSignalCategory.FORBIDDEN),
        ("今天天气不错", ProfileSignalCategory.NO_SIGNAL),
    ],
)
def test_profile_signal_classifier_uses_one_shared_category(
    content: str, category: ProfileSignalCategory
) -> None:
    result = ProfileSignalClassifier().classify(content)

    assert result.category == category
    assert result.reason_code
    assert result.strategy_version


@pytest.mark.parametrize(
    "content",
    ["大学生如何学习Transformer", "目标是什么"],
)
def test_course_and_profile_questions_do_not_become_stable_self_statements(
    content: str,
) -> None:
    result = ProfileSignalClassifier().classify(content)

    assert result.category == ProfileSignalCategory.BEHAVIOR_OBSERVATION
    assert not result.is_self_statement


def test_subject_omission_messages_create_three_dimensions_without_hobby() -> None:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
    )

    messages = (
        ("conversation-academic", "大三人工智能专业"),
        ("conversation-goal", "目标考211相关专业/考研"),
        ("conversation-knowledge", "想学习Transformer"),
        ("conversation-search", "找Transformer论文"),
    )
    results = [
        service.preprocess_message(
            "account-alice",
            conversation_id=conversation_id,
            message_id=f"message-{index}",
            content=content,
            run_id=f"run-{index}",
            mode="study",
        )
        for index, (conversation_id, content) in enumerate(messages, start=1)
    ]

    assert all(result.run.status == "succeeded" for result in results)
    assert {
        (record.dimension, record.content)
        for record in target_service.list_records("account-alice")
    } == {
        (FourDimension.ACADEMIC_STATUS, "大三人工智能专业"),
        (FourDimension.STAGE_GOAL, "考211相关专业/考研"),
        (FourDimension.KNOWLEDGE_INTEREST, "Transformer"),
    }
    assert not any(
        record.dimension == FourDimension.HOBBY
        for record in target_service.list_records("account-alice")
    )


def test_search_is_observation_then_explicit_learning_deduplicates_topic() -> None:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    repository = InMemoryAutomaticProfileRepository()
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=repository,
        extractor=RuleBasedAutomaticProfileExtractor(),
    )

    search = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-search",
        message_id="message-search",
        content="找Transformer论文",
        run_id="run-search",
        mode="study",
    )
    learning = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-learning",
        message_id="message-learning",
        content="想学习Transformer",
        run_id="run-learning",
        mode="study",
    )

    assert search.observed_count == 1
    assert learning.committed_record_ids
    assert [
        (record.dimension, record.content)
        for record in target_service.list_records("account-alice")
    ] == [(FourDimension.KNOWLEDGE_INTEREST, "Transformer")]
    observations = repository.list_observations(
        "account-alice",
        FourDimension.KNOWLEDGE_INTEREST,
        "Transformer",
        since=datetime.now(UTC) - timedelta(days=1),
    )
    assert [observation.message_id for observation in observations] == [
        "message-search",
        "message-learning",
    ]
    assert 0 < observations[0].reliability < 0.6
    assert all(
        PROFILE_SIGNAL_CLASSIFIER_VERSION in observation.extractor_version
        for observation in observations
    )
    record = target_service.list_records("account-alice")[0]
    assert PROFILE_SIGNAL_CLASSIFIER_VERSION in record.migration_version
    assert "category=high_confidence_self" in record.migration_version


@pytest.mark.parametrize(
    "content",
    [
        "我朋友是大三人工智能专业",
        "第三方说：我想学习 Transformer",
        "假设我是大三学生",
        "我不想学习 Transformer",
        "角色扮演：我想学习 Transformer",
        "引用：我想学习 Transformer",
        "我最近焦虑，想学习 Transformer",
        "我想学习政治",
        "我喜欢佛教",
        "我对糖尿病感兴趣",
    ],
)
def test_forbidden_subject_omission_variants_never_write_profile(
    content: str,
) -> None:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
        mode="study",
    )

    assert result.run.status == "succeeded"
    assert result.observed_count == 0
    assert target_service.list_records("account-alice") == []


def test_submission_validation_reuses_precheck_classification() -> None:
    class _ClassificationAwareExtractor:
        version = "classification-aware-v1"

        def __init__(self) -> None:
            self.classifications: list[ProfileSignalClassification] = []

        def extract(
            self,
            *,
            message_id: str,
            signal_classification: ProfileSignalClassification,
            **_: object,
        ) -> ProfileExtractionOutput:
            self.classifications.append(signal_classification)
            return ProfileExtractionOutput.model_validate(
                {
                    "items": [
                        {
                            "dimension": FourDimension.KNOWLEDGE_INTEREST,
                            "normalized_value": "Transformer",
                            "evidence_ref": message_id,
                            "reliability": 0.99,
                            "action": "create",
                        }
                    ]
                }
            )

    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    extractor = _ClassificationAwareExtractor()
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=extractor,
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-search",
        message_id="message-search",
        content="找Transformer论文",
        run_id="run-search",
        mode="study",
    )

    assert extractor.classifications[0].category == (
        ProfileSignalCategory.BEHAVIOR_OBSERVATION
    )
    assert result.run.status == "exhausted"
    assert result.run.last_error == "profile_extraction_self_statement_missing"
    assert target_service.list_records("account-alice") == []


def test_gateway_configuration_keeps_deterministic_signals_local() -> None:
    class _CountingGateway:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            self.calls += 1
            return SimpleNamespace(
                status=ModelCallStatus.SUCCESS,
                output={"items": []},
                error_code=None,
                error_message=None,
            )

    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    gateway = _CountingGateway()
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=GatewayAutomaticProfileExtractor(cast(ModelGateway, gateway)),
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-learning",
        message_id="message-learning",
        content="想学习 CNN",
        run_id="run-learning",
        mode="study",
    )

    assert result.run.status == "succeeded"
    assert gateway.calls == 0
    assert [record.content for record in target_service.list_records("account-alice")] == [
        "CNN"
    ]


def test_learning_request_is_committed_as_knowledge_interest_on_first_message() -> None:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我想学习卷积神经网络相关知识",
        run_id="run-1",
        mode="study",
    )

    assert result.run.status == "succeeded"
    assert [
        (record.dimension, record.content)
        for record in target_service.list_records("account-alice")
    ] == [(FourDimension.KNOWLEDGE_INTEREST, "卷积神经网络相关知识")]


def test_compound_academic_and_planning_request_commits_two_dimensions() -> None:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我是一名大三的人工智能专业学生，给我规划一下考研进度",
        run_id="run-1",
        mode="study",
    )

    assert result.run.status == "succeeded"
    assert {
        (record.dimension, record.content)
        for record in target_service.list_records("account-alice")
    } == {
        (FourDimension.ACADEMIC_STATUS, "一名大三的人工智能专业学生"),
        (FourDimension.STAGE_GOAL, "考研进度"),
    }


def test_standalone_planning_request_triggers_stage_goal_extraction() -> None:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="给我规划一下考研进度",
        run_id="run-1",
        mode="study",
    )

    assert result.run.status == "succeeded"
    assert [record.content for record in target_service.list_records("account-alice")] == [
        "考研进度"
    ]


def test_gateway_profile_prompt_requires_json_and_preserves_upstream_error_message() -> None:
    class _Gateway:
        def __init__(self, result: SimpleNamespace) -> None:
            self.result = result
            self.payload: dict[str, Any] | None = None

        def invoke(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args
            self.payload = cast(dict[str, Any], kwargs["payload"])
            return self.result

    success_gateway = _Gateway(
        SimpleNamespace(
            status=ModelCallStatus.SUCCESS,
            output={"items": []},
            error_code=None,
            error_message=None,
            lock=None,
        )
    )
    GatewayAutomaticProfileExtractor(cast(ModelGateway, success_gateway)).extract(
        account_id="account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我想学习物理",
        run_id="run-1",
    )

    assert success_gateway.payload is not None
    system_prompt = success_gateway.payload["messages"][0]["content"]
    assert "json" in system_prompt.lower()

    failed_gateway = _Gateway(
        SimpleNamespace(
            status=ModelCallStatus.BLOCKED,
            output=None,
            error_code="client_error_400",
            error_message="Qwen client error (400): messages must contain JSON",
            lock=None,
        )
    )
    with pytest.raises(RuntimeError, match="client_error_400.*messages must contain JSON"):
        GatewayAutomaticProfileExtractor(cast(ModelGateway, failed_gateway)).extract(
            account_id="account-alice",
            conversation_id="conversation-1",
            message_id="message-1",
            content="我想学习物理",
            run_id="run-1",
        )


def test_ordinary_question_is_internal_observation_only() -> None:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="什么是物理？",
        run_id="run-1",
        mode="study",
    )

    assert result.observed_count == 1
    assert target_service.list_records("account-alice") == []
    assert (
        service.compile_chat_slice(
            "account-alice", mode="study", run_id="assistant-1"
        ).included_items
        == []
    )


def test_forbidden_signal_is_zero_write() -> None:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我朋友喜欢物理",
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == "succeeded"
    assert result.observed_count == 0
    assert target_service.list_records("account-alice") == []


def test_extractor_cannot_invent_a_forbidden_normalized_value() -> None:
    class _InventedSensitiveExtractor:
        version = "test-extractor-v1"

        def extract(self, **kwargs: object) -> ProfileExtractionOutput:
            return ProfileExtractionOutput.model_validate(
                {
                    "items": [
                        {
                            "dimension": FourDimension.STAGE_GOAL,
                            "normalized_value": "焦虑",
                            "evidence_ref": kwargs["message_id"],
                            "reliability": 0.99,
                            "action": "create",
                        }
                    ]
                }
            )

    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=_InventedSensitiveExtractor(),
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的阶段目标是完成论文",
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == "exhausted"
    assert result.run.last_error == "profile_extraction_forbidden_value"
    assert target_service.list_records("account-alice") == []


def test_extraction_contract_rejects_unknown_dimensions_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ProfileExtractionOutput.model_validate(
            {
                "items": [
                    {
                        "dimension": "political_identity",
                        "normalized_value": "不应写入",
                        "evidence_ref": "message-1",
                        "reliability": 1,
                        "action": "create",
                        "confidence_reason": "额外字段",
                    }
                ]
            }
        )


def test_failed_batch_rolls_back_observations_and_four_dimension_records() -> None:
    target_repository = _FailingFourDimensionRepository()
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=target_repository,
    )
    automatic_repository = InMemoryAutomaticProfileRepository()
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=automatic_repository,
        extractor=_TwoItemExtractor(),
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的阶段目标和学业情况很明确",
        run_id="run-1",
        mode="study",
    )

    assert result.run.status == "pending"
    assert target_service.list_records("account-alice") == []
    assert (
        automatic_repository.list_observations(
            "account-alice",
            FourDimension.STAGE_GOAL,
            "完成目标 A",
            since=datetime(2020, 1, 1, tzinfo=UTC),
        )
        == []
    )


def test_later_explicit_stage_goal_updates_in_place() -> None:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=_ChangingGoalExtractor(),
    )

    for index, content in enumerate(("我的目标是旧目标", "我的目标是新目标"), start=1):
        service.preprocess_message(
            "account-alice",
            conversation_id="conversation-1",
            message_id=f"message-{index}",
            content=content,
            run_id=f"run-{index}",
            mode="study",
        )

    records = target_service.list_records("account-alice")
    assert len(records) == 1
    assert records[0].content == "新目标"


def test_sqlite_repository_survives_restart_and_isolates_accounts(tmp_path) -> None:
    database_path = tmp_path / "bridges.db"
    first_database = BridgesDatabase(database_path)
    first_database.initialize()
    first_service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=SqliteFourDimensionProfileRepository(first_database),
        ),
        repository=SqliteAutomaticProfileRepository(first_database),
        extractor=_GoalExtractor(),
    )
    first_service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的阶段目标是今年通过雅思考试",
        run_id="run-1",
        mode="companion",
    )
    first_database.close()

    second_database = BridgesDatabase(database_path)
    second_service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=SqliteFourDimensionProfileRepository(second_database),
        ),
        repository=SqliteAutomaticProfileRepository(second_database),
        extractor=_GoalExtractor(),
    )

    replay = second_service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的阶段目标是今年通过雅思考试",
        run_id="run-2",
        mode="companion",
    )
    other_account = second_service.preprocess_message(
        "account-bob",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的阶段目标是今年通过雅思考试",
        run_id="run-3",
        mode="companion",
    )

    assert replay.run.status == "succeeded"
    assert other_account.run.account_id == "account-bob"
    assert (
        len(second_service._four_dimensions.list_records("account-alice")) == 1
    )  # noqa: SLF001
    assert (
        len(second_service._four_dimensions.list_records("account-bob")) == 1
    )  # noqa: SLF001
    second_database.close()


def test_sqlite_subject_omission_path_survives_restart_and_isolates_accounts(
    tmp_path,
) -> None:
    database_path = tmp_path / "issue06.db"
    first_database = BridgesDatabase(database_path)
    first_database.initialize()
    first_target = SqliteFourDimensionProfileRepository(first_database)
    first_automatic = SqliteAutomaticProfileRepository(first_database)
    first_service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=first_target,
        ),
        repository=first_automatic,
        extractor=RuleBasedAutomaticProfileExtractor(),
    )
    for index, content in enumerate(
        (
            "大三人工智能专业",
            "目标考211相关专业/考研",
            "想学习Transformer",
            "找Transformer论文",
        ),
        start=1,
    ):
        first_service.preprocess_message(
            "account-alice",
            conversation_id=f"conversation-{index}",
            message_id=f"message-{index}",
            content=content,
            run_id=f"run-{index}",
            mode="study",
        )
    first_database.close()

    second_database = BridgesDatabase(database_path)
    second_service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=SqliteFourDimensionProfileRepository(second_database),
        ),
        repository=SqliteAutomaticProfileRepository(second_database),
        extractor=RuleBasedAutomaticProfileExtractor(),
    )
    replay = second_service.preprocess_message(
        "account-alice",
        conversation_id="conversation-3",
        message_id="message-3",
        content="想学习Transformer",
        run_id="replay",
        mode="study",
    )
    bob = second_service.preprocess_message(
        "account-bob",
        conversation_id="conversation-1",
        message_id="message-1",
        content="找Transformer论文",
        run_id="bob-run",
        mode="study",
    )

    assert replay.run.status == "succeeded"
    assert bob.run.account_id == "account-bob"
    assert {
        (record.dimension, record.content)
        for record in second_service._four_dimensions.list_records("account-alice")
    } == {
        (FourDimension.ACADEMIC_STATUS, "大三人工智能专业"),
        (FourDimension.STAGE_GOAL, "考211相关专业/考研"),
        (FourDimension.KNOWLEDGE_INTEREST, "Transformer"),
    }  # noqa: SLF001
    assert second_service._four_dimensions.list_records("account-bob") == []  # noqa: SLF001
    second_database.close()


def test_sqlite_retry_queue_recovers_after_restart(tmp_path) -> None:
    database_path = tmp_path / "retry.db"
    first_database = BridgesDatabase(database_path)
    first_database.initialize()
    first_extractor = _BrokenExtractor()
    first_service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=SqliteFourDimensionProfileRepository(first_database),
        ),
        repository=SqliteAutomaticProfileRepository(first_database),
        extractor=first_extractor,
    )
    first_service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的阶段目标是今年通过雅思考试",
        run_id="run-1",
        mode="companion",
    )
    assert first_extractor.calls == 1
    first_database.close()

    second_database = BridgesDatabase(database_path)
    second_extractor = _BrokenExtractor()
    second_service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=SqliteFourDimensionProfileRepository(second_database),
        ),
        repository=SqliteAutomaticProfileRepository(second_database),
        extractor=second_extractor,
    )

    for _ in range(3):
        second_service.run_retry_tick()

    task = second_service.list_retry_tasks("account-alice")[0]
    assert task.status == "exhausted"
    assert second_extractor.calls == 3
    second_database.close()


def test_sqlite_restart_recovers_an_inflight_run_without_a_task(tmp_path) -> None:
    database_path = tmp_path / "inflight.db"
    first_database = BridgesDatabase(database_path)
    first_database.initialize()
    first_repository = SqliteAutomaticProfileRepository(first_database)
    now = datetime.now(UTC)
    run = ProfileExtractionRun(
        extraction_id="inflight-run",
        account_id="account-alice",
        message_id="message-1",
        extractor_version=f"broken-v1+signal-{PROFILE_SIGNAL_CLASSIFIER_VERSION}",
        source_hash="source-hash",
        source_snapshot="我的阶段目标是今年通过雅思考试",
        status=ProfileExtractionStatus.RUNNING,
        outcome=ProfileExtractionOutcome.PENDING_RETRY,
        attempts=0,
        created_at=now,
        updated_at=now,
    )
    first_repository.save_run(run)
    first_database.close()

    second_database = BridgesDatabase(database_path)
    second_service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=SqliteFourDimensionProfileRepository(second_database),
        ),
        repository=SqliteAutomaticProfileRepository(second_database),
        extractor=_BrokenExtractor(),
    )

    tasks = second_service.list_retry_tasks("account-alice")
    recovered = second_service._repository.get_run(  # type: ignore[attr-defined]
        "account-alice",
        "message-1",
        run.extractor_version,
        "source-hash",
    )
    assert len(tasks) == 1
    assert tasks[0].status == ProfileExtractionStatus.PENDING
    assert recovered is not None
    assert recovered.outcome == ProfileExtractionOutcome.PENDING_RETRY
    assert recovered.last_error == "profile_extraction_recovered_after_restart"
    second_database.close()


# ---------------------------------------------------------------------------
# Issue 13：保留混合抽取并诚实标记来源（local_rule / qwen_model）。
# ---------------------------------------------------------------------------


def _hybrid_service(
    gateway: _LockAwareCountingGateway,
    *,
    recorder: _RecordingLockRecorder | None = None,
) -> tuple[AutomaticProfileService, FourDimensionProfileService]:
    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=GatewayAutomaticProfileExtractor(cast(ModelGateway, gateway)),
        lock_recorder=recorder,
    )
    return service, target_service


@pytest.mark.parametrize(
    ("content", "expected_outcome"),
    [
        ("我的目标是今年通过雅思考试", ProfileExtractionOutcome.SUCCEEDED_WRITTEN),
        ("想学习Transformer", ProfileExtractionOutcome.SUCCEEDED_WRITTEN),
        ("找Transformer论文", ProfileExtractionOutcome.SUCCEEDED_OBSERVED),
        ("我的关注点换成 CNN", ProfileExtractionOutcome.SUCCEEDED_CORRECTION_WRITTEN),
    ],
)
def test_local_rule_cases_never_call_gateway_and_mark_source(
    content: str, expected_outcome: ProfileExtractionOutcome
) -> None:
    """明确自述、学习目标、清晰观察、更正代表性用例：source=local_rule、
    网关 0 调用、模型锁 0 条（Issue 13 AC）。"""
    gateway = _LockAwareCountingGateway()
    recorder = _RecordingLockRecorder()
    service, target_service = _hybrid_service(gateway, recorder=recorder)
    if content == "我的关注点换成 CNN":
        target_service.upsert_automatic_record(
            "account-alice",
            dimension=FourDimension.KNOWLEDGE_INTEREST,
            content="旧的知识兴趣",
            action="create",
            confidence=FourDimensionConfidence.HIGH,
        )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    assert result.run.outcome == expected_outcome
    assert result.run.source == ProfileExtractionSource.LOCAL_RULE
    assert gateway.calls == 0
    assert recorder.records == []
    if content in {"找Transformer论文", "我的关注点换成 CNN"}:
        return
    records = target_service.list_records("account-alice")
    assert records
    assert "source=local_rule" in records[0].migration_version


@pytest.mark.parametrize(
    ("content", "expected_status", "expected_outcome"),
    [
        (
            "把我的阶段目标撤回",
            ProfileExtractionStatus.SUCCEEDED,
            ProfileExtractionOutcome.NO_SIGNAL,
        ),
        (
            "不要记录",
            ProfileExtractionStatus.EXHAUSTED,
            ProfileExtractionOutcome.NO_SIGNAL,
        ),
    ],
)
def test_withdraw_and_privacy_blocked_never_call_gateway(
    content: str,
    expected_status: ProfileExtractionStatus,
    expected_outcome: ProfileExtractionOutcome,
) -> None:
    """撤回/隐私阻断代表用例同样不调用 Qwen，来源保持 local_rule。"""
    gateway = _LockAwareCountingGateway()
    service, _ = _hybrid_service(gateway)

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
        mode="companion",
    )

    assert gateway.calls == 0
    assert result.run.source == ProfileExtractionSource.LOCAL_RULE
    assert result.run.status == expected_status
    assert result.run.outcome == expected_outcome


def test_ambiguous_signal_uses_qwen_branch_with_one_lock() -> None:
    """歧义信号：source=qwen_model、真实调用 1 次、锁 1 条（Issue 13 AC）。"""
    gateway = _LockAwareCountingGateway(
        {
            "items": [
                {
                    "dimension": FourDimension.KNOWLEDGE_INTEREST.value,
                    "normalized_value": "Transformer",
                    "evidence_ref": "message-1",
                    "reliability": 0.9,
                    "action": "observe",
                }
            ]
        }
    )
    recorder = _RecordingLockRecorder()
    service, _ = _hybrid_service(gateway, recorder=recorder)

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我可能想学习 Transformer",
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    assert result.run.outcome == ProfileExtractionOutcome.SUCCEEDED_OBSERVED
    assert result.run.source == ProfileExtractionSource.QWEN_MODEL
    assert gateway.calls == 1
    assert len(recorder.records) == 1
    lock, business_ref = recorder.records[0]
    assert lock.status == ModelCallStatus.SUCCESS
    assert business_ref.object_type == "profile_extraction_run"
    assert business_ref.object_id == result.run.extraction_id
    assert business_ref.attempt_ordinal == 1
    assert business_ref.is_primary is True
    observations = service._repository.list_observations(  # type: ignore[attr-defined]
        "account-alice",
        FourDimension.KNOWLEDGE_INTEREST,
        "Transformer",
        since=datetime.now(UTC) - timedelta(days=1),
    )
    assert observations
    assert observations[0].source == ProfileExtractionSource.QWEN_MODEL
    assert "source=qwen_model" in observations[0].extractor_version


def test_qwen_retry_keeps_first_failed_lock_and_appends_next_ordinal() -> None:
    """首次失败锁保留，重试真实发生后新增下一序号锁（Issue 13 AC）。"""
    gateway = _LockAwareCountingGateway(
        {
            "items": [
                {
                    "dimension": FourDimension.KNOWLEDGE_INTEREST.value,
                    "normalized_value": "Transformer",
                    "evidence_ref": "message-1",
                    "reliability": 0.9,
                    "action": "observe",
                }
            ]
        },
        fail="transient",
        fail_once=True,
    )
    recorder = _RecordingLockRecorder()
    service, _ = _hybrid_service(gateway, recorder=recorder)

    first = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我可能想学习 Transformer",
        run_id="run-1",
        mode="companion",
    )
    assert first.run.status == ProfileExtractionStatus.PENDING
    assert first.run.source == ProfileExtractionSource.QWEN_MODEL
    assert gateway.calls == 1
    assert len(recorder.records) == 1
    failed_lock, failed_ref = recorder.records[0]
    assert failed_lock.status == ModelCallStatus.RETRYABLE_FAIL
    assert failed_ref.attempt_ordinal == 1

    service.run_retry_tick()

    task = service.list_retry_tasks("account-alice")[0]
    assert task.status == ProfileExtractionStatus.SUCCEEDED
    assert gateway.calls == 2
    assert len(recorder.records) == 2
    second_lock, second_ref = recorder.records[1]
    assert second_lock.status == ModelCallStatus.SUCCESS
    assert second_ref.attempt_ordinal == 2
    assert second_ref.object_id == first.run.extraction_id
    assert second_ref.is_primary is False
    # 来源不被重试改写。
    run = service._repository.get_run(  # type: ignore[attr-defined]
        "account-alice",
        "message-1",
        first.run.extractor_version,
        first.run.source_hash,
    )
    assert run is not None
    assert run.source == ProfileExtractionSource.QWEN_MODEL


def test_local_branch_with_collected_lock_fails_closed() -> None:
    """守卫：本地分支出现模型调用必须失败关闭（profile_local_unexpected_model_call）。"""

    class _LockEmittingLocalExtractor:
        version = "local-leaking-v1"

        def extract(self, **kwargs: object) -> ProfileExtractionOutput:
            lock_sink = kwargs.get("lock_sink")
            if lock_sink is not None:
                lock_sink(
                    _fake_lock(
                        "leaked-lock",
                        status=ModelCallStatus.SUCCESS,
                    )
                )
            return ProfileExtractionOutput(items=[])

    target_service = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    recorder = _RecordingLockRecorder()
    service = AutomaticProfileService(
        four_dimension_service=target_service,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=_LockEmittingLocalExtractor(),
        lock_recorder=recorder,
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的目标是今年通过雅思考试",
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.EXHAUSTED
    assert result.run.last_error == "profile_local_unexpected_model_call"
    assert result.run.outcome == ProfileExtractionOutcome.PERMANENT_FAILURE
    # 来源不变量：local_rule 分支即使出现异常锁，也绝不落库（0 锁）。
    assert recorder.records == []
    assert target_service.list_records("account-alice") == []


def test_qwen_branch_without_lock_fails_closed() -> None:
    """守卫：Qwen 分支缺少运行锁必须失败关闭（profile_qwen_missing_run_lock）。"""
    gateway = _LockAwareCountingGateway(with_lock=False)
    service, _ = _hybrid_service(gateway)

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我可能想学习 Transformer",
        run_id="run-1",
        mode="companion",
    )

    assert gateway.calls == 1
    assert result.run.status == ProfileExtractionStatus.EXHAUSTED
    assert result.run.last_error == "profile_qwen_missing_run_lock"
    assert result.run.outcome == ProfileExtractionOutcome.PERMANENT_FAILURE
