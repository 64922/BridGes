from __future__ import annotations

from pathlib import Path

import pytest

from bridges.contracts.atomic_profile import AtomicProfileItemModifyRequest
from bridges.contracts.profile_extraction import (
    ProfileCorrectionStatus,
    ProfileExtractionOutcome,
)
from bridges.contracts.profiles import (
    FourDimension,
)
from bridges.profiles import (
    AtomicProfileService,
    AutomaticProfileService,
    FourDimensionProfileService,
    InMemoryAtomicProfileRepository,
    InMemoryAutomaticProfileRepository,
    InMemoryFourDimensionProfileRepository,
    RuleBasedAutomaticProfileExtractor,
    SqliteAtomicProfileRepository,
    SqliteAutomaticProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles.automatic import AutomaticProfileError
from bridges.profiles.signals import ProfileSignalCategory, ProfileSignalClassifier
from bridges.storage.database import BridgesDatabase


def _services() -> tuple[
    FourDimensionProfileService, AutomaticProfileService, AtomicProfileService
]:
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
    return four_dimensions, automatic, atomic


def _seed(automatic: AutomaticProfileService, content: str, number: int) -> None:
    """经明确学习写出一条知识兴趣条目，供更正定位。"""

    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-seed",
        message_id=f"message-seed-{number}",
        content=content,
        run_id=f"run-seed-{number}",
        mode="companion",
    )


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
    four_dimensions, automatic, atomic = _services()
    _seed(automatic, "我想学习 Transformer 的相关基础知识", 1)
    original = atomic.list_items("account-alice")[0].model_copy(deep=True)

    result = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-correction-1",
        content="我改主意了，把这条画像内容修改成我喜欢学习卷积神经网络相关知识",
        run_id="run-1",
        mode="companion",
    )

    updated = atomic.list_items("account-alice")[0]
    assert result.run.outcome == ProfileExtractionOutcome.SUCCEEDED_CORRECTION_WRITTEN
    assert result.correction is not None
    assert result.correction.status == ProfileCorrectionStatus.WRITTEN
    assert result.correction.dimension == FourDimension.KNOWLEDGE_INTEREST
    assert updated.profile_item_id == original.profile_item_id
    assert updated.text == "卷积神经网络相关知识"
    assert updated.user_edited_at is not None
    assert updated.version == original.version + 1
    assert updated.updated_at >= original.updated_at


def test_chat_correction_uses_latest_record_and_is_idempotent() -> None:
    four_dimensions, automatic, atomic = _services()
    _seed(automatic, "我想学习旧的知识兴趣", 1)
    _seed(automatic, "我想学习较新的知识兴趣", 2)
    items = atomic.list_items("account-alice")
    older = next(item for item in items if item.text == "我想学习旧的知识兴趣")

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

    items = atomic.list_items("account-alice")
    latest = next(item for item in items if item.text == "CNN")
    unchanged = next(
        item for item in items if item.profile_item_id == older.profile_item_id
    )
    assert result.correction is not None
    assert result.correction.status == ProfileCorrectionStatus.WRITTEN
    assert latest.text == "CNN"
    assert replay.run.outcome == ProfileExtractionOutcome.SUCCEEDED_CORRECTION_WRITTEN
    assert latest.text == "CNN"


def test_page_edited_item_wins_over_chat_correction() -> None:
    """工单 03：页面编辑是最高权威；聊天更正不改写用户版本。"""

    four_dimensions, automatic, atomic = _services()
    _seed(automatic, "我想学习 Transformer 的相关基础知识", 1)
    item = atomic.list_items("account-alice")[0]
    edited = atomic.modify_item(
        "account-alice",
        item.profile_item_id,
        AtomicProfileItemModifyRequest(text="手动纠正后的兴趣", version=item.version),
    )

    result = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-correction-protected",
        content="把我的关注点换成 CNN",
        run_id="run-protected",
        mode="companion",
    )

    after = atomic.list_items("account-alice")[0]
    assert result.correction is not None
    assert result.correction.status == ProfileCorrectionStatus.WRITTEN
    assert after.profile_item_id == edited.profile_item_id
    assert after.text == "手动纠正后的兴趣"


def test_unresolved_correction_keeps_profile_unchanged() -> None:
    four_dimensions, automatic, atomic = _services()
    _seed(automatic, "我想学习 Transformer 的相关基础知识", 1)
    original = atomic.list_items("account-alice")[0]

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
    assert atomic.list_items("account-alice")[0] == original


def test_correction_without_active_record_does_not_claim_a_write() -> None:
    _, automatic, _ = _services()

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
    four_dimensions, automatic, atomic = _services()
    _seed(automatic, "我想学习 Transformer 的相关基础知识", 1)
    original_correct = atomic.correct_fact
    calls = 0

    def flaky_correct(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AutomaticProfileError("transient", retryable=True)
        return original_correct(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(atomic, "correct_fact", flaky_correct)
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
    item = atomic.list_items("account-alice")[0]
    assert item.text == "CNN"


def test_sqlite_correction_survives_restart_and_replay(tmp_path: Path) -> None:
    database_path = tmp_path / "profile-correction.db"
    database = BridgesDatabase(database_path)
    four_dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=SqliteFourDimensionProfileRepository(database),
    )
    atomic = AtomicProfileService(
        four_dimensions, SqliteAtomicProfileRepository(database)
    )
    automatic = AutomaticProfileService(
        four_dimension_service=four_dimensions,
        repository=SqliteAutomaticProfileRepository(database),
        extractor=RuleBasedAutomaticProfileExtractor(),
        atomic_profile_service=atomic,
    )
    _seed(automatic, "我想学习 Transformer 的相关基础知识", 1)
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
    reopened_atomic = AtomicProfileService(
        reopened_four_dimensions, SqliteAtomicProfileRepository(reopened)
    )
    reopened_automatic = AutomaticProfileService(
        four_dimension_service=reopened_four_dimensions,
        repository=SqliteAutomaticProfileRepository(reopened),
        extractor=RuleBasedAutomaticProfileExtractor(),
        atomic_profile_service=reopened_atomic,
    )
    restored = reopened_atomic.list_items("account-alice")[0]
    replay = reopened_automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-correction-sqlite",
        content="把我的关注点换成 CNN",
        run_id="run-sqlite-replay",
        mode="companion",
    )

    assert restored.text == "CNN"
    assert replay.run.outcome == ProfileExtractionOutcome.SUCCEEDED_CORRECTION_WRITTEN
    assert reopened_atomic.list_items("account-alice")[0].text == "CNN"
    reopened.close()
