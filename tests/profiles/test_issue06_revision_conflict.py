"""画像工单 06：区分重复、纠错、变化与事实冲突。

覆盖验收：同一事实换说法只增加依据；「说错了」与「现在变了」历史语义
不同；可并行事实并存；手动编辑不被自动消息覆盖；未解决冲突不作确定
判断、只在问题依赖该属性时澄清；并发修改、重复提交与错误目标有真实
存储测试且不丢版本和依据；冲突数据支持迁移、导出、删除与恢复。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bridges.contracts.atomic_profile import (
    AtomicProfileConflictState,
    AtomicProfileFactRelation,
    AtomicProfileItem,
    AtomicProfileItemModifyRequest,
    AtomicProfileItemStatus,
    AtomicProfileRevisionKind,
    AtomicProfileWriteOrigin,
)
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    RECALL_CONFLICT_REASON,
    AtomicProfileError,
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    SqliteAtomicProfileRepository,
    identity_key,
)
from bridges.profiles.commit import ProfileFactSubmission
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.signals import ProfileSignalCategory, ProfileSignalClassifier
from bridges.storage.database import BridgesDatabase

ACCOUNT = "account-alice"


def _atomic_service() -> tuple[InMemoryAtomicProfileRepository, AtomicProfileService]:
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    repository = InMemoryAtomicProfileRepository()
    return repository, AtomicProfileService(four, repository)


def _sqlite_service(
    tmp_path: Path,
) -> tuple[BridgesDatabase, SqliteAtomicProfileRepository, AtomicProfileService]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    repository = SqliteAtomicProfileRepository(database, initialize=False)
    return database, repository, AtomicProfileService(four, repository)


def _fact(
    text: str,
    *,
    dimension: FourDimension | None = FourDimension.ACADEMIC_STATUS,
    message_id: str | None = None,
    evidence_quote: str | None = None,
    confidence: FourDimensionConfidence = FourDimensionConfidence.HIGH,
) -> ProfileFactSubmission:
    return ProfileFactSubmission(
        text=text,
        dimension=dimension,
        confidence=confidence,
        evidence_quote=evidence_quote,
        evidence_message_id=message_id,
    )


def _texts(atomic: AtomicProfileService) -> set[str]:
    return {item.text for item in atomic.list_items(ACCOUNT)}


def _item(atomic: AtomicProfileService, text: str) -> AtomicProfileItem:
    return next(item for item in atomic.list_items(ACCOUNT) if item.text == text)


def _historical(
    repository: InMemoryAtomicProfileRepository | SqliteAtomicProfileRepository,
    *,
    status: AtomicProfileItemStatus = AtomicProfileItemStatus.SUPERSEDED,
    fact_object: str | None = None,
) -> list[AtomicProfileItem]:
    return [
        item
        for item in repository.list_items(ACCOUNT, include_withdrawn=True)
        if item.status == status
        and (fact_object is None or item.fact_object == fact_object)
    ]


# ---------------------------------------------------------------------------
# 重复与关系：换说法只加依据，关系不同不误合并
# ---------------------------------------------------------------------------


def test_reworded_restatement_merges_evidence_into_one_item() -> None:
    repository, atomic = _atomic_service()
    first = atomic.commit_fact(
        ACCOUNT,
        _fact(
            "我喜欢跑步",
            dimension=FourDimension.HOBBY,
            message_id="message-1",
            evidence_quote="我真的很喜欢跑步",
        ),
    )
    second = atomic.commit_fact(
        ACCOUNT,
        _fact(
            "我特别喜欢跑步",
            dimension=FourDimension.HOBBY,
            message_id="message-2",
            evidence_quote="平时我最喜欢跑步了",
        ),
    )

    assert first is not None and second is not None
    assert _texts(atomic) == {"我特别喜欢跑步"}
    merged = _item(atomic, "我特别喜欢跑步")
    assert merged.source_message_ids == ["message-1", "message-2"]
    assert repository.list_items(ACCOUNT) and len(repository.list_items(ACCOUNT)) == 1


def test_related_relations_do_not_auto_merge() -> None:
    _, atomic = _atomic_service()
    atomic.commit_fact(
        ACCOUNT,
        _fact(
            "我喜欢Python",
            dimension=FourDimension.KNOWLEDGE_INTEREST,
            message_id="message-1",
        ),
    )
    atomic.commit_fact(
        ACCOUNT,
        _fact(
            "我正在学习Python",
            dimension=FourDimension.KNOWLEDGE_INTEREST,
            message_id="message-2",
        ),
    )

    assert _texts(atomic) == {"我喜欢Python", "我正在学习Python"}
    assert _item(atomic, "我喜欢Python").fact_relation == (
        AtomicProfileFactRelation.INTEREST
    )
    assert _item(atomic, "我正在学习Python").fact_relation == (
        AtomicProfileFactRelation.LEARNING
    )


# ---------------------------------------------------------------------------
# 纠错与状态变化：不同的历史语义
# ---------------------------------------------------------------------------


def test_state_change_keeps_old_value_as_real_history() -> None:
    repository, atomic = _atomic_service()
    atomic.commit_fact(
        ACCOUNT,
        _fact("大二", evidence_quote="我现在大二", message_id="message-1"),
    )
    atomic.commit_fact(
        ACCOUNT,
        _fact("大三", evidence_quote="我现在升大三了", message_id="message-2"),
    )

    assert _texts(atomic) == {"大三"}
    old = _historical(repository, fact_object="大二")
    assert len(old) == 1
    assert old[0].retired_reason == AtomicProfileRevisionKind.STATE_CHANGE
    assert old[0].valid_until is not None
    assert old[0].text == "大二"


def test_explicit_correction_does_not_keep_old_value_as_experience() -> None:
    repository, atomic = _atomic_service()
    atomic.commit_fact(
        ACCOUNT,
        _fact("大四", evidence_quote="我是大四", message_id="message-1"),
    )

    corrected = atomic.correct_fact(
        ACCOUNT, dimension=FourDimension.ACADEMIC_STATUS, text="大三"
    )

    assert corrected[1] is True
    assert _texts(atomic) == {"大三"}
    # 更正后的旧值是错误记录：没有可当真实经历展示的被替代版本。
    assert _historical(repository, fact_object="大四") == []
    # 旧消息重放不得把被更正掉的错误值写回来。
    assert (
        atomic.commit_fact(
            ACCOUNT,
            _fact("大四", evidence_quote="我是大四", message_id="message-1"),
        )
        is None
    )
    assert _texts(atomic) == {"大三"}


def test_state_change_after_correction_keeps_corrected_value_as_history() -> None:
    """T07：先说错被更正的年级，之后真实升学仍保留为历史。"""

    repository, atomic = _atomic_service()
    atomic.commit_fact(
        ACCOUNT,
        _fact("大四", evidence_quote="我是大四", message_id="message-1"),
    )
    corrected, written = atomic.correct_fact(
        ACCOUNT, dimension=FourDimension.ACADEMIC_STATUS, text="大三"
    )

    assert written is True
    assert corrected is not None
    assert corrected.user_edited_at is not None

    tombstone = _historical(
        repository,
        status=AtomicProfileItemStatus.WITHDRAWN,
        fact_object="大四",
    )
    assert len(tombstone) == 1
    changed = atomic.commit_fact(
        ACCOUNT,
        _fact("大四", evidence_quote="我现在升大四了", message_id="message-2"),
        source_at=tombstone[0].created_at + timedelta(seconds=1),
    )

    assert changed is not None
    assert changed.conflict_state == AtomicProfileConflictState.NONE
    assert _texts(atomic) == {"大四"}
    old = _historical(repository, fact_object="大三")
    assert len(old) == 1
    assert old[0].retired_reason == AtomicProfileRevisionKind.STATE_CHANGE
    assert old[0].valid_until is not None


def test_tombstone_reopen_requires_source_time_after_retirement() -> None:
    """被更正掉的错误值：无来源时间或来源早于退役的新说法不得重开。"""

    repository, atomic = _atomic_service()
    atomic.commit_fact(
        ACCOUNT,
        _fact("大四", evidence_quote="我是大四", message_id="message-1"),
    )
    atomic.correct_fact(ACCOUNT, dimension=FourDimension.ACADEMIC_STATUS, text="大三")
    tombstone = _historical(
        repository,
        status=AtomicProfileItemStatus.WITHDRAWN,
        fact_object="大四",
    )
    assert len(tombstone) == 1

    marked = _fact("大四", evidence_quote="我现在升大四了", message_id="message-2")
    assert atomic.commit_fact(ACCOUNT, marked) is None
    assert (
        atomic.commit_fact(
            ACCOUNT,
            marked,
            source_at=tombstone[0].created_at - timedelta(seconds=1),
        )
        is None
    )
    assert _texts(atomic) == {"大三"}


def test_markerless_value_after_correction_stays_unresolved() -> None:
    """会话更正的值不被无标记自动说法顶替，只进未解决冲突。"""

    _, atomic = _atomic_service()
    atomic.commit_fact(
        ACCOUNT,
        _fact("大四", evidence_quote="我是大四", message_id="message-1"),
    )
    atomic.correct_fact(ACCOUNT, dimension=FourDimension.ACADEMIC_STATUS, text="大三")

    later = atomic.commit_fact(
        ACCOUNT,
        _fact("大二", evidence_quote="我是大二", message_id="message-2"),
    )

    assert later is not None
    assert later.conflict_state == AtomicProfileConflictState.UNRESOLVED
    corrected = _item(atomic, "大三")
    assert corrected.conflict_state == AtomicProfileConflictState.NONE
    assert corrected.status == AtomicProfileItemStatus.ACTIVE


def test_commit_path_correction_marks_wrong_value_without_history_window() -> None:
    repository, atomic = _atomic_service()
    atomic.commit_fact(
        ACCOUNT,
        _fact("大二", evidence_quote="我现在大二", message_id="message-1"),
    )
    atomic.commit_fact(
        ACCOUNT,
        _fact(
            "大三",
            evidence_quote="之前记错了，我现在是大三",
            message_id="message-2",
        ),
    )

    assert _texts(atomic) == {"大三"}
    old = _historical(repository, fact_object="大二")
    assert len(old) == 1
    assert old[0].retired_reason == AtomicProfileRevisionKind.CORRECTION
    assert old[0].valid_until is None


# ---------------------------------------------------------------------------
# 并存：多个爱好与并行目标
# ---------------------------------------------------------------------------


def test_parallel_hobbies_and_goals_coexist_until_explicit_change() -> None:
    _, atomic = _atomic_service()
    atomic.commit_fact(
        ACCOUNT,
        _fact("我喜欢跑步", dimension=FourDimension.HOBBY, message_id="message-1"),
    )
    atomic.commit_fact(
        ACCOUNT,
        _fact("我喜欢游泳", dimension=FourDimension.HOBBY, message_id="message-2"),
    )
    atomic.commit_fact(
        ACCOUNT,
        _fact("我计划考研", dimension=FourDimension.STAGE_GOAL, message_id="message-3"),
    )
    atomic.commit_fact(
        ACCOUNT,
        _fact("我准备找工作", dimension=FourDimension.STAGE_GOAL, message_id="message-4"),
    )

    assert _texts(atomic) == {
        "我喜欢跑步",
        "我喜欢游泳",
        "我计划考研",
        "我准备找工作",
    }

    atomic.commit_fact(
        ACCOUNT,
        _fact(
            "我不考研了，转为准备就业",
            dimension=FourDimension.STAGE_GOAL,
            message_id="message-5",
        ),
    )

    assert _texts(atomic) == {
        "我喜欢跑步",
        "我喜欢游泳",
        "我准备找工作",
        "我不考研了，转为准备就业",
    }


# ---------------------------------------------------------------------------
# 冲突：无明确信号的矛盾保留不确定性
# ---------------------------------------------------------------------------


def _seed_major_conflict(
    atomic: AtomicProfileService,
) -> tuple[AtomicProfileItem, AtomicProfileItem]:
    first = atomic.commit_fact(
        ACCOUNT,
        _fact(
            "我是软件工程专业",
            evidence_quote="我是软件工程专业",
            message_id="message-1",
        ),
    )
    second = atomic.commit_fact(
        ACCOUNT,
        _fact(
            "我是计算机专业",
            evidence_quote="我是计算机专业",
            message_id="message-2",
        ),
    )
    assert first is not None and second is not None
    return first, second


def test_ambiguous_contradiction_is_kept_unresolved() -> None:
    _, atomic = _atomic_service()
    first, second = _seed_major_conflict(atomic)

    items = atomic.list_items(ACCOUNT)
    assert {item.text for item in items} == {"我是软件工程专业", "我是计算机专业"}
    for item in items:
        assert item.conflict_state == AtomicProfileConflictState.UNRESOLVED
    assert first.status == AtomicProfileItemStatus.ACTIVE
    assert second.status == AtomicProfileItemStatus.ACTIVE

    projections = {entry.text: entry for entry in atomic.projections(ACCOUNT)}
    assert (
        projections["我是计算机专业"].conflict_state
        == AtomicProfileConflictState.UNRESOLVED
    )


def test_unresolved_conflict_is_not_used_as_definite_fact() -> None:
    _, atomic = _atomic_service()
    _seed_major_conflict(atomic)

    slice_ = atomic.compile_chat_slice(
        ACCOUNT, run_id="run-1", current_question="我该选什么专业课程？"
    )

    assert slice_.included_items == []
    reasons = {item.exclusion_reason for item in slice_.unused_items}
    assert reasons == {RECALL_CONFLICT_REASON}


def test_conflict_clarification_only_when_question_depends_on_slot() -> None:
    _, atomic = _atomic_service()
    _seed_major_conflict(atomic)

    pending = atomic.pending_conflicts(ACCOUNT)
    assert len(pending) == 1
    conflict = pending[0]
    assert {item.text for item in conflict.items} == {
        "我是软件工程专业",
        "我是计算机专业",
    }
    assert "我是软件工程专业" in conflict.question
    assert "我是计算机专业" in conflict.question
    assert "以哪个为准" in conflict.question

    relevant = atomic.conflict_clarifications(
        ACCOUNT, question="帮我推荐几个计算机相关的选修课"
    )
    assert len(relevant) == 1

    unrelated = atomic.conflict_clarifications(ACCOUNT, question="今晚吃什么好")
    assert unrelated == []


def test_marked_state_change_resolves_conflict() -> None:
    repository, atomic = _atomic_service()
    _seed_major_conflict(atomic)

    atomic.commit_fact(
        ACCOUNT,
        _fact(
            "我是计算机专业",
            evidence_quote="我现在转专业到计算机专业了",
            message_id="message-3",
        ),
    )

    items = atomic.list_items(ACCOUNT)
    assert {item.text for item in items} == {"我是计算机专业"}
    assert items[0].conflict_state == AtomicProfileConflictState.NONE
    old = _historical(repository, fact_object="软件工程")
    assert len(old) == 1
    assert old[0].retired_reason == AtomicProfileRevisionKind.STATE_CHANGE


def test_explicit_correction_resolves_conflict() -> None:
    repository, atomic = _atomic_service()
    _seed_major_conflict(atomic)

    corrected = atomic.correct_fact(
        ACCOUNT, dimension=FourDimension.ACADEMIC_STATUS, text="计算机专业"
    )

    assert corrected[1] is True
    items = atomic.list_items(ACCOUNT)
    assert len(items) == 1
    assert items[0].fact_relation == AtomicProfileFactRelation.MAJOR
    assert items[0].fact_object == "计算机"
    assert items[0].conflict_state == AtomicProfileConflictState.NONE
    assert _historical(repository, fact_object="软件工程")


def test_user_edit_resolves_conflict_on_that_entry() -> None:
    _, atomic = _atomic_service()
    first, _second = _seed_major_conflict(atomic)

    fresh = _item(atomic, "我是软件工程专业")
    assert fresh.profile_item_id == first.profile_item_id
    edited = atomic.modify_item(
        ACCOUNT,
        fresh.profile_item_id,
        AtomicProfileItemModifyRequest(text="我是网络工程专业", version=fresh.version),
    )

    assert edited.conflict_state == AtomicProfileConflictState.NONE
    peer = _item(atomic, "我是计算机专业")
    assert peer.conflict_state == AtomicProfileConflictState.UNRESOLVED

    # 一侧被编辑消解后，澄清问句仍须展示双方当前值，不能只引用单条。
    pending = atomic.pending_conflicts(ACCOUNT)
    assert len(pending) == 1
    assert {item.text for item in pending[0].items} == {
        "我是网络工程专业",
        "我是计算机专业",
    }
    assert "我是网络工程专业" in pending[0].question
    assert "我是计算机专业" in pending[0].question
    assert "以哪个为准" in pending[0].question


# ---------------------------------------------------------------------------
# 用户权威：普通自动信息不能覆盖手动编辑
# ---------------------------------------------------------------------------


def test_automatic_commit_cannot_retire_user_edited_slot_value() -> None:
    _, atomic = _atomic_service()
    remembered = atomic.remember(ACCOUNT, "我是大二学生")

    marked = atomic.commit_fact(
        ACCOUNT,
        _fact("大四", evidence_quote="我现在升大四了", message_id="message-9"),
    )
    unmarked = atomic.commit_fact(
        ACCOUNT,
        _fact("大三", evidence_quote="我是大三", message_id="message-10"),
    )

    assert marked is not None and unmarked is not None
    assert marked.conflict_state == AtomicProfileConflictState.UNRESOLVED
    assert unmarked.conflict_state == AtomicProfileConflictState.UNRESOLVED
    stored = _item(atomic, "我是大二学生")
    assert stored.profile_item_id == remembered.profile_item_id
    assert stored.status == AtomicProfileItemStatus.ACTIVE
    assert stored.conflict_state == AtomicProfileConflictState.NONE


# ---------------------------------------------------------------------------
# 信号扩展：说错/记错与「不是A，是B」进入纠错意图
# ---------------------------------------------------------------------------


def test_error_admission_is_classified_as_correction_with_intent() -> None:
    classifier = ProfileSignalClassifier()

    result = classifier.classify("之前说错了，我不是大四，是大三。")

    assert result.category == ProfileSignalCategory.CORRECTION
    assert result.correction_intent is not None
    assert result.correction_intent.dimension == FourDimension.ACADEMIC_STATUS
    assert result.correction_intent.new_value == "大三"


def test_unrelated_negation_stays_forbidden() -> None:
    classifier = ProfileSignalClassifier()

    assert (
        classifier.classify("我不是不想学，只是没时间").category
        == ProfileSignalCategory.FORBIDDEN
    )
    assert (
        classifier.classify("我没有考上研究生，准备再考一次").category
        == ProfileSignalCategory.FORBIDDEN
    )


def test_upgrade_statement_remains_schedulable() -> None:
    classifier = ProfileSignalClassifier()

    result = classifier.classify("我现在升大四了。")

    assert result.category != ProfileSignalCategory.FORBIDDEN
    assert result.should_process is True


# ---------------------------------------------------------------------------
# 真实存储：并发修改、重复提交、错误目标与冲突数据持久化
# ---------------------------------------------------------------------------


def test_late_worker_commit_does_not_override_user_edit(tmp_path: Path) -> None:
    database, repository, atomic = _sqlite_service(tmp_path)
    try:
        original = atomic.commit_fact(
            ACCOUNT,
            _fact("大二", evidence_quote="我现在大二", message_id="message-old"),
        )
        assert original is not None
        edited = atomic.modify_item(
            ACCOUNT,
            original.profile_item_id,
            AtomicProfileItemModifyRequest(
                text="我现在大三了", version=original.version
            ),
        )

        # 迟到的同事实旧提取：按用户版本原样返回，不追加证据、不改版本。
        late_same_fact = atomic.commit_fact(
            ACCOUNT,
            _fact("大三", evidence_quote="我现在大三了", message_id="message-late"),
        )
        assert late_same_fact is not None
        assert late_same_fact.profile_item_id == edited.profile_item_id
        assert late_same_fact.text == "我现在大三了"
        assert late_same_fact.version == edited.version
        assert late_same_fact.source_message_ids == original.source_message_ids

        # 迟到的旧值重放：被编辑产生的抑制键拦住，不复活。
        assert (
            atomic.commit_fact(
                ACCOUNT,
                _fact("大二", evidence_quote="我现在大二", message_id="message-old"),
            )
            is None
        )

        stored = repository.get_item(ACCOUNT, edited.profile_item_id)
        assert stored.text == "我现在大三了"
        assert stored.version == edited.version
        assert stored.user_edited_at is not None
    finally:
        database.close()


def test_duplicate_submission_is_idempotent_and_keeps_evidence(tmp_path: Path) -> None:
    database, repository, atomic = _sqlite_service(tmp_path)
    try:
        payload = _fact(
            "我喜欢跑步",
            dimension=FourDimension.HOBBY,
            evidence_quote="我喜欢跑步",
            message_id="message-1",
        )
        first = atomic.commit_fact(ACCOUNT, payload)
        replay = atomic.commit_fact(ACCOUNT, payload)

        assert first is not None and replay is not None
        items = repository.list_items(ACCOUNT)
        assert len(items) == 1
        assert items[0].source_message_ids == ["message-1"]
        assert items[0].evidence_quote == "我喜欢跑步"
        assert replay.profile_item_id == first.profile_item_id
    finally:
        database.close()


def test_stale_target_version_is_rejected_without_overwrite(tmp_path: Path) -> None:
    database, repository, atomic = _sqlite_service(tmp_path)
    try:
        item = atomic.commit_fact(
            ACCOUNT,
            _fact("我喜欢跑步", dimension=FourDimension.HOBBY, message_id="message-1"),
        )
        assert item is not None
        updated = atomic.modify_item(
            ACCOUNT,
            item.profile_item_id,
            AtomicProfileItemModifyRequest(text="我喜欢游泳", version=item.version),
        )

        with pytest.raises(AtomicProfileError, match="版本冲突，请刷新后重试。"):
            atomic.modify_item(
                ACCOUNT,
                item.profile_item_id,
                AtomicProfileItemModifyRequest(
                    text="我喜欢跑步", version=item.version
                ),
            )
        with pytest.raises(AtomicProfileError, match="版本冲突，请刷新后重试。"):
            atomic.delete_item(ACCOUNT, item.profile_item_id, item.version)

        stored = repository.get_item(ACCOUNT, item.profile_item_id)
        assert stored.text == "我喜欢游泳"
        assert stored.version == updated.version
        assert stored.status == AtomicProfileItemStatus.ACTIVE
    finally:
        database.close()


def test_conflict_and_revision_data_survive_reopen(tmp_path: Path) -> None:
    database, _, atomic = _sqlite_service(tmp_path)
    _seed_major_conflict(atomic)
    atomic.commit_fact(
        ACCOUNT,
        _fact("大二", evidence_quote="我现在大二", message_id="grade-1"),
    )
    atomic.commit_fact(
        ACCOUNT,
        _fact("大三", evidence_quote="我现在升大三了", message_id="grade-2"),
    )
    database.close()

    reopened_db = BridgesDatabase(tmp_path / "bridges.db")
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    reopened_repository = SqliteAtomicProfileRepository(reopened_db, initialize=False)
    reopened = AtomicProfileService(four, reopened_repository)

    items = reopened.list_items(ACCOUNT)
    conflicted = [
        item
        for item in items
        if item.fact_relation == AtomicProfileFactRelation.MAJOR
    ]
    assert len(conflicted) == 2
    assert all(
        item.conflict_state == AtomicProfileConflictState.UNRESOLVED
        for item in conflicted
    )
    grade = [item for item in items if item.text == "大三"]
    assert len(grade) == 1
    assert grade[0].conflict_state == AtomicProfileConflictState.NONE

    old = [
        item
        for item in reopened_repository.list_items(ACCOUNT, include_withdrawn=True)
        if item.fact_object == "大二"
    ]
    assert len(old) == 1
    assert old[0].retired_reason == AtomicProfileRevisionKind.STATE_CHANGE
    assert old[0].valid_until is not None
    reopened_db.close()


def test_conflict_data_supports_delete_and_recovery(tmp_path: Path) -> None:
    database, repository, atomic = _sqlite_service(tmp_path)
    try:
        first, second = _seed_major_conflict(atomic)
        fresh_first = repository.get_item(ACCOUNT, first.profile_item_id)
        atomic.delete_item(ACCOUNT, fresh_first.profile_item_id, fresh_first.version)

        remaining = repository.list_items(ACCOUNT)
        assert [item.profile_item_id for item in remaining] == [second.profile_item_id]
        tombstones = _historical(
            repository,
            status=AtomicProfileItemStatus.WITHDRAWN,
            fact_object="软件工程",
        )
        assert tombstones and tombstones[0].text == ""
        assert (
            atomic.commit_fact(
                ACCOUNT,
                _fact(
                    "我是软件工程专业",
                    evidence_quote="我是软件工程专业",
                    message_id="message-1",
                ),
            )
            is None
        )
    finally:
        database.close()


def test_legacy_item_defaults_for_revision_and_conflict_fields(
    tmp_path: Path,
) -> None:
    database, repository, atomic = _sqlite_service(tmp_path)
    try:
        legacy = AtomicProfileItem(
            profile_item_id="item-legacy",
            owner_account_id=ACCOUNT,
            text="我是大二学生",
            identity_key=identity_key(ACCOUNT, "我是大二学生"),
            status=AtomicProfileItemStatus.ACTIVE,
            write_origin=AtomicProfileWriteOrigin.AUTOMATIC,
            confidence=FourDimensionConfidence.HIGH,
            version=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        repository.save_item(legacy)

        stored = repository.get_item(ACCOUNT, "item-legacy")
        assert stored.retired_reason is None
        assert stored.conflict_state == AtomicProfileConflictState.NONE
    finally:
        database.close()
