"""Issue 05：用户权威操作与自动镜像遵守同一套提交规则。

覆盖四类用户入口的共同合同：聊天更正（首次处理与后台重试一致地成对
写入来源与镜像）、显式记住/忘掉（本轮立即生效、重复指令不产生重复
条目）、行内编辑与删除（版本冲突、重复正文、旧正文抑制、账户隔离），
以及删除/忘掉的关键保护点——墓碑先提交，来源撤回失败不回滚墓碑，
同一撤回操作重复执行幂等。核心场景在内存与 SQLite 两种 adapter 上各
跑一遍；故障注入在真实提交点（四维撤回、原子镜像），不 mock 调用顺序。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from bridges.contracts.atomic_profile import (
    AtomicProfileItem,
    AtomicProfileItemModifyRequest,
    AtomicProfileItemStatus,
    AtomicProfileMemoryStatus,
)
from bridges.contracts.profile_extraction import (
    ProfileCorrectionStatus,
    ProfileExtractionOutcome,
    ProfileExtractionOutput,
    ProfileExtractionRetryTask,
    ProfileExtractionStatus,
    ProfilePreprocessResult,
)
from bridges.contracts.profiles import (
    FourDimension,
    FourDimensionProfileRecord,
)
from bridges.observability.service import ObservabilityService
from bridges.profiles.atomic import (
    AtomicProfileError,
    AtomicProfileRepository,
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    SqliteAtomicProfileRepository,
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
# 与既有抽取测试同源的纠正指令：命中学业阶段维度。
CORRECTION_MESSAGE = "我的阶段目标改成通过雅思考试"
CORRECTED_TEXT = "通过雅思考试"
REMEMBER_TEXT = "我在准备今年的雅思考试"


class _TwoFactExtractor:
    """一次稳定抽出一条阶段目标与一条学业情况（与既有抽取测试同源）。"""

    version = "two-fact-v1"

    def extract(self, *, message_id: str, **_: object) -> ProfileExtractionOutput:
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
    """``fail_at`` 指定第几次来源撤回失败（0 表示不注入故障）。"""

    def __init__(
        self,
        repository: FourDimensionProfileRepository,
    ) -> None:
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
    ) -> AtomicProfileItem | None:
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("镜像写入失败")
        return super().mirror_record(
            account_id, record, evidence_message_id=evidence_message_id
        )


class _Harness:
    """被测组合：自动抽取 + 四维记录 + 用户可见原子列表。"""

    def __init__(
        self,
        *,
        service: AutomaticProfileService,
        state: AutomaticProfileRepository,
        dimensions: _WithdrawFailsOnce,
        atomic: _MirrorFailsOnce,
        database: BridgesDatabase | None,
    ) -> None:
        self.service = service
        self.state = state
        self.dimensions = dimensions
        self.atomic = atomic
        self._database = database

    def close(self) -> None:
        if self._database is not None:
            self._database.close()

    def extract(
        self,
        message_id: str,
        content: str,
        *,
        account: str = ACCOUNT,
    ) -> None:
        self.service.preprocess_message(
            account,
            conversation_id="conversation-1",
            message_id=message_id,
            content=content,
            run_id=f"run-{message_id}",
            mode="study",
        )

    def correct(
        self, message_id: str, content: str = CORRECTION_MESSAGE
    ) -> ProfilePreprocessResult:
        return self.service.preprocess_message(
            ACCOUNT,
            conversation_id="conversation-1",
            message_id=message_id,
            content=content,
            run_id=f"run-{message_id}",
            mode="study",
        )

    def records(self, account: str = ACCOUNT) -> list[FourDimensionProfileRecord]:
        return self.dimensions.list_records(account)

    def all_records(self, account: str = ACCOUNT) -> list[FourDimensionProfileRecord]:
        """含已撤回记录：撤回后默认列表不可见，对账需要看到它们。"""

        return self.dimensions.list_records(account, include_withdrawn=True)

    def items(self, account: str = ACCOUNT) -> list[AtomicProfileItem]:
        return self.atomic.list_items(account)

    def slice_texts(self, *, current_message_id: str | None = None) -> set[str]:
        slice_ = self.atomic.compile_chat_slice(
            ACCOUNT,
            run_id="verify-slice",
            current_question=None,
            current_user_message_id=current_message_id,
        )
        return {item.value_or_rule for item in slice_.included_items}

    def retry(self) -> str:
        return self.service.run_retry_tick()


def _build(flavor: str, tmp_path: Path) -> _Harness:
    """按 adapter 组合装配生产同形的接线：``memory``、``sqlite``。"""

    database = None if flavor == "memory" else BridgesDatabase(tmp_path / "bridges.db")
    if database is not None:
        database.initialize()
    if flavor == "sqlite":
        assert database is not None
        dimension_repository: FourDimensionProfileRepository = (
            SqliteFourDimensionProfileRepository(database)
        )
        atomic_repository: AtomicProfileRepository = SqliteAtomicProfileRepository(
            database
        )
        state: AutomaticProfileRepository = SqliteAutomaticProfileRepository(database)
    else:
        dimension_repository = InMemoryFourDimensionProfileRepository()
        atomic_repository = InMemoryAtomicProfileRepository()
        state = InMemoryAutomaticProfileRepository()

    dimensions = _WithdrawFailsOnce(dimension_repository)
    atomic = _MirrorFailsOnce(dimensions, atomic_repository)
    return _Harness(
        service=AutomaticProfileService(
            four_dimension_service=dimensions,
            repository=state,
            extractor=_TwoFactExtractor(),
            observability_service=ObservabilityService(),
            atomic_profile_service=atomic,
        ),
        state=state,
        dimensions=dimensions,
        atomic=atomic,
        database=database,
    )


@pytest.fixture(params=["memory", "sqlite"])
def harness(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[_Harness]:
    built = _build(request.param, tmp_path)
    try:
        yield built
    finally:
        built.close()


def _goal_item(harness: _Harness) -> AtomicProfileItem:
    return next(
        item
        for item in harness.atomic.list_items(ACCOUNT)
        if item.text == GOAL_TEXT
    )


def _stage_goal_item(harness: _Harness) -> AtomicProfileItem:
    """按来源记录定位阶段目标条目：更正或编辑改文后仍可寻址。"""

    record = next(
        record
        for record in harness.records()
        if record.dimension is FourDimension.STAGE_GOAL
    )
    return next(
        item
        for item in harness.atomic.list_items(ACCOUNT)
        if item.source_record_id == record.record_id
    )


def _item_by_id(harness: _Harness, item_id: str) -> AtomicProfileItem:
    """按标识取条目（含墓碑）：list 只返回活动条目，这里直接读存储。"""

    return harness.atomic.get_item(ACCOUNT, item_id)


# -- 聊天更正：首次处理与重试共用同一成对写入 ------------------------------


def test_correction_retry_mirrors_the_atomic_item(harness: _Harness) -> None:
    """首次更正在镜像处失败时，重试补齐记录与条目，不留半更新状态。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    # extract 阶段已经消耗了镜像调用；故障注入指向下一次（更正的）镜像。
    harness.atomic.fail_at = harness.atomic.calls + 1

    first = harness.correct("message-correction-1")
    assert first.correction is not None
    assert first.correction.status == ProfileCorrectionStatus.FAILED
    # 失败整批回滚：四维记录与原子条目都保持原值。
    goal_record = next(
        record
        for record in harness.records()
        if record.dimension is FourDimension.STAGE_GOAL
    )
    assert goal_record.content == GOAL_TEXT
    assert _goal_item(harness).text == GOAL_TEXT

    harness.retry()

    goal_record = next(
        record
        for record in harness.records()
        if record.dimension is FourDimension.STAGE_GOAL
    )
    assert goal_record.content == CORRECTED_TEXT
    # 重试与首次处理一致：来源已改，用户可见条目同步更新。
    assert _stage_goal_item(harness).text == CORRECTED_TEXT
    assert [task.status for task in harness.service.list_retry_tasks(ACCOUNT)] == [
        ProfileExtractionStatus.SUCCEEDED
    ]


