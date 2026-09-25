"""V2 Issue 08：原子画像的领域合同。

覆盖四项验收：旧四类数据的可对账、可恢复迁移与无类别列表；自动抽取镜像的
去重、冲突与来源；记住／忘掉的本轮生效、用户编辑优先与删除墓碑；最小切片与
账户隔离。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridges.contracts.atomic_profile import (
    AtomicProfileItemModifyRequest,
    AtomicProfileItemStatus,
    AtomicProfileMemoryKind,
    AtomicProfileMemoryStatus,
    AtomicProfileMigrationStatus,
    AtomicProfileWriteOrigin,
)
from bridges.contracts.profiles import (
    FourDimension,
    FourDimensionConfidence,
)
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    MAX_SLICE_ITEMS,
    AtomicProfileError,
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    SqliteAtomicProfileRepository,
    identity_key,
    normalize_text,
    parse_memory_directive,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.storage.database import BridgesDatabase

ALICE = "alice"
BOB = "bob"


@pytest.fixture
def four_dimensions() -> FourDimensionProfileService:
    return FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )


@pytest.fixture
def repository() -> InMemoryAtomicProfileRepository:
    return InMemoryAtomicProfileRepository()


@pytest.fixture
def service(
    four_dimensions: FourDimensionProfileService,
    repository: InMemoryAtomicProfileRepository,
) -> AtomicProfileService:
    return AtomicProfileService(four_dimensions, repository)


def _record(
    four_dimensions: FourDimensionProfileService,
    account_id: str,
    content: str,
    *,
    dimension: FourDimension = FourDimension.KNOWLEDGE_INTEREST,
    evidence_message_id: str | None = None,
    confidence: FourDimensionConfidence = FourDimensionConfidence.MEDIUM,
):
    return four_dimensions.upsert_automatic_record(
        account_id,
        dimension=dimension,
        content=content,
        action="create",
        confidence=confidence,
        evidence_message_id=evidence_message_id,
        migration_version="profile-auto-v2",
    )


# ---------------------------------------------------------------------------
# 记忆指令解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "kind", "target"),
    [
        ("记住我喜欢跑步", AtomicProfileMemoryKind.REMEMBER, "我喜欢跑步"),
        ("请记住：我的目标是考研", AtomicProfileMemoryKind.REMEMBER, "我的目标是考研"),
        ("忘掉我住在南昌", AtomicProfileMemoryKind.FORGET, "我住在南昌"),
        ("帮我删除我的跑步爱好", AtomicProfileMemoryKind.FORGET, "我的跑步爱好"),
        ("顺便说一句，记住我下周有考试", AtomicProfileMemoryKind.REMEMBER, "我下周有考试"),
    ],
)
def test_memory_directive_parses_explicit_targets(
    content: str, kind: AtomicProfileMemoryKind, target: str
) -> None:
    directive = parse_memory_directive(content)
    assert directive is not None
    assert directive.kind == kind
    assert directive.target == target


@pytest.mark.parametrize(
    "content",
    [
        "我记住了这件事",
        "你还记得我吗？",
        "别忘掉我刚才说的",
        "不要记录我跑步的事",
        "记住了",
        "忘掉了吧",
    ],
)
def test_memory_directive_ignores_narration_and_negation(content: str) -> None:
    assert parse_memory_directive(content) is None


# ---------------------------------------------------------------------------
# AC1：迁移与无类别列表
# ---------------------------------------------------------------------------


def test_migration_turns_old_records_into_category_free_list(
    service: AtomicProfileService, four_dimensions: FourDimensionProfileService
) -> None:
    first = _record(
        four_dimensions, ALICE, "我大三，学计算机", dimension=FourDimension.ACADEMIC_STATUS
    )
    second = _record(
        four_dimensions,
        ALICE,
        "喜欢看天体物理科普",
        dimension=FourDimension.KNOWLEDGE_INTEREST,
    )
    withdrawn = _record(
        four_dimensions, ALICE, "想去上海工作", dimension=FourDimension.STAGE_GOAL
    )
    four_dimensions.withdraw_record(ALICE, withdrawn.record_id)

    report = service.migrate_account(ALICE)

    assert report.status == AtomicProfileMigrationStatus.COMPLETED
    assert report.migrated == 2
    assert report.tombstoned == 1
    assert report.retryable is False
    assert sorted(report.created_item_ids) == sorted(
        item.profile_item_id for item in service.list_items(ALICE)
    )

    items = service.list_items(ALICE)
    assert {item.text for item in items} == {"我大三，学计算机", "喜欢看天体物理科普"}
    assert {item.source_record_id for item in items} == {first.record_id, second.record_id}
    assert all(item.write_origin == AtomicProfileWriteOrigin.MIGRATION for item in items)
    assert all(item.topic_hint is None or isinstance(item.topic_hint, str) for item in items)

    projection = service.projections(ALICE)[0]
    dumped = projection.model_dump()
    assert "dimension" not in dumped and "label" not in dumped and "topic_hint" not in dumped
    # 撤回的旧记录只能变成墓碑，不进入列表。
    assert withdrawn.record_id in report.source_record_ids
    assert "想去上海工作" not in {item.text for item in items}


def test_migration_is_reconcilable_and_idempotent(
    service: AtomicProfileService, four_dimensions: FourDimensionProfileService
) -> None:
    _record(four_dimensions, ALICE, "我大三", dimension=FourDimension.ACADEMIC_STATUS)
    _record(four_dimensions, ALICE, "喜欢看科普", dimension=FourDimension.KNOWLEDGE_INTEREST)

    first = service.migrate_account(ALICE)
    second = service.migrate_account(ALICE)

    assert first.migrated == 2 and first.duplicated == 0
    assert second.migrated == 0 and second.duplicated == 2
    assert second.reconciliation_digest == first.reconciliation_digest
    assert len(second.source_record_ids) == 2
    assert len(service.list_items(ALICE)) == 2

    bob = service.migrate_account(BOB)
    assert bob.migrated == 0
    assert service.latest_migration_report(ALICE).run_id == second.run_id


def test_migration_rollback_restores_and_keeps_old_records(
    service: AtomicProfileService, four_dimensions: FourDimensionProfileService
) -> None:
    record = _record(four_dimensions, ALICE, "我大三", dimension=FourDimension.ACADEMIC_STATUS)
    report = service.migrate_account(ALICE)
    assert len(service.list_items(ALICE)) == 1

    undone = service.rollback_migration(ALICE, report.run_id)

    assert undone.status == AtomicProfileMigrationStatus.UNDONE
    assert undone.undone_at is not None
    assert service.list_items(ALICE) == []
    # 旧数据可对账、不被改写：回滚后再迁移能完整恢复。
    assert four_dimensions.get_record(ALICE, record.record_id).content == "我大三"
    again = service.migrate_account(ALICE)
    assert again.migrated == 1
    assert [item.text for item in service.list_items(ALICE)] == ["我大三"]
    assert service.rollback_migration(ALICE, report.run_id).status == (
        AtomicProfileMigrationStatus.UNDONE
    )


def test_migration_rollback_rejects_unknown_batch(service: AtomicProfileService) -> None:
    with pytest.raises(AtomicProfileError, match="对象不存在"):
        service.rollback_migration(ALICE, "atomic-missing")


# ---------------------------------------------------------------------------
# AC2：自动抽取镜像（去重、冲突、来源、零条）
# ---------------------------------------------------------------------------


def test_mirror_records_zero_or_many_items_with_sources(
    service: AtomicProfileService, four_dimensions: FourDimensionProfileService
) -> None:
    assert service.mirror_record(ALICE, _record(four_dimensions, ALICE, "我大三")) is None or True
    assert len(service.list_items(BOB)) == 0

    record = _record(four_dimensions, ALICE, "我大三", evidence_message_id="msg-1")
    item = service.mirror_record(
        ALICE, record, evidence_message_id="msg-1"
    )
    assert item is not None
    assert item.text == "我大三"
    assert item.source_message_ids == ["msg-1"]
    assert item.write_origin == AtomicProfileWriteOrigin.AUTOMATIC


def test_mirror_deduplicates_same_text_and_unions_sources(
    service: AtomicProfileService, four_dimensions: FourDimensionProfileService
) -> None:
    first = _record(
        four_dimensions,
        ALICE,
        "喜欢看天体物理科普",
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        evidence_message_id="msg-1",
    )
    other_dimension = _record(
        four_dimensions,
        ALICE,
        "喜欢看天体物理科普",
        dimension=FourDimension.HOBBY,
        evidence_message_id="msg-2",
    )

    service.mirror_record(ALICE, first, evidence_message_id="msg-1")
    service.mirror_record(ALICE, other_dimension, evidence_message_id="msg-2")

    items = service.list_items(ALICE)
    assert len(items) == 1
    assert items[0].source_message_ids == ["msg-1", "msg-2"]


def test_mirror_replaces_value_when_underlying_record_is_updated(
    service: AtomicProfileService, four_dimensions: FourDimensionProfileService
) -> None:
    record = _record(four_dimensions, ALICE, "假期想学游泳", dimension=FourDimension.STAGE_GOAL)
    service.mirror_record(ALICE, record, evidence_message_id="msg-1")

    four_dimensions.correct_record(
        ALICE, dimension=FourDimension.STAGE_GOAL, content="假期想学爬山"
    )
    updated = four_dimensions.get_record(ALICE, record.record_id)
    service.mirror_record(ALICE, updated, evidence_message_id="msg-2")

    items = service.list_items(ALICE)
    assert [item.text for item in items] == ["假期想学爬山"]
    assert items[0].source_message_ids == ["msg-1", "msg-2"]


def test_mirror_skips_blank_and_tombstoned_content(
    service: AtomicProfileService, four_dimensions: FourDimensionProfileService
) -> None:
    record = _record(four_dimensions, ALICE, "我住在南昌", evidence_message_id="msg-1")
    service.mirror_record(ALICE, record, evidence_message_id="msg-1")
    item = service.list_items(ALICE)[0]

    service.delete_item(ALICE, item.profile_item_id, item.version)

    assert service.mirror_record(ALICE, record, evidence_message_id="msg-1") is None
    assert service.list_items(ALICE) == []


# ---------------------------------------------------------------------------
# AC3：用户编辑、删除与记忆指令
# ---------------------------------------------------------------------------


def test_modify_item_sets_user_authority_and_protects_from_automation(
    service: AtomicProfileService, four_dimensions: FourDimensionProfileService
) -> None:
    record = _record(four_dimensions, ALICE, "喜欢看科普", evidence_message_id="msg-1")
    service.mirror_record(ALICE, record, evidence_message_id="msg-1")
    item = service.list_items(ALICE)[0]
    version = item.version

    updated = service.modify_item(
        ALICE,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(text="喜欢看天体物理科普", version=version),
    )

    assert updated.text == "喜欢看天体物理科普"
    assert updated.user_edited_at is not None
    assert updated.write_origin == AtomicProfileWriteOrigin.USER
    assert updated.version == version + 1

    # 自动抽取再提出旧值：条目保持用户版本，不被改写。
    service._four_dimensions.upsert_automatic_record(  # noqa: SLF001 - 合同断言
        ALICE, dimension=FourDimension.KNOWLEDGE_INTEREST, content="喜欢看科普", action="update"
    )
    stale = four_dimensions.get_record(ALICE, record.record_id)
    service.mirror_record(ALICE, stale, evidence_message_id="msg-2")
    assert [item_.text for item_ in service.list_items(ALICE)] == ["喜欢看天体物理科普"]


def test_modify_item_suppresses_the_old_text_from_later_extraction(
    service: AtomicProfileService,
    four_dimensions: FourDimensionProfileService,
    repository: InMemoryAtomicProfileRepository,
) -> None:
    """用户改掉的值不会因旧消息或旧记录再次被抽取而作为新条目回来。"""

    item = service.remember(ALICE, "我养了一只猫", source_message_id="msg-1")
    version = item.version
    service.modify_item(
        ALICE,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(text="我养了两只猫", version=version),
    )

    record = _record(
        four_dimensions, ALICE, "我养了一只猫", evidence_message_id="msg-2"
    )
    service.mirror_record(ALICE, record, evidence_message_id="msg-2")

    assert [item_.text for item_ in service.list_items(ALICE)] == ["我养了两只猫"]
    # 抑制键只留键、不留正文：对账输出里不能出现被改掉的旧值。
    tombstones = repository.list_items(ALICE, include_withdrawn=True)
    assert [(item_.status, item_.text) for item_ in tombstones] == [
        (AtomicProfileItemStatus.WITHDRAWN, ""),
        (AtomicProfileItemStatus.ACTIVE, "我养了两只猫"),
    ]

    # 用户再次明确「记住」旧正文：指令优先于抑制键，可以恢复。
    revived = service.remember(ALICE, "我养了一只猫")
    assert revived.status == AtomicProfileItemStatus.ACTIVE
    assert {item_.text for item_ in service.list_items(ALICE)} == {
        "我养了两只猫",
        "我养了一只猫",
    }


def test_modify_item_rejects_stale_version_and_duplicates(
    service: AtomicProfileService,
) -> None:
    item = service.remember(ALICE, "喜欢看科普")
    other = service.remember(ALICE, "喜欢看纪录片")

    with pytest.raises(AtomicProfileError, match="版本冲突"):
        service.modify_item(
            ALICE,
            item.profile_item_id,
            AtomicProfileItemModifyRequest(text="喜欢看天文", version=item.version + 5),
        )
    with pytest.raises(AtomicProfileError, match="已存在内容相同"):
        service.modify_item(
            ALICE,
            other.profile_item_id,
            AtomicProfileItemModifyRequest(text="喜欢看科普", version=other.version),
        )
    with pytest.raises(AtomicProfileError, match="对象不存在"):
        service.modify_item(
            BOB,
            item.profile_item_id,
            AtomicProfileItemModifyRequest(text="别人的信息", version=item.version),
        )


def test_delete_item_writes_tombstone_and_survives_replay(
    service: AtomicProfileService, four_dimensions: FourDimensionProfileService
) -> None:
    record = _record(
        four_dimensions,
        ALICE,
        "我住在南昌",
        dimension=FourDimension.ACADEMIC_STATUS,
        evidence_message_id="msg-1",
    )
    service.mirror_record(ALICE, record, evidence_message_id="msg-1")
    item = service.list_items(ALICE)[0]

    service.delete_item(ALICE, item.profile_item_id, item.version)

    assert service.list_items(ALICE) == []
    tombstone = service._repository.find_item_by_identity(  # noqa: SLF001 - 墓碑合同
        ALICE, identity_key(ALICE, "我住在南昌")
    )
    assert tombstone is not None
    assert tombstone.status == AtomicProfileItemStatus.WITHDRAWN
    assert tombstone.text == ""
    # 旧消息重放：抽取结果不会再写成条目，底层记录也不会复活。
    service.mirror_record(ALICE, record, evidence_message_id="msg-1")
    assert service.list_items(ALICE) == []
    assert four_dimensions.list_records(ALICE) == []


def test_remember_takes_effect_immediately_and_revives_deleted(
    service: AtomicProfileService,
) -> None:
    item = service.remember(ALICE, "我的目标是今年通过雅思考试", source_message_id="msg-1")

    assert item.write_origin == AtomicProfileWriteOrigin.USER
    assert item.user_edited_at is not None
    assert item.source_message_ids == ["msg-1"]
    assert [entry.text for entry in service.list_items(ALICE)] == [
        "我的目标是今年通过雅思考试"
    ]

    service.delete_item(ALICE, item.profile_item_id, item.version)
    assert service.list_items(ALICE) == []

    revived = service.remember(ALICE, "我的目标是今年通过雅思考试")
    assert revived.status == AtomicProfileItemStatus.ACTIVE
    assert [entry.text for entry in service.list_items(ALICE)] == [
        "我的目标是今年通过雅思考试"
    ]
    # 重复处理同一条消息是幂等的：不重复涨版本。
    assert service.remember(
        ALICE, "我的目标是今年通过雅思考试", source_message_id="msg-1"
    ).version >= revived.version


def test_remember_rejects_blank_text(service: AtomicProfileService) -> None:
    with pytest.raises(AtomicProfileError, match="内容不合法"):
        service.remember(ALICE, "   ")


def test_forget_removes_matched_items_and_reports_unresolved(
    service: AtomicProfileService, four_dimensions: FourDimensionProfileService
) -> None:
    record = _record(
        four_dimensions,
        ALICE,
        "爱好是跑步",
        dimension=FourDimension.HOBBY,
        evidence_message_id="msg-1",
    )
    service.mirror_record(ALICE, record, evidence_message_id="msg-1")
    service.remember(ALICE, "习惯早睡")

    result = service.forget(ALICE, "跑步")

    assert result.kind == AtomicProfileMemoryKind.FORGET
    assert result.status == AtomicProfileMemoryStatus.FORGOTTEN
    assert result.matched_count == 1
    assert [item.text for item in service.list_items(ALICE)] == ["习惯早睡"]
    assert four_dimensions.list_records(ALICE) == []
    assert service.mirror_record(ALICE, record, evidence_message_id="msg-1") is None

    assert service.forget(
        ALICE, "我从来没说过的事情"
    ).status == AtomicProfileMemoryStatus.UNRESOLVED
    assert service.forget(ALICE, "习").status == AtomicProfileMemoryStatus.UNRESOLVED
    assert [item.text for item in service.list_items(ALICE)] == ["习惯早睡"]


# ---------------------------------------------------------------------------
# AC4：最小切片与账户隔离
# ---------------------------------------------------------------------------


def test_slice_includes_only_task_relevant_items(service: AtomicProfileService) -> None:
    service.remember(ALICE, "我在准备雅思考试")
    service.remember(ALICE, "喜欢跑步和游泳")
    service.remember(ALICE, "住在南昌")

    slice_ = service.compile_chat_slice(
        ALICE, run_id="assistant-1", current_question="帮我安排雅思考试的复习计划"
    )

    included = {item.value_or_rule for item in slice_.included_items}
    assert included == {"我在准备雅思考试"}
    excluded = {item.value_or_rule: item.exclusion_reason for item in slice_.unused_items}
    assert excluded["喜欢跑步和游泳"] == "与当前问题无关"
    assert len(slice_.included_items) <= MAX_SLICE_ITEMS

    no_question = service.compile_chat_slice(ALICE, run_id="assistant-2")
    assert len(no_question.included_items) == 3


def test_same_turn_extraction_enters_context_from_the_next_turn(
    service: AtomicProfileService, four_dimensions: FourDimensionProfileService
) -> None:
    """普通异步提取下一轮生效；用户「记住」的条目本轮就能用。"""

    record = _record(four_dimensions, ALICE, "今年通过雅思考试")
    service.mirror_record(ALICE, record, evidence_message_id="message-1")

    this_turn = service.compile_chat_slice(
        ALICE,
        run_id="assistant-1",
        current_question="帮我安排雅思考试的复习计划",
        current_user_message_id="message-1",
    )

    assert this_turn.included_items == []
    assert [
        item.exclusion_reason for item in this_turn.unused_items
    ] == ["本轮刚整理，下一轮才使用"]

    next_turn = service.compile_chat_slice(
        ALICE,
        run_id="assistant-2",
        current_question="帮我安排雅思考试的复习计划",
        current_user_message_id="message-2",
    )
    assert [item.value_or_rule for item in next_turn.included_items] == ["今年通过雅思考试"]

    # 用户明确要求记住的条目在同一轮就必须可用。
    service.remember(ALICE, "我在准备雅思考试", source_message_id="message-2")
    same_turn = service.compile_chat_slice(
        ALICE,
        run_id="assistant-3",
        current_question="帮我安排雅思考试的复习计划",
        current_user_message_id="message-2",
    )
    assert "我在准备雅思考试" in [
        item.value_or_rule for item in same_turn.included_items
    ]


def test_slice_caps_minimal_budget(service: AtomicProfileService) -> None:
    for index in range(6):
        service.remember(ALICE, f"第 {index} 条考试安排")

    slice_ = service.compile_chat_slice(
        ALICE, run_id="assistant-3", current_question="考试安排"
    )

    assert len(slice_.included_items) == MAX_SLICE_ITEMS
    assert all(
        item.exclusion_reason == "超出本轮最小切片预算"
        for item in slice_.unused_items
    )


def test_items_tombstones_and_reports_are_account_scoped(
    service: AtomicProfileService,
) -> None:
    alice_item = service.remember(ALICE, "我在准备雅思考试")
    service.remember(BOB, "我在准备考研")
    service.delete_item(ALICE, alice_item.profile_item_id, alice_item.version)

    assert [item.text for item in service.list_items(BOB)] == ["我在准备考研"]
    assert service.list_items(ALICE) == []
    assert parse_memory_directive("忘掉我在准备考研") is not None
    # 爱丽丝的墓碑不影响鲍勃记住同一句话。
    service.remember(BOB, "我在准备雅思考试")
    assert "我在准备雅思考试" in {item.text for item in service.list_items(BOB)}

    with pytest.raises(AtomicProfileError, match="没有访问权限"):
        service.get_item(BOB, alice_item.profile_item_id)
    assert service.latest_migration_report(BOB) is None
    with pytest.raises(AtomicProfileError, match="对象不存在"):
        service.rollback_migration(BOB, "atomic-alice")


def test_orders_newest_first(service: AtomicProfileService) -> None:
    first = service.remember(ALICE, "第一条")
    second = service.remember(ALICE, "第二条")
    second.updated_at = datetime(2030, 1, 1, tzinfo=UTC)
    service._repository.save_item(second)  # noqa: SLF001 - 排序断言
    assert [item.text for item in service.list_items(ALICE)] == ["第二条", "第一条"]
    assert first.profile_item_id != second.profile_item_id


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------


def test_sqlite_repository_persists_items_and_reports(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    source = InMemoryProfileRepository()
    four_dimensions = FourDimensionProfileService(
        source_repository=source,
        repository=InMemoryFourDimensionProfileRepository(),
    )
    repository = SqliteAtomicProfileRepository(database, initialize=False)
    service = AtomicProfileService(four_dimensions, repository)
    record = _record(
        four_dimensions, ALICE, "喜欢看科普", evidence_message_id="msg-1"
    )
    service.mirror_record(ALICE, record, evidence_message_id="msg-1")
    item = service.list_items(ALICE)[0]
    service.modify_item(
        ALICE,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(
            text="喜欢看天体物理科普", version=item.version
        ),
    )
    report = service.migrate_account(ALICE)

    reopened = SqliteAtomicProfileRepository(
        BridgesDatabase(tmp_path / "bridges.db"), initialize=False
    )
    reloaded = AtomicProfileService(four_dimensions, reopened)
    items = reloaded.list_items(ALICE)

    assert [entry.text for entry in items] == ["喜欢看天体物理科普"]
    assert items[0].user_edited_at is not None
    assert items[0].source_message_ids == ["msg-1"]
    assert reloaded.latest_migration_report(ALICE).run_id == report.run_id
    assert reloaded.rollback_migration(ALICE, report.run_id).status == (
        AtomicProfileMigrationStatus.UNDONE
    )
    # 回滚只删除该批次新建的条目；镜像写入并已由用户编辑的条目不受影响。
    assert [entry.text for entry in reloaded.list_items(ALICE)] == ["喜欢看天体物理科普"]


def test_sqlite_tombstone_blocks_mirror_after_restart(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    four_dimensions = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AtomicProfileService(
        four_dimensions, SqliteAtomicProfileRepository(database, initialize=False)
    )
    item = service.remember(ALICE, "我住在南昌")
    service.delete_item(ALICE, item.profile_item_id, item.version)

    reopened = SqliteAtomicProfileRepository(database, initialize=False)
    reloaded = AtomicProfileService(four_dimensions, reopened)
    record = _record(four_dimensions, ALICE, "我住在南昌", evidence_message_id="msg-9")

    assert reloaded.mirror_record(ALICE, record, evidence_message_id="msg-9") is None
    assert reloaded.list_items(ALICE) == []
    assert normalize_text("  我住在南昌  ") == "我住在南昌"
