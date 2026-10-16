"""工单 03 验收：开放记忆的手动管理与单一权威写入走查。

覆盖演示路径：聊天说出「记住」→ 页面查看/编辑 → 相关下一轮采用修改后的
内容 → 删除 → 重启状态一致；主题可空/多选/可扩展；用户编辑优先、陈旧
版本与跨账户被拒；新写入不再产生四维记录/镜像副作用。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bridges.contracts.atomic_profile import (
    AtomicProfileItemCreateRequest,
    AtomicProfileItemModifyRequest,
    AtomicProfileWriteOrigin,
)
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
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
from bridges.profiles.atomic import AtomicProfileError
from bridges.profiles.commit import ProfileFactSubmission
from bridges.profiles.purpose import build_purpose
from bridges.storage.database import BridgesDatabase

ALICE = "account-alice"
BOB = "account-bob"


def _services() -> tuple[
    FourDimensionProfileService, AtomicProfileService, AutomaticProfileService
]:
    four_dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    atomic = AtomicProfileService(four_dimensions, InMemoryAtomicProfileRepository())
    automatic = AutomaticProfileService(
        four_dimension_service=four_dimensions,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
        atomic_profile_service=atomic,
    )
    return four_dimensions, atomic, automatic


class _SqliteStack:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.database = BridgesDatabase(path)
        self.database.initialize()
        self.four_dimensions = FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=SqliteFourDimensionProfileRepository(self.database),
        )
        self.atomic = AtomicProfileService(
            self.four_dimensions, SqliteAtomicProfileRepository(self.database)
        )
        self.automatic = AutomaticProfileService(
            four_dimension_service=self.four_dimensions,
            repository=SqliteAutomaticProfileRepository(self.database),
            extractor=RuleBasedAutomaticProfileExtractor(),
            atomic_profile_service=self.atomic,
        )

    def close(self) -> None:
        self.database.close()


def _remember_pet(stack: _SqliteStack, *, number: int = 1) -> None:
    result = stack.automatic.preprocess_message(
        ALICE,
        conversation_id=f"conversation-{number}",
        message_id=f"message-{number}",
        content="记住，我养了一只猫叫团子",
        run_id=f"run-{number}",
        mode="companion",
    )
    assert result is not None and result.run.outcome.value.startswith("succeeded")


def _adopted_texts(stack: _SqliteStack, query: str) -> list[str]:
    slice_ = stack.atomic.compile_adopted_slice(
        ALICE, run_id=f"slice-{query}", purpose=build_purpose(mode="companion", query=query)
    )
    return [item.fact_text for item in slice_.adopted_items]


def test_manual_roundtrip_saves_edits_adopts_and_survives_restart(
    tmp_path: Path,
) -> None:
    """演示走查：记住 → 查看 → 采用 → 编辑 → 重启 → 删除后续管理一致。"""

    path = tmp_path / "open-memory.db"
    stack = _SqliteStack(path)
    _remember_pet(stack)

    items = stack.atomic.list_items(ALICE)
    assert len(items) == 1
    assert items[0].write_origin == AtomicProfileWriteOrigin.USER
    assert "团子" in items[0].text
    projections = stack.atomic.projections(ALICE)
    assert len(projections) == 1 and "团子" in projections[0].text
    assert any("团子" in text for text in _adopted_texts(stack, "团子"))

    item = items[0]
    edited = stack.atomic.modify_item(
        ALICE,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(text="我养了一只猫叫圆子", version=item.version),
    )
    assert edited.text == "我养了一只猫叫圆子"
    assert edited.user_edited_at is not None
    assert any("圆子" in text for text in _adopted_texts(stack, "圆子"))

    stack.close()
    reopened = _SqliteStack(path)
    restored = reopened.atomic.list_items(ALICE)
    assert [entry.text for entry in restored] == ["我养了一只猫叫圆子"]

    again = reopened.atomic.modify_item(
        ALICE,
        restored[0].profile_item_id,
        AtomicProfileItemModifyRequest(
            text="我养了一只猫叫团团", version=restored[0].version
        ),
    )
    reopened.atomic.delete_item(ALICE, again.profile_item_id, again.version)
    assert reopened.atomic.list_items(ALICE) == []
    assert reopened.atomic.projections(ALICE) == []
    assert reopened.four_dimensions.list_records(ALICE) == []

    reopened.close()
    after_delete = _SqliteStack(path)
    assert after_delete.atomic.list_items(ALICE) == []
    assert after_delete.four_dimensions.list_records(ALICE) == []
    after_delete.close()


def test_open_topics_stay_optional_multi_and_extensible() -> None:
    _, atomic, _ = _services()

    first = atomic.add_item(
        ALICE, AtomicProfileItemCreateRequest(text="我养了一只猫叫团子")
    )
    assert first.topics == []

    merged = atomic.add_item(
        ALICE,
        AtomicProfileItemCreateRequest(
            text="我养了一只猫叫团子", topics=["宠物", "生活"]
        ),
    )
    assert merged.profile_item_id == first.profile_item_id
    assert merged.topics == ["宠物", "生活"]
    assert len(atomic.list_items(ALICE)) == 1

    retopiced = atomic.modify_item(
        ALICE,
        merged.profile_item_id,
        AtomicProfileItemModifyRequest(
            text=merged.text, version=merged.version, topics=["生活健康"]
        ),
    )
    assert retopiced.profile_item_id == first.profile_item_id
    assert retopiced.text == merged.text
    assert retopiced.topics == ["生活健康"]


def test_user_authority_stale_version_and_cross_account_are_rejected() -> None:
    _, atomic, _ = _services()
    proposal = ProfileFactSubmission(
        text="我养了一只猫叫团子",
        dimension=FourDimension.HOBBY,
        confidence=FourDimensionConfidence.MEDIUM,
        evidence_message_id="message-auto-1",
    )
    item = atomic.commit_fact(ALICE, proposal)
    assert item is not None and item.write_origin == AtomicProfileWriteOrigin.AUTOMATIC

    with pytest.raises(AtomicProfileError):
        atomic.modify_item(
            ALICE,
            item.profile_item_id,
            AtomicProfileItemModifyRequest(text="我养了一只猫叫圆子", version=99),
        )

    edited = atomic.modify_item(
        ALICE,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(text="我养了一只猫叫圆子", version=item.version),
    )
    assert edited.write_origin == AtomicProfileWriteOrigin.USER

    late_proposal = atomic.commit_fact(
        ALICE,
        ProfileFactSubmission(
            text="我养了一只猫叫团子",
            dimension=FourDimension.HOBBY,
            confidence=FourDimensionConfidence.HIGH,
            evidence_message_id="message-auto-2",
        ),
    )
    assert late_proposal is None
    assert [entry.text for entry in atomic.list_items(ALICE)] == ["我养了一只猫叫圆子"]

    with pytest.raises(AtomicProfileError):
        atomic.modify_item(
            BOB,
            edited.profile_item_id,
            AtomicProfileItemModifyRequest(text="劫持", version=edited.version),
        )
    with pytest.raises(AtomicProfileError):
        atomic.delete_item(BOB, edited.profile_item_id, edited.version)
    assert atomic.list_items(BOB) == []
    assert [entry.text for entry in atomic.list_items(ALICE)] == ["我养了一只猫叫圆子"]


def test_deletion_suppresses_automatic_revival_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "open-memory-delete.db"
    stack = _SqliteStack(path)
    proposal = ProfileFactSubmission(
        text="我每周三晚上打羽毛球",
        confidence=FourDimensionConfidence.MEDIUM,
        evidence_message_id="msg-auto-badminton",
    )
    stack.atomic.commit_fact(ALICE, proposal)
    assert any("羽毛球" in text for text in _adopted_texts(stack, "羽毛球"))

    item = stack.atomic.list_items(ALICE)[0]
    stack.atomic.delete_item(ALICE, item.profile_item_id, item.version)
    assert all("羽毛球" not in text for text in _adopted_texts(stack, "羽毛球"))
    stack.close()

    reopened = _SqliteStack(path)
    revived = reopened.atomic.commit_fact(ALICE, proposal)
    assert revived is None
    assert reopened.atomic.list_items(ALICE) == []
    reopened.close()
