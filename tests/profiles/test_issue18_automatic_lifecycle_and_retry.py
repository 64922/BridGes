"""Issue 18：自动抽取入口的生命周期信号与后台重试时间锚。

「暂时不考研」「考完了」这类明确信号本身不是新事实，也不会被普通信号分类器
当成可抽取内容；本票在消息预处理入口先做目标生命周期对账，使它们在停止记录
期间仍然即时生效，且不写出新事实。

后台重试（首次镜像失败后）必须沿用原消息时间作为相对时间的锚，而不是按重试
当天重算：否则「下周考试」会在重试后变成另一个时间窗口。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.contracts.atomic_profile import (
    AtomicProfileFactRelation,
    AtomicProfileGoalState,
    AtomicProfileItem,
)
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutcome,
    ProfileExtractionOutput,
    ProfileExtractionStatus,
)
from bridges.contracts.profiles import (
    FourDimension,
    FourDimensionConfidence,
    FourDimensionProfileRecord,
)
from bridges.profiles.adapters import InMemoryProfileRepository
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
from bridges.storage.database import BridgesDatabase

ACCOUNT = "account-alice"
ANCHOR = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
EXAM_TEXT = "我下周有考试"
EXAM_MESSAGE = "我的目标是下周通过考试"


class _FixedExtractor:
    """按消息内容返回一条固定事实的确定性抽取器。"""

    version = "issue18-fixed-v1"

    def extract(
        self,
        *,
        message_id: str,
        content: str,
        **_ignored: object,
    ) -> ProfileExtractionOutput:
        if "考研" in content:
            dimension = FourDimension.STAGE_GOAL.value
            value = "考研"
        else:
            dimension = FourDimension.KNOWLEDGE_INTEREST.value
            value = EXAM_TEXT
        return ProfileExtractionOutput.model_validate(
            {
                "items": [
                    {
                        "dimension": dimension,
                        "normalized_value": value,
                        "evidence_ref": message_id,
                        "reliability": 0.9,
                        "action": "create",
                    }
                ]
            }
        )


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


@dataclass
class _Stack:
    database: BridgesDatabase | None
    dimensions: FourDimensionProfileRepository
    atomic: AtomicProfileRepository
    state: AutomaticProfileRepository

    def close(self) -> None:
        if self.database is not None:
            self.database.close()


def _open_stack(flavor: str, db_path: Path) -> _Stack:
    if flavor == "sqlite":
        database = BridgesDatabase(db_path)
        database.initialize()
        return _Stack(
            database=database,
            dimensions=SqliteFourDimensionProfileRepository(database),
            atomic=SqliteAtomicProfileRepository(database),
            state=SqliteAutomaticProfileRepository(database),
        )
    return _Stack(
        database=None,
        dimensions=InMemoryFourDimensionProfileRepository(),
        atomic=InMemoryAtomicProfileRepository(),
        state=InMemoryAutomaticProfileRepository(),
    )


class _Harness:
    def __init__(self, stack: _Stack, messages: dict[str, Any]) -> None:
        self._stack = stack
        self.dimensions = FourDimensionProfileService(
            source_repository=InMemoryProfileRepository(),
            repository=stack.dimensions,
        )
        self.atomic = _MirrorFailsOnce(self.dimensions, stack.atomic)
        self.service = AutomaticProfileService(
            four_dimension_service=self.dimensions,
            repository=stack.state,
            extractor=_FixedExtractor(),
            message_reader=lambda account_id, message_id: messages.get(message_id),
            atomic_profile_service=self.atomic,
        )

    def close(self) -> None:
        self._stack.close()

    def preprocess(
        self, message_id: str, content: str
    ) -> Any:
        return self.service.preprocess_message(
            ACCOUNT,
            conversation_id="conversation-1",
            message_id=message_id,
            content=content,
            run_id=f"run-{message_id}",
            mode="study",
        )

    def retry(self) -> str:
        return self.service.run_retry_tick()

    def block_recording(self) -> None:
        self._stack.state.block_recording(ACCOUNT, None, ANCHOR)

    def is_tombstoned(self, message_id: str) -> bool:
        return self._stack.state.is_message_tombstoned(ACCOUNT, message_id)


@pytest.fixture(params=["memory", "sqlite"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Harness]:
    messages = {
        "m-goal": SimpleNamespace(content="我的目标是考研", created_at=ANCHOR),
        "m-pause": SimpleNamespace(content="暂时不考研", created_at=ANCHOR),
        "m-exam": SimpleNamespace(content=EXAM_MESSAGE, created_at=ANCHOR),
    }
    built = _Harness(_open_stack(request.param, tmp_path / "bridges.db"), messages)
    try:
        yield built
    finally:
        built.close()


def test_pause_signal_applies_without_writing_new_fact(harness: _Harness) -> None:
    harness.preprocess("m-goal", "我的目标是考研")
    goals = [
        item
        for item in harness.atomic.list_items(ACCOUNT)
        if item.fact_relation is AtomicProfileFactRelation.GOAL
    ]
    assert [item.text for item in goals] == ["考研"]

    result = harness.preprocess("m-pause", "暂时不考研")

    assert result.run.outcome is (
        ProfileExtractionOutcome.SUCCEEDED_LIFECYCLE_SIGNAL
    )
    assert result.run.status is ProfileExtractionStatus.SUCCEEDED
    items = harness.atomic.list_items(ACCOUNT)
    assert [item.text for item in items] == ["考研"]
    goal = items[0]
    assert goal.goal_state is AtomicProfileGoalState.PAUSED
    slice_ = harness.atomic.compile_chat_slice(
        ACCOUNT, run_id="verify", current_question=None, now=ANCHOR
    )
    assert {item.value_or_rule for item in slice_.included_items} == set()


def test_blocked_recording_still_applies_pause_with_goal(
    harness: _Harness,
) -> None:
    """停止记录期间明确的暂停信号仍即时生效，不落墓碑、不写新事实。"""

    harness.preprocess("m-goal", "我的目标是考研")
    harness.block_recording()

    result = harness.preprocess("m-pause", "暂时不考研")

    assert result.run.outcome is (
        ProfileExtractionOutcome.SUCCEEDED_LIFECYCLE_SIGNAL
    )
    assert not harness.is_tombstoned("m-pause")
    items = harness.atomic.list_items(ACCOUNT)
    assert [item.text for item in items] == ["考研"]
    assert items[0].goal_state is AtomicProfileGoalState.PAUSED


def test_blocked_recording_tombstones_unmatched_lifecycle_phrase(
    harness: _Harness,
) -> None:
    """没有可对账目标时，停止记录期间的生命周期措辞按普通阻止处理。"""

    harness.block_recording()

    result = harness.preprocess("m-unmatched", "考完了")

    assert result.run.outcome is ProfileExtractionOutcome.NO_SIGNAL
    assert harness.is_tombstoned("m-unmatched")
    assert harness.atomic.list_items(ACCOUNT) == []


def test_retry_reanchors_relative_phrase_to_original_message(
    harness: _Harness,
) -> None:
    harness.atomic.fail_at = 1
    first = harness.preprocess("m-exam", EXAM_MESSAGE)
    assert first.run.status is ProfileExtractionStatus.PENDING
    assert harness.atomic.list_items(ACCOUNT) == []

    harness.retry()

    items = harness.atomic.list_items(ACCOUNT)
    assert [item.text for item in items] == [EXAM_TEXT]
    item = items[0]
    # 重试发生在真实“现在”，但锚仍是原消息时间，窗口不随重试漂移。
    assert item.validity_anchor_at == ANCHOR
    assert item.valid_until == datetime(2026, 1, 18, 23, 59, 59, tzinfo=UTC)
    assert item.confidence is FourDimensionConfidence.MEDIUM
