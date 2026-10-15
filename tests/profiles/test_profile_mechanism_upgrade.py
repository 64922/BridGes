"""Issue 06：把握度、证据、纠错降级、真删与隐私边界。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from bridges.chat.turn import profile_slice_context
from bridges.contracts.atomic_profile import AtomicProfileItemModifyRequest
from bridges.contracts.profiles import (
    FourDimension,
    FourDimensionConfidence,
    ProfileSensitivityClass,
    ProfileSlice,
    ProfileSliceItem,
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
from bridges.storage import BridgesDatabase


def _in_memory_services() -> tuple[
    FourDimensionProfileService, AutomaticProfileService, AtomicProfileService
]:
    target = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    atomic = AtomicProfileService(target, InMemoryAtomicProfileRepository())
    automatic = AutomaticProfileService(
        four_dimension_service=target,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
        atomic_profile_service=atomic,
    )
    return target, automatic, atomic


def test_first_self_statement_is_medium_and_repeated_message_becomes_high() -> None:
    target, automatic, atomic = _in_memory_services()

    first = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我想学习物理",
        run_id="run-1",
        mode="study",
    )
    item = atomic.list_items("account-alice")[0]
    assert item.confidence == FourDimensionConfidence.MEDIUM
    assert item.evidence_quote == "我想学习物理"
    assert item.source_message_ids == ["message-1"]
    assert first.observed_count == 1

    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-2",
        message_id="message-2",
        content="我想学习物理",
        run_id="run-2",
        mode="study",
    )
    repeated = atomic.list_items("account-alice")[0]
    assert repeated.text == "我想学习物理"
    assert repeated.confidence == FourDimensionConfidence.HIGH
    assert repeated.source_message_ids == ["message-1", "message-2"]


def test_explicit_preference_change_coexists_without_forced_overwrite() -> None:
    """工单 03：兴趣不再按维度槽强制覆盖；两个偏好作为并行事实并存。"""

    target, automatic, atomic = _in_memory_services()

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

    items = atomic.list_items("account-alice")
    assert {item.text for item in items} == {"我喜欢游泳", "我现在更喜欢跑步"}


def test_explicit_confirmation_is_stronger_than_a_first_self_statement() -> None:
    target, automatic, atomic = _in_memory_services()

    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="对，我喜欢跑步",
        run_id="run-1",
        mode="companion",
    )

    item = atomic.list_items("account-alice")[0]
    assert item.text == "我喜欢跑步"
    assert item.confidence == FourDimensionConfidence.HIGH


def test_question_observations_do_not_upgrade_a_later_self_statement() -> None:
    target, automatic, atomic = _in_memory_services()

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

    items = atomic.list_items("account-alice")
    assert len(items) == 1
    assert items[0].text == "我想学习物理"
    assert items[0].confidence == FourDimensionConfidence.MEDIUM


def test_repeated_chat_corrections_keep_applying_without_degradation() -> None:
    """工单 03：连续聊天更正直接改写同一条目；旧的四维降级保护不迁移。"""

    target, automatic, atomic = _in_memory_services()
    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我想学习物理",
        run_id="run-1",
        mode="study",
    )

    for text in ("我想学习化学", "我想学习生物", "我想学习数学"):
        item, changed = atomic.correct_fact(
            "account-alice",
            dimension=FourDimension.KNOWLEDGE_INTEREST,
            text=text,
        )
        assert changed
        assert item is not None

    items = atomic.list_items("account-alice")
    assert [item.text for item in items] == ["我想学习数学"]
    assert items[0].confidence == FourDimensionConfidence.HIGH
    slice_ = atomic.compile_chat_slice(
        "account-alice",
        run_id="assistant-1",
        current_question="帮我安排数学的学习计划",
    )
    assert [item.value_or_rule for item in slice_.included_items] == [
        "我想学习数学"
    ]


def test_true_delete_removes_item_and_replay_cannot_revive(tmp_path: Path) -> None:
    """工单 03：删除是原子墓碑；旧消息重放与重启都不再复活。"""

    database = BridgesDatabase(tmp_path / "bridges.db")
    automatic_repository = SqliteAutomaticProfileRepository(database)
    target_repository = SqliteFourDimensionProfileRepository(database)
    atomic_repository = SqliteAtomicProfileRepository(database)
    target = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=target_repository,
    )
    atomic = AtomicProfileService(target, atomic_repository)
    automatic = AutomaticProfileService(
        four_dimension_service=target,
        repository=automatic_repository,
        extractor=RuleBasedAutomaticProfileExtractor(),
        atomic_profile_service=atomic,
    )
    automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我想学习物理",
        run_id="run-1",
        mode="study",
    )
    item = atomic.list_items("account-alice")[0]
    edited = atomic.modify_item(
        "account-alice",
        item.profile_item_id,
        AtomicProfileItemModifyRequest(text="我想学习化学", version=item.version),
    )

    atomic.delete_item("account-alice", edited.profile_item_id, edited.version)

    assert atomic.list_items("account-alice") == []
    tombstones = atomic_repository.list_items("account-alice", include_withdrawn=True)
    deleted = next(
        tombstone
        for tombstone in tombstones
        if tombstone.profile_item_id == edited.profile_item_id
    )
    assert deleted.status.value == "withdrawn"

    replay = automatic.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我想学习物理",
        run_id="run-2",
        mode="study",
    )
    assert replay.run.status == "succeeded"
    # 墓碑抑制复活：重放不写回被删除的事实。
    assert atomic.list_items("account-alice") == []
    database.close()


def test_do_not_record_stops_new_observations_and_future_retries() -> None:
    target, automatic, atomic = _in_memory_services()

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
    assert atomic.list_items("account-alice") == []
    assert automatic._repository.list_observations(  # type: ignore[attr-defined]
        "account-alice",
        FourDimension.KNOWLEDGE_INTEREST,
        "物理",
        since=datetime.now(UTC) - timedelta(days=1),
    ) == []


def test_local_do_not_record_only_blocks_the_named_content() -> None:
    target, automatic, atomic = _in_memory_services()

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

    items = atomic.list_items("account-alice")
    assert [item.text for item in items] == ["我喜欢游泳"]


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
