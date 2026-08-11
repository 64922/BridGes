"""Issue 06：把握度、证据、纠错降级、真删与隐私边界。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from bridges.chat.turn import profile_slice_context
from bridges.contracts.profiles import (
    FourDimension,
    FourDimensionConfidence,
    FourDimensionProfileModifyRequest,
    ProfileSensitivityClass,
    ProfileSlice,
    ProfileSliceItem,
)
from bridges.profiles import (
    AutomaticProfileService,
    FourDimensionProfileService,
    InMemoryAutomaticProfileRepository,
    InMemoryFourDimensionProfileRepository,
    RuleBasedAutomaticProfileExtractor,
    SqliteAutomaticProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.storage import BridgesDatabase


def _in_memory_services() -> tuple[FourDimensionProfileService, AutomaticProfileService]:
    target = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    automatic = AutomaticProfileService(
        four_dimension_service=target,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
    )
    return target, automatic


def test_first_self_statement_is_medium_and_repeated_message_becomes_high() -> None:
    target, automatic = _in_memory_services()

    first = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我想学习物理",
        run_id="run-1",
        mode="study",
    )
    record = target.list_records("account-alice")[0]
    assert record.confidence == FourDimensionConfidence.MEDIUM
    assert record.evidence_quote == "我想学习物理"
    assert record.evidence_message_id == "message-1"
    assert first.observed_count == 1

    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-2",
        message_id="message-2",
        content="我想学习物理",
        run_id="run-2",
        mode="study",
    )
    repeated = target.list_records("account-alice")[0]
    assert repeated.confidence == FourDimensionConfidence.HIGH
    assert repeated.evidence_message_id == "message-2"


def test_explicit_preference_change_replaces_the_previous_value() -> None:
    target, automatic = _in_memory_services()

    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我喜欢游泳",
        run_id="run-1",
        mode="companion",
    )
    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-2",
        message_id="message-2",
        content="我现在更喜欢跑步",
        run_id="run-2",
        mode="companion",
    )

    records = target.list_records("account-alice")
    assert len(records) == 1
    assert records[0].content == "跑步"


def test_question_observations_do_not_upgrade_a_later_self_statement() -> None:
    target, automatic = _in_memory_services()

    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="question-1",
        content="什么是物理？",
        run_id="run-1",
        mode="study",
    )
    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-2",
        message_id="message-1",
        content="我想学习物理",
        run_id="run-2",
        mode="study",
    )

    records = target.list_records("account-alice")
    assert len(records) == 1
    assert records[0].confidence == FourDimensionConfidence.MEDIUM


def test_repeated_corrections_lower_confidence_and_stop_recall() -> None:
    target, automatic = _in_memory_services()
    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我想学习物理",
        run_id="run-1",
        mode="study",
    )
    record = target.list_records("account-alice")[0]

    first = target.modify_record(
        "account-alice",
        record.record_id,
        FourDimensionProfileModifyRequest(content="我想学习化学", version=record.version),
    )
    assert first.correction_count == 1
    assert first.content == "我想学习化学"
    assert first.confidence == FourDimensionConfidence.MEDIUM

    second = target.modify_record(
        "account-alice",
        first.record_id,
        FourDimensionProfileModifyRequest(content="我想学习生物", version=first.version),
    )
    assert second.correction_count == 2
    assert second.confidence == FourDimensionConfidence.LOW
    assert "反复纠错" in (second.change_note or "")

    third = target.modify_record(
        "account-alice",
        second.record_id,
        FourDimensionProfileModifyRequest(content="我想学习数学", version=second.version),
    )
    assert third.correction_count == 3
    assert third.confidence == FourDimensionConfidence.LOW
    assert "当前不可信" in (third.change_note or "")
    assert automatic.compile_chat_slice(
        "account-alice", mode="study", run_id="assistant-1"
    ).included_items == []


def test_true_delete_removes_record_and_its_observations(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    automatic_repository = SqliteAutomaticProfileRepository(database)
    target_repository = SqliteFourDimensionProfileRepository(database)
    target = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=target_repository,
        observation_delete_callback=automatic_repository.delete_observations_for_record,
    )
    automatic = AutomaticProfileService(
        four_dimension_service=target,
        repository=automatic_repository,
        extractor=RuleBasedAutomaticProfileExtractor(),
    )
    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我想学习物理",
        run_id="run-1",
        mode="study",
    )
    record = target.list_records("account-alice")[0]
    edited = target.modify_record(
        "account-alice",
        record.record_id,
        FourDimensionProfileModifyRequest(content="我想学习化学", version=record.version),
    )
    assert database.connection.execute(
        "SELECT COUNT(*) FROM profile_extraction_observations "
        "WHERE account_id = 'account-alice' AND normalized_value = '物理'"
    ).fetchone()[0] == 0

    target.delete_record("account-alice", edited.record_id, edited.version)

    assert target.list_records("account-alice") == []
    assert database.connection.execute(
        "SELECT COUNT(*) FROM profile_extraction_observations "
        "WHERE account_id = 'account-alice'"
    ).fetchone()[0] == 0
    assert database.connection.execute(
        "SELECT COUNT(*) FROM profile_four_dimension_records "
        "WHERE account_id = 'account-alice'"
    ).fetchone()[0] == 0


def test_do_not_record_stops_new_observations_and_future_retries() -> None:
    target, automatic = _in_memory_services()

    blocked = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="directive-1",
        content="不要记录",
        run_id="run-1",
        mode="companion",
    )
    assert blocked.committed_record_ids == []

    result = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我想学习物理",
        run_id="run-2",
        mode="study",
    )
    assert result.committed_record_ids == []
    assert target.list_records("account-alice") == []
    assert automatic._repository.list_observations(  # type: ignore[attr-defined]
        "account-alice",
        FourDimension.KNOWLEDGE_INTEREST,
        "物理",
        since=datetime.now(UTC) - timedelta(days=1),
    ) == []


def test_local_do_not_record_only_blocks_the_named_content() -> None:
    target, automatic = _in_memory_services()

    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="directive-1",
        content="这个不用记，我喜欢跑步",
        run_id="run-1",
        mode="companion",
    )
    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我喜欢跑步",
        run_id="run-2",
        mode="companion",
    )
    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-2",
        content="我喜欢游泳",
        run_id="run-3",
        mode="companion",
    )

    records = target.list_records("account-alice")
    assert len(records) == 1
    assert records[0].content == "游泳"


def test_model_context_uses_daily_language_without_internal_terms() -> None:
    profile_slice = ProfileSlice(
        slice_id="slice-1",
        owner_account_id="account-alice",
        run_id="run-1",
        purpose="chat:study",
        included_items=[
            ProfileSliceItem(
                assertion_id="record-1",
                dimension=FourDimension.KNOWLEDGE_INTEREST.value,
                value_or_rule="物理",
                inclusion_reason="与你当前任务相关的已授权信息",
                sensitivity_class=ProfileSensitivityClass.LEARNING,
            )
        ],
        compiled_at=datetime.now(UTC),
    )

    context = profile_slice_context(profile_slice)
    assert "物理" in context
    assert "画像" not in context
    assert "把握度" not in context

    confirmation_context = profile_slice_context(
        profile_slice, requires_confirmation=True
    )
    assert "直接向用户确认" in confirmation_context