def test_correction_keeps_user_edited_item(harness: _Harness) -> None:
    """更正写入四维来源，但用户编辑过的条目保持用户正文。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    goal = _goal_item(harness)
    harness.atomic.modify_item(
        ACCOUNT,
        goal.profile_item_id,
        AtomicProfileItemModifyRequest(text="目标改成明年完成", version=goal.version),
    )

    result = harness.correct("message-correction-1")

    assert result.correction is not None
    assert result.correction.status == ProfileCorrectionStatus.WRITTEN
    goal_record = next(
        record
        for record in harness.records()
        if record.dimension is FourDimension.STAGE_GOAL
    )
    assert goal_record.content == CORRECTED_TEXT
    assert _stage_goal_item(harness).text == "目标改成明年完成"


def test_correction_unresolved_and_protected_results_are_preserved(
    harness: _Harness,
) -> None:
    """无法定位与受保护阈值命中的更正保持现行语义，不误写。"""

    result = harness.correct("message-correction-1", "我改主意了，换成研一")
    assert result.correction is not None
    assert result.correction.status == ProfileCorrectionStatus.NO_ACTIVE_RECORD

    harness.extract("message-1", TWO_FACT_MESSAGE)
    for attempt in range(3):
        harness.correct(f"message-correction-{attempt + 2}")
    fourth = harness.correct("message-correction-9")
    assert fourth.correction is not None
    assert fourth.correction.status == ProfileCorrectionStatus.PROTECTED
    goal_record = next(
        record
        for record in harness.records()
        if record.dimension is FourDimension.STAGE_GOAL
    )
    assert goal_record.correction_count == 3


# -- 重放不得改回用户纠正过的内容 ------------------------------------------


def test_replay_does_not_revert_chat_correction(harness: _Harness) -> None:
    """旧消息重放走同一提交规则：来源与条目都不回到纠正前的旧值。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    harness.correct("message-correction-1")
    assert _stage_goal_item(harness).text == CORRECTED_TEXT

    run = harness.state.list_runs(ACCOUNT)[0]
    replay_hash = f"{run.source_hash}{PROFILE_REPLAY_SOURCE_HASH_PREFIX}"
    replay_run = run.model_copy(
        update={
            "extraction_id": f"{run.extraction_id}-replay",
            "source_hash": replay_hash,
            "status": ProfileExtractionStatus.PENDING,
            "outcome": ProfileExtractionOutcome.PENDING_RETRY,
            "attempts": 0,
            "source_snapshot": TWO_FACT_MESSAGE,
        }
    )
    task = ProfileExtractionRetryTask(
        task_id=f"task-{replay_hash}",
        account_id=ACCOUNT,
        message_id=run.message_id,
        extractor_version=run.extractor_version,
        source_hash=replay_hash,
        status=ProfileExtractionStatus.PENDING,
        attempts=0,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )
    harness.state.save_run(replay_run)
    harness.state.save_task(task)
    queue = harness.state.durable_queue()
    if queue is not None:
        queue.enqueue(
            PROFILE_EXTRACTION_QUEUE,
            task.task_id,
            payload={
                "account_id": ACCOUNT,
                "message_id": task.message_id,
                "extractor_version": task.extractor_version,
                "source_hash": replay_hash,
            },
        )

    harness.retry()

    goal_record = next(
        record
        for record in harness.records()
        if record.dimension is FourDimension.STAGE_GOAL
    )
    assert goal_record.content == CORRECTED_TEXT
    assert goal_record.correction_count == 1
    assert _stage_goal_item(harness).text == CORRECTED_TEXT


