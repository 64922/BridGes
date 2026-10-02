"""Issue 06：来源撤回失败的跨重启恢复与两种 adapter 的同一行为合同。

04、05 已经证明保护点之内的整批回滚（四维记录、原子镜像与提取记账同成同败）
以及「墓碑先落地、来源撤回在后」的顺序。本票补保护点之后的失败：条目已删除
但底层来源仍活动的残留状态，如何只凭持久记录被重新发现并幂等补齐；内存与
SQLite 两种 adapter 在故障、恢复、重放与历史迁移上给出同一组可见业务结果。

故障注入落在真实提交点（四维来源撤回、原子镜像），不 mock 调用顺序；「重启」
对 SQLite 是关库重开（连接与仓库都是新的，只剩文件里的记录），对内存是复用
同一组仓库、丢掉整个服务层，两者都不保留任何失败时的进程内对象。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from bridges.contracts.atomic_profile import (
    AtomicProfileItem,
    AtomicProfileItemModifyRequest,
    AtomicProfileItemStatus,
    AtomicProfileMemoryStatus,
    AtomicProfileMigrationStatus,
)
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutcome,
    ProfileExtractionOutput,
    ProfileExtractionRetryTask,
    ProfileExtractionStatus,
    ProfilePreprocessResult,
)
from bridges.contracts.profiles import (
    FourDimension,
    FourDimensionConfidence,
    FourDimensionProfileRecord,
    FourDimensionRecordStatus,
)
from bridges.profiles.atomic import (
    AtomicProfileRepository,
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    SqliteAtomicProfileRepository,
    identity_key,
)
from bridges.profiles.automatic import (
    PROFILE_EXTRACTION_QUEUE,
    PROFILE_REPLAY_SOURCE_HASH_PREFIX,
    AutomaticProfileRepository,
    AutomaticProfileService,
    InMemoryAutomaticProfileRepository,
    SqliteAutomaticProfileRepository,
)
from bridges.profiles.commit import SourceWithdrawalStatus
from bridges.profiles.four_dimensions import (
    FourDimensionProfileRepository,
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.storage import BridgesDatabase

ACCOUNT = "account-alice"
OTHER_ACCOUNT = "account-bob"
# 本地规则抽取器能从这句话稳定抽出两条长期信息（与既有抽取测试同源）。
TWO_FACT_MESSAGE = "我的阶段目标和学业情况很明确"
GOAL_TEXT = "完成目标 A"
STATUS_TEXT = "大学生"
# 忘掉目标同时命中两条条目（正文互含，属既有确定性匹配规则）。
MULTI_TARGET = "完成目标 A 和大学生"
USER_EDIT_TEXT = "在读大四"
# 只有四维来源、还没有原子条目的历史记录（迁移要处理的对象）。
LEGACY_TEXT = "喜欢看天体物理科普"


class _TwoFactExtractor:
    """一次稳定抽出一条阶段目标与一条学业情况（与既有抽取测试同源）。"""

    version = "two-fact-v1"

    def extract(self, *, message_id: str, **_ignored: object) -> ProfileExtractionOutput:
        return ProfileExtractionOutput.model_validate(
            {
                "items": [
                    {
                        "dimension": FourDimension.STAGE_GOAL.value,
                        "normalized_value": GOAL_TEXT,
                        "evidence_ref": message_id,
                        "reliability": 0.9,
                        "action": "create",
                    },
                    {
                        "dimension": FourDimension.ACADEMIC_STATUS.value,
                        "normalized_value": STATUS_TEXT,
                        "evidence_ref": message_id,
                        "reliability": 0.9,
                        "action": "create",
                    },
                ]
            }
        )


class _WithdrawFailsOnce(FourDimensionProfileService):
    """``fail_at`` 指定第几次来源撤回写入失败（0 表示不注入故障）。"""

    def __init__(self, repository: FourDimensionProfileRepository) -> None:
        super().__init__(
            source_repository=None,  # type: ignore[arg-type]
            repository=repository,
        )
        self.calls = 0
        self.fail_at = 0

    def withdraw_record(
        self, account_id: str, record_id: str, version: int | None = None
    ) -> FourDimensionProfileRecord:
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("来源撤回写入失败")
        return super().withdraw_record(account_id, record_id, version)


class _MirrorFailsOnce(AtomicProfileService):
    """``fail_at`` 指定第几次镜像写入失败（0 表示不注入故障）。"""

    def __init__(
        self,
        four_dimensions: FourDimensionProfileService,
        repository: AtomicProfileRepository,
    ) -> None:
        super().__init__(four_dimensions, repository)
        self.calls = 0
        self.fail_at = 0

    def mirror_record(
        self,
        account_id: str,
        record: FourDimensionProfileRecord,
        *,
        evidence_message_id: str | None = None,
        fact_text: str | None = None,
        source_at: datetime | None = None,
    ) -> AtomicProfileItem | None:
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("镜像写入失败")
        return super().mirror_record(
            account_id,
            record,
            evidence_message_id=evidence_message_id,
            fact_text=fact_text,
            source_at=source_at,
        )


class _Stack:
    """一组存储：SQLite 三仓库共用同一个库文件，内存组合各持一份状态。"""

    def __init__(
        self,
        *,
        flavor: str,
        database: BridgesDatabase | None,
        dimensions: FourDimensionProfileRepository,
        atomic: AtomicProfileRepository,
        state: AutomaticProfileRepository,
    ) -> None:
        self.flavor = flavor
        self.database = database
        self.dimensions = dimensions
        self.atomic = atomic
        self.state = state

    def close(self) -> None:
        if self.database is not None:
            self.database.close()


def _open_stack(flavor: str, db_path: Path) -> _Stack:
    if flavor == "sqlite":
        database = BridgesDatabase(db_path)
        database.initialize()
        return _Stack(
            flavor=flavor,
            database=database,
            dimensions=SqliteFourDimensionProfileRepository(database),
            atomic=SqliteAtomicProfileRepository(database),
            state=SqliteAutomaticProfileRepository(database),
        )
    return _Stack(
        flavor=flavor,
        database=None,
        dimensions=InMemoryFourDimensionProfileRepository(),
        atomic=InMemoryAtomicProfileRepository(),
        state=InMemoryAutomaticProfileRepository(),
    )


class _Harness:
    """被测组合：自动抽取 + 四维记录 + 用户可见原子列表。"""

    def __init__(self, stack: _Stack, db_path: Path) -> None:
        self._db_path = db_path
        self._stack = stack
        self._install(stack)

    def _install(self, stack: _Stack) -> None:
        self.stack = stack
        self.dimensions = _WithdrawFailsOnce(stack.dimensions)
        self.atomic = _MirrorFailsOnce(self.dimensions, stack.atomic)
        self.service = AutomaticProfileService(
            four_dimension_service=self.dimensions,
            repository=stack.state,
            extractor=_TwoFactExtractor(),
            atomic_profile_service=self.atomic,
        )

    def restart(self) -> None:
        """重建模块：进程内对象全部丢弃，只留存储里的残留状态。

        SQLite 关库重开（新的连接与三个仓库）；内存仓库没有跨进程状态，
        因此复用同一组仓库、重建整个服务层——失败时持有的条目对象与提交
        实例都已不在。
        """

        if self.stack.database is not None:
            self.stack.close()
            self._stack = _open_stack("sqlite", self._db_path)
        self._install(self._stack)

    def close(self) -> None:
        self.stack.close()

    def extract(
        self,
        message_id: str,
        content: str = TWO_FACT_MESSAGE,
        *,
        account: str = ACCOUNT,
    ) -> ProfilePreprocessResult:
        return self.service.preprocess_message(
            account,
            conversation_id="conversation-1",
            message_id=message_id,
            content=content,
            run_id=f"run-{message_id}",
            mode="study",
        )

    def records(
        self, account: str = ACCOUNT, *, include_withdrawn: bool = False
    ) -> list[FourDimensionProfileRecord]:
        return self.dimensions.list_records(account, include_withdrawn=include_withdrawn)

    def record_of(
        self, dimension: FourDimension, *, account: str = ACCOUNT
    ) -> FourDimensionProfileRecord:
        """按维度取来源记录（撤回后默认列表不可见，这里含墓碑）。"""

        return next(
            record
            for record in self.records(account, include_withdrawn=True)
            if record.dimension is dimension
        )

    def items(self, account: str = ACCOUNT) -> list[AtomicProfileItem]:
        return self.atomic.list_items(account)

    def item_of(self, text: str) -> AtomicProfileItem:
        return next(item for item in self.items() if item.text == text)

    def item_by_id(self, item_id: str, *, account: str = ACCOUNT) -> AtomicProfileItem:
        """按标识读条目（含墓碑）：列表只返回活动条目。"""

        return self.atomic.get_item(account, item_id)

    def item_by_source(self, record_id: str) -> AtomicProfileItem:
        return next(
            item
            for item in self.atomic.tombstoned_items_with_sources(ACCOUNT)
            if item.source_record_id == record_id
        )

    def slice_texts(self) -> set[str]:
        slice_ = self.atomic.compile_chat_slice(
            ACCOUNT, run_id="verify-slice", current_question=None
        )
        return {item.value_or_rule for item in slice_.included_items}

    def recover(self) -> list[SourceWithdrawalStatus]:
        return [
            outcome.status
            for outcome in self.atomic.recover_source_withdrawals(ACCOUNT)
        ]

    def retry(self) -> str:
        return self.service.run_retry_tick()


@pytest.fixture(params=["memory", "sqlite"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Harness]:
    """同一组合同场景覆盖内存与 SQLite 两种 adapter。"""

    built = _Harness(_open_stack(request.param, tmp_path / "bridges.db"), tmp_path / "bridges.db")
    try:
        yield built
    finally:
        built.close()


def _delete_with_failed_withdrawal(harness: _Harness, text: str) -> AtomicProfileItem:
    """删除一条条目，但让来源撤回写入失败：留下受保护的残留状态。"""

    item = harness.item_of(text)
    harness.dimensions.fail_at = 1
    with pytest.raises(RuntimeError, match="来源撤回写入失败"):
        harness.atomic.delete_item(ACCOUNT, item.profile_item_id, item.version)
    harness.dimensions.fail_at = 0
    return item


def _enqueue_replay_task(harness: _Harness, message_id: str) -> None:
    """以真实重放形状为旧消息排队一条重放任务。

    重放任务的来源哈希与 ``replay._replay_source_hash`` 同形：
    ``{原哈希}{重放标记}{原运行标识}``；正文由运行记录的来源快照提供，
    因此这里不需要消息存储。
    """

    run = next(
        item
        for item in harness.stack.state.list_runs(ACCOUNT)
        if item.message_id == message_id
    )
    replay_hash = (
        f"{run.source_hash}{PROFILE_REPLAY_SOURCE_HASH_PREFIX}{run.extraction_id}"
    )
    harness.stack.state.save_run(
        run.model_copy(
            update={
                "extraction_id": f"{run.extraction_id}-replay",
                "source_hash": replay_hash,
                "status": ProfileExtractionStatus.PENDING,
                "outcome": ProfileExtractionOutcome.PENDING_RETRY,
                "attempts": 0,
            }
        )
    )
    task = ProfileExtractionRetryTask(
        task_id=f"task-{replay_hash}",
        account_id=run.account_id,
        message_id=run.message_id,
        extractor_version=run.extractor_version,
        source_hash=replay_hash,
        status=ProfileExtractionStatus.PENDING,
        attempts=0,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )
    harness.stack.state.save_task(task)
    queue = harness.stack.state.durable_queue()
    if queue is not None:
        queue.enqueue(
            PROFILE_EXTRACTION_QUEUE,
            task.task_id,
            payload={
                "account_id": task.account_id,
                "message_id": task.message_id,
                "extractor_version": task.extractor_version,
                "source_hash": replay_hash,
            },
        )


# -- 保护点之后的残留状态：只凭持久记录恢复 --------------------------------


def test_restart_recovery_completes_source_withdrawal_after_delete(
    harness: _Harness,
) -> None:
    """删除越过墓碑保护点后撤回失败：重启后恢复补齐来源，条目不复活。"""

    harness.extract("message-1")
    goal = _delete_with_failed_withdrawal(harness, GOAL_TEXT)

    # 保护点之内没事：墓碑已提交，条目不可召回，切片里也没有它。
    assert harness.item_by_id(goal.profile_item_id).status is (
        AtomicProfileItemStatus.WITHDRAWN
    )
    assert {item.text for item in harness.items()} == {STATUS_TEXT}
    assert harness.slice_texts() == {STATUS_TEXT}
    # 残留状态 = 底层来源仍是活动的，且它只能从持久记录看出来。
    pending = harness.record_of(FourDimension.STAGE_GOAL)
    assert pending.status is FourDimensionRecordStatus.ACTIVE
    version_before = pending.version
    scanned = harness.atomic.tombstoned_items_with_sources(ACCOUNT)
    assert [item.profile_item_id for item in scanned] == [goal.profile_item_id]

    harness.restart()

    assert harness.recover() == [SourceWithdrawalStatus.WITHDRAWN]
    repaired = harness.record_of(FourDimension.STAGE_GOAL)
    assert repaired.status is FourDimensionRecordStatus.WITHDRAWN
    # 恢复只补来源处理：正文不被改写，条目保持删除且不可召回。
    assert repaired.content == GOAL_TEXT
    assert repaired.version == version_before + 1
    assert harness.item_by_id(goal.profile_item_id).status is (
        AtomicProfileItemStatus.WITHDRAWN
    )
    assert {item.text for item in harness.items()} == {STATUS_TEXT}
    assert harness.slice_texts() == {STATUS_TEXT}
    # 同批次另一条事实不受影响：来源与条目都保持活动。
    assert harness.record_of(FourDimension.ACADEMIC_STATUS).status is (
        FourDimensionRecordStatus.ACTIVE
    )

    # 已完成项不重复变更：再次恢复收敛为已撤回，来源版本不再前进。
    assert harness.recover() == [SourceWithdrawalStatus.ALREADY_WITHDRAWN]
    assert harness.record_of(FourDimension.STAGE_GOAL).version == version_before + 1


def test_multi_forget_partial_failure_is_recoverable_and_account_scoped(
    harness: _Harness,
) -> None:
    """多条忘掉的部分失败可重复恢复，已完成项不重复变更，不越账户。"""

    harness.extract("message-1")
    harness.extract("message-1", account=OTHER_ACCOUNT)
    other_items_before = {
        item.profile_item_id: item.model_dump()
        for item in harness.items(OTHER_ACCOUNT)
    }
    other_records_before = {
        record.record_id: record.model_dump()
        for record in harness.records(OTHER_ACCOUNT, include_withdrawn=True)
    }

    # 第一条撤回失败、第二条成功：用户在页面上看到的是「两条都删了」。
    harness.dimensions.fail_at = 1
    result = harness.atomic.forget(ACCOUNT, MULTI_TARGET)
    harness.dimensions.fail_at = 0

    assert result.status is AtomicProfileMemoryStatus.FORGOTTEN
    assert result.matched_count == 2
    assert harness.items() == []
    assert harness.slice_texts() == set()
    statuses = {
        record.dimension: record.status
        for record in harness.records(include_withdrawn=True)
    }
    assert sorted(status.value for status in statuses.values()) == ["active", "withdrawn"]
    pending_dimension = next(
        dimension
        for dimension, status in statuses.items()
        if status is FourDimensionRecordStatus.ACTIVE
    )
    done_dimension = next(
        dimension
        for dimension, status in statuses.items()
        if status is FourDimensionRecordStatus.WITHDRAWN
    )
    done_version = harness.record_of(done_dimension).version

    harness.restart()

    # 未完成项可识别：恢复只补齐那一条，已完成项收敛为已撤回。
    outcomes = harness.recover()
    assert outcomes.count(SourceWithdrawalStatus.WITHDRAWN) == 1
    assert outcomes.count(SourceWithdrawalStatus.ALREADY_WITHDRAWN) == 1
    assert harness.record_of(pending_dimension).status is (
        FourDimensionRecordStatus.WITHDRAWN
    )
    assert harness.record_of(done_dimension).version == done_version
    # 两条条目都保持删除；重复恢复不再写业务结果。
    assert harness.items() == []
    repaired_versions = {
        record.record_id: record.version
        for record in harness.records(include_withdrawn=True)
    }
    assert harness.recover() == [
        SourceWithdrawalStatus.ALREADY_WITHDRAWN,
        SourceWithdrawalStatus.ALREADY_WITHDRAWN,
    ]
    assert {
        record.record_id: record.version
        for record in harness.records(include_withdrawn=True)
    } == repaired_versions

    # 其他账户的条目与来源一条都没被碰过。
    assert {
        item.profile_item_id: item.model_dump()
        for item in harness.items(OTHER_ACCOUNT)
    } == other_items_before
    assert {
        record.record_id: record.model_dump()
        for record in harness.records(OTHER_ACCOUNT, include_withdrawn=True)
    } == other_records_before


def test_recovery_keeps_user_edits_and_never_revives_deleted_facts(
    harness: _Harness,
) -> None:
    """故障期间的用户编辑与重放：编辑不被旧结果覆盖，删除的事实不复活。"""

    harness.extract("message-1")
    goal = _delete_with_failed_withdrawal(harness, GOAL_TEXT)

    # 故障期间：用户编辑另一条条目，随后旧消息被重放。
    status_item = harness.item_of(STATUS_TEXT)
    harness.atomic.modify_item(
        ACCOUNT,
        status_item.profile_item_id,
        AtomicProfileItemModifyRequest(text=USER_EDIT_TEXT, version=status_item.version),
    )
    replay = harness.extract("message-replay-old")

    assert replay.run.status is ProfileExtractionStatus.SUCCEEDED
    assert {item.text for item in harness.items()} == {USER_EDIT_TEXT}
    assert harness.slice_texts() == {USER_EDIT_TEXT}

    harness.restart()
    assert harness.recover() == [SourceWithdrawalStatus.WITHDRAWN]

    # 恢复只补被删事实的来源：用户正文仍是当前版本，被删事实仍未复活。
    assert {item.text for item in harness.items()} == {USER_EDIT_TEXT}
    assert harness.slice_texts() == {USER_EDIT_TEXT}
    assert harness.record_of(FourDimension.STAGE_GOAL).status is (
        FourDimensionRecordStatus.WITHDRAWN
    )
    # 恢复之后旧消息按真实重放路径再处理一次：被删事实不因重放复活，
    # 用户正文也保持当前版本。
    _enqueue_replay_task(harness, "message-1")
    harness.retry()
    assert {item.text for item in harness.items()} == {USER_EDIT_TEXT}
    assert harness.slice_texts() == {USER_EDIT_TEXT}
    assert harness.record_of(FourDimension.STAGE_GOAL).status is (
        FourDimensionRecordStatus.WITHDRAWN
    )
    assert harness.item_by_id(goal.profile_item_id).status is (
        AtomicProfileItemStatus.WITHDRAWN
    )


# -- 保护点之内的镜像中断：重试不重复、不半写 --------------------------------


def test_interrupted_mirror_retry_does_not_duplicate_or_half_write(
    harness: _Harness,
) -> None:
    """同一批次自动镜像中断后重试：整批回滚、重试不产生重复活动条目。"""

    # 第二条事实的镜像写入失败：整批提交必须一起回滚。
    harness.atomic.fail_at = 2
    failed = harness.extract("message-1")

    # 没有「已成功但只写一半」：运行停在可重试状态，业务结果一条都没留下。
    assert failed.run.status is ProfileExtractionStatus.PENDING
    assert failed.run.outcome is ProfileExtractionOutcome.PENDING_RETRY
    assert failed.run.committed_record_ids == []
    assert harness.records(include_withdrawn=True) == []
    assert harness.items() == []
    assert harness.slice_texts() == set()

    # 重试走生产重试路径：同一运行边界内一次补齐两条事实。
    harness.atomic.fail_at = 0
    assert harness.retry() == (
        f"profile-extraction: {ProfileExtractionStatus.SUCCEEDED.value}。"
    )
    runs = harness.stack.state.list_runs(ACCOUNT)
    assert len(runs) == 1
    assert runs[0].status is ProfileExtractionStatus.SUCCEEDED
    assert len(runs[0].committed_record_ids) == 2
    assert {item.text for item in harness.items()} == {GOAL_TEXT, STATUS_TEXT}
    assert {record.content for record in harness.records()} == {
        GOAL_TEXT,
        STATUS_TEXT,
    }
    assert harness.slice_texts() == {GOAL_TEXT, STATUS_TEXT}

    # 去重：再次重试没有待处理任务，条目标识与版本都不变。
    before = {
        item.text: (item.profile_item_id, item.version) for item in harness.items()
    }
    assert harness.retry() == "profile-extraction: 无待处理任务。"
    assert {
        item.text: (item.profile_item_id, item.version) for item in harness.items()
    } == before


# -- 保护点之内：编辑的换键与抑制键同成同败 ----------------------------------


def test_inline_edit_fault_window_keeps_text_key_and_unsuppressed_old_text(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """编辑在写抑制键时失败：正文、去重键与版本一起保持原状。"""

    harness.extract("message-1")
    goal = harness.item_of(GOAL_TEXT)
    # 失败前的可见状态按值快照：内存仓库按引用返回条目，失败回滚后调用方
    # 手里的旧引用仍带着未提交的中间值，因此这里比的是存储里的当前状态。
    before = (goal.text, goal.identity_key, goal.version)
    repository = harness.stack.atomic
    original = repository.save_item
    calls = {"count": 0}

    def flaky_save(item: AtomicProfileItem) -> AtomicProfileItem:
        calls["count"] += 1
        if calls["count"] == 2:  # 1 = 新正文，2 = 旧正文抑制键
            raise RuntimeError("条目保存失败")
        return original(item)

    monkeypatch.setattr(repository, "save_item", flaky_save)

    with pytest.raises(RuntimeError, match="条目保存失败"):
        harness.atomic.modify_item(
            ACCOUNT,
            goal.profile_item_id,
            AtomicProfileItemModifyRequest(text=USER_EDIT_TEXT, version=goal.version),
        )

    # 边界之内失败即整批回滚：用户可见正文、身份键与乐观锁版本都没动。
    item = harness.atomic.get_item(ACCOUNT, goal.profile_item_id)
    assert (item.text, item.identity_key, item.version) == before
    assert item.user_edited_at is None
    # 没有留下半次编辑：旧正文没有被写成抑制键，同键仍是这条活动条目。
    same_key = repository.find_item_by_identity(ACCOUNT, identity_key(ACCOUNT, GOAL_TEXT))
    assert same_key is not None
    assert (same_key.profile_item_id, same_key.status) == (
        goal.profile_item_id,
        AtomicProfileItemStatus.ACTIVE,
    )
    # 后续自动写入仍按正常规则合并到同一事实，不因这次失败分叉。
    harness.extract("message-2")
    assert {entry.text for entry in harness.items()} == {GOAL_TEXT, STATUS_TEXT}
    assert harness.item_of(GOAL_TEXT).source_message_ids == ["message-1", "message-2"]


# -- 恢复不改变历史能力：迁移重复运行、回滚与旧来源不被改写 ------------------


def test_history_migration_contracts_hold_after_recovery(harness: _Harness) -> None:
    """恢复之后：迁移可重复运行、可回滚，旧四维审计来源不被删除或改写。"""

    harness.extract("message-1")
    _delete_with_failed_withdrawal(harness, GOAL_TEXT)
    harness.restart()
    assert harness.recover() == [SourceWithdrawalStatus.WITHDRAWN]
    # 旧数据里还有一条没有原子条目的四维记录——迁移要处理的历史对象。
    harness.dimensions.upsert_automatic_record(
        ACCOUNT,
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content=LEGACY_TEXT,
        action="create",
        confidence=FourDimensionConfidence.MEDIUM,
        migration_version="profile-auto-v2",
    )
    sources_before = {
        record.record_id: record.model_dump()
        for record in harness.records(include_withdrawn=True)
    }

    first = harness.atomic.migrate_account(ACCOUNT)
    second = harness.atomic.migrate_account(ACCOUNT)

    # 已撤回的来源只写墓碑，不因迁移重新激活；镜像过的来源只计重复；
    # 只有那条没有条目的历史来源真正迁入。
    assert (first.migrated, first.duplicated, first.tombstoned) == (1, 1, 1)
    assert first.created_item_ids == [
        item.profile_item_id
        for item in harness.items()
        if item.text == LEGACY_TEXT
    ]
    assert second.migrated == 0
    assert second.reconciliation_digest == first.reconciliation_digest
    assert {item.text for item in harness.items()} == {STATUS_TEXT, LEGACY_TEXT}
    assert GOAL_TEXT not in harness.slice_texts()
    # 幂等：重复迁移没有新增条目。
    assert len(harness.items()) == 2
    # 旧四维审计来源逐字段未被改写。
    assert {
        record.record_id: record.model_dump()
        for record in harness.records(include_withdrawn=True)
    } == sources_before

    undone = harness.atomic.rollback_migration(ACCOUNT, first.run_id)

    # 回滚只删除本批次新建的条目：迁移产物消失，用户删除与镜像产物保持。
    assert undone.status is AtomicProfileMigrationStatus.UNDONE
    assert {item.text for item in harness.items()} == {STATUS_TEXT}
    assert GOAL_TEXT not in harness.slice_texts()
    assert {
        record.record_id: record.model_dump()
        for record in harness.records(include_withdrawn=True)
    } == sources_before
