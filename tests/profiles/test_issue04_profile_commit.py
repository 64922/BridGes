"""Issue 04：自动提取与重试通过同一提交规则维护四维记录与原子条目。

覆盖三条真实调用路径共同遵守的提交合同：首次自动写入、后台重试，以及
「批次中前一条成功、后续镜像失败」的故障窗口。核心场景在内存与 SQLite
两种 adapter 上各跑一遍；混合组合（状态仓库落 SQLite、画像仓库在内存）
再证明事务边界由仓库 adapter 自己声明，而不是由调用方判断实现类型、
连接或事务开关。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridges.contracts.atomic_profile import (
    AtomicProfileItem,
    AtomicProfileItemModifyRequest,
)
from bridges.contracts.observability import AuditAction, AuditEvent, AuditResult
from bridges.contracts.profile_extraction import (
    AutomaticProfileObservation,
    ProfileExtractionOutput,
    ProfileExtractionRetryTask,
    ProfileExtractionStatus,
    ProfilePreprocessResult,
)
from bridges.contracts.profiles import FourDimension, FourDimensionProfileRecord
from bridges.observability.service import ObservabilityService
from bridges.profiles.atomic import (
    AtomicProfileRepository,
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    SqliteAtomicProfileRepository,
)
from bridges.profiles.automatic import (
    AutomaticProfileRepository,
    AutomaticProfileService,
    InMemoryAutomaticProfileRepository,
    SqliteAutomaticProfileRepository,
)
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
# 与两条事实都相关的提问：下一轮最小切片据此判定相关性。
RELATED_QUESTION = "我作为大学生怎么完成目标 A 的复盘"


class _TwoFactExtractor:
    """一次抽出一条阶段目标与一条学业情况，用于制造两写一批的批次。"""

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


class _MirrorFailsOnce(AtomicProfileService):
    """`fail_at` 指定第几次镜像写入失败（0 表示不注入故障）。"""

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


class _Harness:
    """被测组合：自动抽取 + 四维记录 + 用户可见原子列表。"""

    def __init__(
        self,
        *,
        service: AutomaticProfileService,
        state: AutomaticProfileRepository,
        dimensions: FourDimensionProfileService,
        atomic: _MirrorFailsOnce,
        database: BridgesDatabase | None,
        observability: ObservabilityService,
    ) -> None:
        self.service = service
        self.state = state
        self.dimensions = dimensions
        self.atomic = atomic
        self.observability = observability
        self._database = database

    @property
    def database(self) -> BridgesDatabase:
        """底层 SQLite 库；只有内存组合没有。"""

        assert self._database is not None
        return self._database

    def close(self) -> None:
        if self._database is not None:
            self._database.close()

    def extract(
        self,
        message_id: str,
        content: str,
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

    def records(self, account: str = ACCOUNT) -> list[FourDimensionProfileRecord]:
        return self.dimensions.list_records(account)

    def items(self, account: str = ACCOUNT) -> list[AtomicProfileItem]:
        return self.atomic.list_items(account)

    def observations(
        self, account: str = ACCOUNT
    ) -> list[AutomaticProfileObservation]:
        """本批次两条事实的观察记录（端口按事实查询，这里合并后返回）。"""

        return [
            observation
            for dimension, value in (
                (FourDimension.STAGE_GOAL, GOAL_TEXT),
                (FourDimension.ACADEMIC_STATUS, STATUS_TEXT),
            )
            for observation in self.state.list_observations(
                account,
                dimension,
                value,
                since=datetime(2020, 1, 1, tzinfo=UTC),
            )
        ]

    def tasks(self, account: str = ACCOUNT) -> list[ProfileExtractionRetryTask]:
        return self.service.list_retry_tasks(account)

    def write_audits(self, account: str = ACCOUNT) -> list[AuditEvent]:
        return self.observability.list_audit_events(
            account_id=account, action=AuditAction.PROFILE_AUTO_WRITE
        )

    def retry(self) -> None:
        self.service.run_retry_tick()


def _build(
    flavor: str,
    tmp_path: Path,
    *,
    observability: ObservabilityService | None = None,
) -> _Harness:
    """按 adapter 组合装配生产同形的接线：``memory``、``sqlite``、``mixed``。"""

    database = (
        None if flavor == "memory" else BridgesDatabase(tmp_path / "bridges.db")
    )
    if database is not None:
        database.initialize()
    dimension_repository: FourDimensionProfileRepository
    atomic_repository: AtomicProfileRepository
    state: AutomaticProfileRepository
    if flavor == "sqlite":
        assert database is not None
        dimension_repository = SqliteFourDimensionProfileRepository(database)
        atomic_repository = SqliteAtomicProfileRepository(database)
        state = SqliteAutomaticProfileRepository(database)
    elif flavor == "mixed":
        assert database is not None
        dimension_repository = InMemoryFourDimensionProfileRepository()
        atomic_repository = InMemoryAtomicProfileRepository()
        state = SqliteAutomaticProfileRepository(database)
    else:
        dimension_repository = InMemoryFourDimensionProfileRepository()
        atomic_repository = InMemoryAtomicProfileRepository()
        state = InMemoryAutomaticProfileRepository()

    dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=dimension_repository,
    )
    atomic = _MirrorFailsOnce(dimensions, atomic_repository)
    return _Harness(
        service=AutomaticProfileService(
            four_dimension_service=dimensions,
            repository=state,
            extractor=_TwoFactExtractor(),
            observability_service=observability,
            atomic_profile_service=atomic,
        ),
        state=state,
        dimensions=dimensions,
        atomic=atomic,
        database=database,
        observability=observability or ObservabilityService(),
    )


@pytest.fixture(params=["memory", "sqlite"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Harness]:
    """同一组核心场景覆盖内存与 SQLite 两种 adapter。"""

    built = _build(request.param, tmp_path, observability=ObservabilityService())
    try:
        yield built
    finally:
        built.close()


def test_mirror_failure_keeps_no_partial_profile(harness: _Harness) -> None:
    """第一条已写入、第二条镜像失败时，整批回滚为零写入。"""

    harness.atomic.fail_at = 2

    result = harness.extract("message-1", TWO_FACT_MESSAGE)

    assert result.run.status == ProfileExtractionStatus.PENDING
    assert result.run.committed_record_ids == []
    # 四维记录、观察记录与用户可见原子条目同属一次提交：一起回滚。
    assert harness.records() == []
    assert harness.observations() == []
    assert harness.items() == []
    # 尝试记录保留：失败按可重试记账、有界重试按既有策略继续，且没有虚假成功。
    assert [(task.status, task.last_error) for task in harness.tasks()] == [
        (ProfileExtractionStatus.PENDING, "profile_extraction_unexpected")
    ]
    assert [event.result for event in harness.write_audits()] == [
        AuditResult.RETRYABLE_FAIL
    ]


def test_retry_after_failure_writes_each_fact_once_and_feeds_next_slice(
    harness: _Harness,
) -> None:
    """故障后按既有重试策略补齐：不重复条目，下一轮最小切片能用上。"""

    harness.atomic.fail_at = 2
    harness.extract("message-1", TWO_FACT_MESSAGE)
    assert harness.items() == []

    harness.retry()

    assert {record.content for record in harness.records()} == {
        GOAL_TEXT,
        STATUS_TEXT,
    }
    items = harness.items()
    assert {item.text for item in items} == {GOAL_TEXT, STATUS_TEXT}
    assert all(item.source_message_ids == ["message-1"] for item in items)
    assert [task.status for task in harness.tasks()] == [
        ProfileExtractionStatus.SUCCEEDED
    ]
    assert [event.result for event in harness.write_audits()] == [
        AuditResult.RETRYABLE_FAIL,
        AuditResult.SUCCESS,
    ]

    # 下一轮读取：只按现有最小切片规则取出与当前任务相关的条目。
    slice_ = harness.atomic.compile_chat_slice(
        ACCOUNT, run_id="next-round", current_question=RELATED_QUESTION
    )
    assert {item.value_or_rule for item in slice_.included_items} == {
        GOAL_TEXT,
        STATUS_TEXT,
    }
    assert (
        harness.atomic.compile_chat_slice(
            ACCOUNT, run_id="unrelated-round", current_question="今天天气怎么样"
        ).included_items
        == []
    )


def test_mixed_adapters_roll_back_all_three_stores_together(tmp_path: Path) -> None:
    """状态仓库是 SQLite、画像仓库是内存时，回滚覆盖同一组业务对象。"""

    harness = _build("mixed", tmp_path, observability=ObservabilityService())
    harness.atomic.fail_at = 2

    result = harness.extract("message-1", TWO_FACT_MESSAGE)

    assert result.run.status == ProfileExtractionStatus.PENDING
    assert harness.records() == []
    assert harness.items() == []
    assert harness.observations() == []

    harness.retry()

    assert {item.text for item in harness.items()} == {GOAL_TEXT, STATUS_TEXT}
    harness.close()


def test_shared_sqlite_connection_opens_one_commit_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """三个仓库共用一个 SQLite 连接时只开一次事务，不出现嵌套 BEGIN。"""

    harness = _build("sqlite", tmp_path)
    entered: list[int] = []
    original = harness.database.transaction

    @contextmanager
    def counting() -> Iterator[None]:
        entered.append(1)
        with original():
            yield

    monkeypatch.setattr(harness.database, "transaction", counting)

    result = harness.extract("message-1", TWO_FACT_MESSAGE)

    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    assert len(entered) == 1
    assert {item.text for item in harness.items()} == {GOAL_TEXT, STATUS_TEXT}
    harness.close()


def test_repeated_fact_merges_sources_without_duplicate_items(
    harness: _Harness,
) -> None:
    """同一事实跨消息重复出现只合并来源，不增加重复活动条目。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    harness.extract("message-2", TWO_FACT_MESSAGE)

    assert len(harness.records()) == 2
    items = harness.items()
    assert {item.text for item in items} == {GOAL_TEXT, STATUS_TEXT}
    for item in items:
        assert item.source_message_ids == ["message-1", "message-2"]

    # 另一个账户的同正文事实完全隔离。
    harness.extract("message-1", TWO_FACT_MESSAGE, account=OTHER_ACCOUNT)
    assert {item.text for item in harness.items()} == {GOAL_TEXT, STATUS_TEXT}
    assert len(harness.records(OTHER_ACCOUNT)) == 2
    assert harness.items(OTHER_ACCOUNT)[0].source_message_ids == ["message-1"]