def test_new_message_still_updates_corrected_record_by_evidence_ladder(
    harness: _Harness,
) -> None:
    """重放保护只作用于重放：新消息的自动写入仍按既有阶梯更新。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    harness.correct("message-correction-1")

    # 用户在新消息里再次陈述同一事实：不是重放，按既有证据阶梯覆盖纠正值。
    harness.extract("message-2", TWO_FACT_MESSAGE)

    goal_record = next(
        record
        for record in harness.records()
        if record.dimension is FourDimension.STAGE_GOAL
    )
    assert goal_record.content == GOAL_TEXT
    assert goal_record.correction_count == 1


# -- 删除/忘掉：墓碑保护点与幂等撤回 ----------------------------------------


def test_delete_tombstone_survives_source_withdrawal_failure(
    harness: _Harness,
) -> None:
    """来源撤回失败时墓碑保持有效，条目不可召回，撤回可幂等补做。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    goal = _goal_item(harness)
    harness.dimensions.fail_at = 1

    with pytest.raises(RuntimeError, match="来源撤回写入失败"):
        harness.atomic.delete_item(ACCOUNT, goal.profile_item_id, goal.version)

    # 墓碑已提交：列表与切片都不再返回该事实；失败未回滚墓碑。
    assert _item_by_id(harness, goal.profile_item_id).status is (
        AtomicProfileItemStatus.WITHDRAWN
    )
    assert {item.text for item in harness.items()} == {STATUS_TEXT}
    assert GOAL_TEXT not in harness.slice_texts()
    goal_record = next(
        record
        for record in harness.records()
        if record.dimension is FourDimension.STAGE_GOAL
    )
    assert goal_record.status.value == "active"
    version_before = goal_record.version

    # 同一撤回操作幂等补做：补做成功，重复执行不再改写业务结果。
    first = harness.atomic._profile_commit.withdraw_item_sources(ACCOUNT, [goal])
    second = harness.atomic._profile_commit.withdraw_item_sources(ACCOUNT, [goal])
    assert [outcome.status for outcome in first] == [SourceWithdrawalStatus.WITHDRAWN]
    assert [outcome.status for outcome in second] == [
        SourceWithdrawalStatus.ALREADY_WITHDRAWN
    ]
    goal_record = next(
        record
        for record in harness.all_records()
        if record.dimension is FourDimension.STAGE_GOAL
    )
    assert goal_record.version == version_before + 1
    # 已删除条目保持不可召回，用户新编辑不受影响。
    assert {item.text for item in harness.items()} == {STATUS_TEXT}


