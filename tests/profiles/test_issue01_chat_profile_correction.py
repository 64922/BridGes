from __future__ import annotations

from pathlib import Path

import pytest

from bridges.contracts.profile_extraction import (
    ProfileCorrectionStatus,
    ProfileExtractionOutcome,
)
from bridges.contracts.profiles import (
    FourDimension,
    FourDimensionConfidence,
    FourDimensionProfileModifyRequest,
)
from bridges.profiles import (
    AutomaticProfileService,
    FourDimensionProfileService,
    InMemoryAutomaticProfileRepository,
    InMemoryFourDimensionProfileRepository,
    SqliteAutomaticProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles.automatic import AutomaticProfileError
from bridges.profiles.signals import ProfileSignalCategory, ProfileSignalClassifier
from bridges.storage.database import BridgesDatabase


def _services() -> tuple[
    FourDimensionProfileService, AutomaticProfileService
]:
    four_dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    automatic = AutomaticProfileService(
        four_dimension_service=four_dimensions,
        repository=InMemoryAutomaticProfileRepository(),
    )
    return four_dimensions, automatic


@pytest.mark.parametrize(
    ("content", "dimension", "value"),
    [
        (
            "我改主意了，把这条画像内容修改成我喜欢学习卷积神经网络相关知识",
            FourDimension.KNOWLEDGE_INTEREST,
            "卷积神经网络相关知识",
        ),
        (
            "我换方向了，现在想学 CNN",
            FourDimension.KNOWLEDGE_INTEREST,
            "CNN",
        ),
        (
            "我改主意了，换成研一",
            FourDimension.ACADEMIC_STATUS,
            "研一",
        ),
        (
            "我改主意了，换成通过雅思考试",
            FourDimension.STAGE_GOAL,
            "通过雅思考试",
        ),
        (
            "不是 Transformer，而是卷积神经网络",
            FourDimension.KNOWLEDGE_INTEREST,
            "卷积神经网络",
        ),
        (
            "我对 Transformer 不感兴趣了，更喜欢 CNN",
            FourDimension.KNOWLEDGE_INTEREST,
            "CNN",
        ),
        (
            "我的阶段目标改成通过雅思考试",
            FourDimension.STAGE_GOAL,
            "通过雅思考试",
        ),
        (
            "把我的学业情况修改成研一",
            FourDimension.ACADEMIC_STATUS,
            "研一",
        ),
        (
            "把我的兴趣爱好换成跑步",
            FourDimension.HOBBY,
            "跑步",
        ),
    ],
)
def test_classifier_extracts_explicit_correction_intent(
    content: str, dimension: FourDimension, value: str
) -> None:
    classification = ProfileSignalClassifier().classify(content)

    assert classification.category == ProfileSignalCategory.CORRECTION
    assert classification.correction_intent is not None
    assert classification.correction_intent.dimension == dimension
    assert classification.correction_intent.new_value == value


def test_ordinary_self_statement_keeps_create_semantics() -> None:
    classification = ProfileSignalClassifier().classify(
        "我喜欢学习卷积神经网络相关知识"
    )

    assert classification.category == ProfileSignalCategory.EXPLICIT_SELF
    assert classification.correction_intent is None


def test_unrelated_replacement_is_not_a_profile_correction() -> None:
    classification = ProfileSignalClassifier().classify("把代码改成 Python")

    assert classification.category == ProfileSignalCategory.NO_SIGNAL
    assert classification.correction_intent is None


def test_chat_correction_updates_the_latest_active_record_in_place() -> None:
    four_dimensions, automatic = _services()
    original = four_dimensions.upsert_automatic_record(
        "account-alice",
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content="Transformer 的相关基础知识",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )
    original_version = original.version

    result = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-correction-1",
        content="我改主意了，把这条画像内容修改成我喜欢学习卷积神经网络相关知识",
        run_id="run-1",
        mode="companion",
    )

    updated = four_dimensions.list_records("account-alice")[0]
    assert result.run.outcome == ProfileExtractionOutcome.SUCCEEDED_CORRECTION_WRITTEN
    assert result.correction is not None
    assert result.correction.status == ProfileCorrectionStatus.WRITTEN
    assert result.correction.dimension == FourDimension.KNOWLEDGE_INTEREST
    assert updated.record_id == original.record_id
    assert updated.content == "卷积神经网络相关知识"
    assert updated.correction_count == 1
    assert updated.change_note == "用户纠正后已更新"
    assert updated.version == original_version + 1
    assert updated.updated_at >= original.updated_at