def test_user_edited_item_is_a_normal_result_not_a_commit_failure(
    harness: _Harness,
) -> None:
    """用户编辑过的正文不被自动提取覆盖，且「不写入」不算系统失败。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    goal = next(item for item in harness.items() if item.text == GOAL_TEXT)
    harness.atomic.modify_item(
        ACCOUNT,
        goal.profile_item_id,
        AtomicProfileItemModifyRequest(text="目标改成明年完成", version=goal.version),
    )

    result = harness.extract("message-2", TWO_FACT_MESSAGE)

    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    assert result.run.outcome.value.startswith("succeeded")
    assert {item.text for item in harness.items()} == {
        "目标改成明年完成",
        STATUS_TEXT,
    }


def test_retry_does_not_revive_a_deleted_fact(harness: _Harness) -> None:
    """用户删除的事实不会被自动提取或重试复活，也不会记成虚假成功。"""

    harness.extract("message-1", TWO_FACT_MESSAGE)
    goal = next(item for item in harness.items() if item.text == GOAL_TEXT)
    harness.atomic.delete_item(ACCOUNT, goal.profile_item_id, goal.version)

    result = harness.extract("message-2", TWO_FACT_MESSAGE)
    assert result.run.status == ProfileExtractionStatus.PENDING
    for _ in range(3):
        harness.retry()

    # 被删的事实不复活；同批次的其他事实留在删除前的状态，没有被部分提交。
    assert {item.text for item in harness.items()} == {STATUS_TEXT}
    assert [item.source_message_ids for item in harness.items()] == [["message-1"]]
    # 删除后的再次提取无法复活的事实落到「重试耗尽」，不是成功。
    assert [task.status for task in harness.tasks()] == [
        ProfileExtractionStatus.EXHAUSTED
    ]
    runs = {run.message_id: run for run in harness.state.list_runs(ACCOUNT)}
    assert runs["message-2"].status == ProfileExtractionStatus.EXHAUSTED
    assert runs["message-2"].outcome.value == "permanent_failure"