def test_forget_partial_source_failure_stays_recoverable(harness: _Harness) -> None:
    """多条忘掉的部分来源失败：结果明确、其余条目继续撤回、可幂等恢复。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    goal = _goal_item(harness)
    harness.dimensions.fail_at = 1

    result = harness.atomic.forget(ACCOUNT, "完成目标")

    # 忘掉按本轮合同成立：命中的条目已墓碑化，来源撤回失败不回滚。
    assert result.status is AtomicProfileMemoryStatus.FORGOTTEN
    assert result.matched_count == 1
    assert _item_by_id(harness, goal.profile_item_id).status is (
        AtomicProfileItemStatus.WITHDRAWN
    )
    assert GOAL_TEXT not in harness.slice_texts()
    assert STATUS_TEXT in harness.slice_texts()
    goal_record = next(
        record
        for record in harness.records()
        if record.dimension is FourDimension.STAGE_GOAL
    )
    assert goal_record.status.value == "active"

    # 幂等补做失败的那条来源；重复执行同一撤回不重写业务结果。
    outcome = harness.atomic._profile_commit.withdraw_item_sources(ACCOUNT, [goal])
    assert [entry.status for entry in outcome] == [SourceWithdrawalStatus.WITHDRAWN]
    goal_record = next(
        record
        for record in harness.all_records()
        if record.dimension is FourDimension.STAGE_GOAL
    )
    assert goal_record.status.value == "withdrawn"

    # 重发同一条忘掉指令：没有活动条目可删，如实返回未完成，不误删。
    again = harness.atomic.forget(ACCOUNT, "完成目标")
    assert again.status is AtomicProfileMemoryStatus.UNRESOLVED
    assert again.matched_count == 0


def test_forget_without_clear_target_does_not_delete(harness: _Harness) -> None:
    """目标不明确或未命中时不误删：无墓碑、无撤回。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    versions_before = {
        record.record_id: record.version for record in harness.records()
    }

    result = harness.atomic.forget(ACCOUNT, "不存在的目标")

    assert result.status is AtomicProfileMemoryStatus.UNRESOLVED
    assert {item.text for item in harness.items()} == {GOAL_TEXT, STATUS_TEXT}
    assert {
        record.record_id: record.version for record in harness.records()
    } == versions_before


