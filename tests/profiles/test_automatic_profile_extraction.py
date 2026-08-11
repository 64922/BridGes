"""Issue 15：默认自动画像抽取与回答前注入的公共接缝测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from bridges.ai import ModelGateway
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.profile_extraction import ProfileExtractionOutput
from bridges.contracts.profiles import FourDimension
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
    assert result.run.status == "pending"
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

    assert result.run.status == "pending"
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