def test_chat_correction_uses_latest_record_and_is_idempotent() -> None:
    four_dimensions, automatic = _services()
    first = four_dimensions.upsert_automatic_record(
        "account-alice",
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content="旧的知识兴趣",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )
    second = four_dimensions.upsert_automatic_record(
        "account-alice",
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content="较新的知识兴趣",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )

    result = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-correction-2",
        content="把我的关注点换成 CNN",
        run_id="run-2",
        mode="companion",
    )
    replay = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-correction-2",
        content="把我的关注点换成 CNN",
        run_id="run-replay",
        mode="companion",
    )

    records = four_dimensions.list_records("account-alice")
    latest = next(record for record in records if record.record_id == second.record_id)
    unchanged = next(record for record in records if record.record_id == first.record_id)
    assert result.correction is not None
    assert result.correction.status == ProfileCorrectionStatus.WRITTEN
    assert latest.content == "CNN"
    assert latest.correction_count == 1
    assert unchanged.content == "旧的知识兴趣"
    assert replay.run.outcome == ProfileExtractionOutcome.SUCCEEDED_CORRECTION_WRITTEN
    assert latest.correction_count == 1


def test_protected_chat_correction_does_not_change_the_record() -> None:
    four_dimensions, automatic = _services()
    record = four_dimensions.upsert_automatic_record(
        "account-alice",
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content="第一次兴趣",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )
    for index in range(3):
        record = four_dimensions.modify_record(
            "account-alice",
            record.record_id,
            FourDimensionProfileModifyRequest(
                content=f"手动纠正 {index}", version=record.version
            ),
        )
    before = record.model_copy(deep=True)

    result = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-correction-protected",
        content="把我的关注点换成 CNN",
        run_id="run-protected",
        mode="companion",
    )

    after = four_dimensions.list_records("account-alice")[0]
    assert result.correction is not None
    assert result.correction.status == ProfileCorrectionStatus.PROTECTED
    assert after == before


def test_unresolved_correction_keeps_profile_unchanged() -> None:
    four_dimensions, automatic = _services()
    original = four_dimensions.upsert_automatic_record(
        "account-alice",
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content="Transformer",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )

    result = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-correction-unresolved",
        content="把这条画像修改一下",
        run_id="run-unresolved",
        mode="companion",
    )

    assert result.correction is not None
    assert result.correction.status == ProfileCorrectionStatus.UNRESOLVED
    assert four_dimensions.list_records("account-alice")[0] == original


def test_correction_without_active_record_does_not_claim_a_write() -> None:
    _, automatic = _services()

    result = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-correction-no-active",
        content="把我的关注点换成 CNN",
        run_id="run-no-active",
        mode="companion",
    )

    assert result.run.outcome == ProfileExtractionOutcome.SUCCEEDED_CORRECTION_NO_ACTIVE
    assert result.correction is not None
    assert result.correction.status == ProfileCorrectionStatus.NO_ACTIVE_RECORD


def test_transient_correction_failure_reuses_correction_path_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    four_dimensions, automatic = _services()
    four_dimensions.upsert_automatic_record(
        "account-alice",
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content="Transformer",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )
    original_correct = four_dimensions.correct_record
    calls = 0

    def flaky_correct(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AutomaticProfileError("transient", retryable=True)
        return original_correct(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(four_dimensions, "correct_record", flaky_correct)
    result = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-correction-retry",
        content="把我的关注点换成 CNN",
        run_id="run-retry",
        mode="companion",
    )

    assert result.correction is not None
    assert result.correction.status == ProfileCorrectionStatus.FAILED
    assert result.run.status.value == "pending"
    assert "succeeded" in automatic.run_retry_tick()
    record = four_dimensions.list_records("account-alice")[0]
    assert record.content == "CNN"
    assert record.correction_count == 1


def test_sqlite_correction_survives_restart_and_replay(tmp_path: Path) -> None:
    database_path = tmp_path / "profile-correction.db"
    database = BridgesDatabase(database_path)
    four_dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=SqliteFourDimensionProfileRepository(database),
    )
    automatic = AutomaticProfileService(
        four_dimension_service=four_dimensions,
        repository=SqliteAutomaticProfileRepository(database),
    )
    original = four_dimensions.upsert_automatic_record(
        "account-alice",
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content="Transformer",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )
    result = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-correction-sqlite",
        content="把我的关注点换成 CNN",
        run_id="run-sqlite",
        mode="companion",
    )
    assert result.correction is not None
    assert result.correction.status == ProfileCorrectionStatus.WRITTEN
    database.close()

    reopened = BridgesDatabase(database_path)
    reopened_four_dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=SqliteFourDimensionProfileRepository(reopened),
    )
    reopened_automatic = AutomaticProfileService(
        four_dimension_service=reopened_four_dimensions,
        repository=SqliteAutomaticProfileRepository(reopened),
    )
    restored = reopened_four_dimensions.list_records("account-alice")[0]
    replay = reopened_automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-correction-sqlite",
        content="把我的关注点换成 CNN",
        run_id="run-sqlite-replay",
        mode="companion",
    )

    assert restored.record_id == original.record_id
    assert restored.content == "CNN"
    assert restored.correction_count == 1
    assert replay.run.outcome == ProfileExtractionOutcome.SUCCEEDED_CORRECTION_WRITTEN
    assert reopened_four_dimensions.list_records("account-alice")[0].correction_count == 1
    reopened.close()