def test_repeated_withdrawal_does_not_override_new_user_item(
    harness: _Harness,
) -> None:
    """删除后用户重新记住同一事实：旧撤回的幂等重跑不影响新条目。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    goal = _goal_item(harness)
    harness.atomic.delete_item(ACCOUNT, goal.profile_item_id, goal.version)

    harness.extract("message-remember", f"记住：{GOAL_TEXT}")
    remembered = [item for item in harness.items() if item.text == GOAL_TEXT]
    assert len(remembered) == 1

    outcome = harness.atomic._profile_commit.withdraw_item_sources(ACCOUNT, [goal])
    assert [entry.status for entry in outcome] == [
        SourceWithdrawalStatus.ALREADY_WITHDRAWN
    ]
    assert [item.text for item in harness.items() if item.text == GOAL_TEXT] == [
        GOAL_TEXT
    ]


# -- 记住：本轮立即生效，重复指令不产生重复条目 ------------------------------


def test_remember_is_effective_this_round_and_idempotent(harness: _Harness) -> None:
    """显式记住当轮即可用；重复指令只合并来源，不新建重复条目。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    result = harness.service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-remember-1",
        content=f"记住：{REMEMBER_TEXT}",
        run_id="run-remember-1",
        mode="study",
    )

    assert result.memory is not None
    assert result.memory.status is AtomicProfileMemoryStatus.REMEMBERED
    remembered = [item for item in harness.items() if item.text == REMEMBER_TEXT]
    assert len(remembered) == 1
    assert remembered[0].write_origin.value == "user"
    # 用户明确记住的条目本轮就进入切片（不受自动条目「下一轮生效」约束）。
    assert REMEMBER_TEXT in harness.slice_texts(current_message_id="message-remember-1")

    harness.service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-remember-2",
        content=f"记住：{REMEMBER_TEXT}",
        run_id="run-remember-2",
        mode="study",
    )

    remembered = [item for item in harness.items() if item.text == REMEMBER_TEXT]
    assert len(remembered) == 1
    assert "message-remember-2" in remembered[0].source_message_ids


# -- 行内编辑：版本、重复正文、旧正文抑制与账户隔离 --------------------------


def test_inline_edit_version_and_duplicate_conflicts(harness: _Harness) -> None:
    """行内编辑保留版本检查与重复正文冲突，文案不变。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    goal = _goal_item(harness)

    with pytest.raises(AtomicProfileError, match="版本冲突，请刷新后重试。"):
        harness.atomic.modify_item(
            ACCOUNT,
            goal.profile_item_id,
            AtomicProfileItemModifyRequest(text="任意新正文", version=goal.version + 5),
        )

    status_item = next(
        item for item in harness.items() if item.text == STATUS_TEXT
    )
    with pytest.raises(
        AtomicProfileError, match="已存在内容相同的信息，请先删除其中一条。"
    ):
        harness.atomic.modify_item(
            ACCOUNT,
            goal.profile_item_id,
            AtomicProfileItemModifyRequest(text=STATUS_TEXT, version=goal.version),
        )
    assert status_item.text == STATUS_TEXT


def test_inline_edit_suppresses_old_text_across_accounts(harness: _Harness) -> None:
    """旧正文经抑制键不再生成条目；另一账户的同正文条目完全隔离。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    harness.extract("message-2", TWO_FACT_MESSAGE, account=OTHER_ACCOUNT)
    goal = _goal_item(harness)

    harness.atomic.modify_item(
        ACCOUNT,
        goal.profile_item_id,
        AtomicProfileItemModifyRequest(text="目标改成明年完成", version=goal.version),
    )

    # 旧消息重放（重复抽取旧正文）不会把改掉的正文作为新条目带回。
    harness.extract("message-replay-old", TWO_FACT_MESSAGE)
    assert {item.text for item in harness.items()} == {
        "目标改成明年完成",
        STATUS_TEXT,
    }
    # 其他账户不受本账户编辑与抑制影响。
    assert {item.text for item in harness.items(OTHER_ACCOUNT)} == {
        GOAL_TEXT,
        STATUS_TEXT,
    }


def test_delete_suppresses_old_text_against_replay(harness: _Harness) -> None:
    """删除后的旧正文不会因旧消息重放复活。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    goal = _goal_item(harness)
    harness.atomic.delete_item(ACCOUNT, goal.profile_item_id, goal.version)

    harness.extract("message-replay-old", TWO_FACT_MESSAGE)

    assert {item.text for item in harness.items()} == {STATUS_TEXT}
    assert GOAL_TEXT not in harness.slice_texts()
