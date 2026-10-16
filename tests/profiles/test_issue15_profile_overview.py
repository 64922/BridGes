"""画像工单 15：整体概览的派生、主题组织与失效守卫。

覆盖验收：
- 每项概览陈述都逐段携带条目标识与版本，正文由条目原文原样组成，不出现
  无证据的人格、情绪或能力评价（不产生新句子）；
- 未知主题不丢条目、多主题不复制事实、主题调整不改变事实身份与授权；
- 编辑/删除后旧概览不再作为当前事实显示；迟到重建不能恢复旧内容（CAS）；
- 无记忆、整理中、部分可用与使用开关状态诚实，加载失败不伪造概览；
- 新派生状态按账户隔离、可备份可删除（迁移与生命周期见 storage 测试）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bridges.contracts.atomic_profile import (
    AtomicProfileItem,
    AtomicProfileItemModifyRequest,
    AtomicProfileWriteOrigin,
)
from bridges.contracts.profile_extraction import ProfileAccountControlsProjection
from bridges.contracts.profile_overview import (
    PROFILE_OVERVIEW_CONTRACT_VERSION,
    ProfileOverviewState,
)
from bridges.contracts.profiles import FourDimensionConfidence
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    ProfileSourceMessage,
    normalize_topics,
)
from bridges.profiles.commit import ProfileFactSubmission
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.overview import ProfileOverviewService
from bridges.profiles.overview_projection import (
    PROFILE_OVERVIEW_PARTIAL_NOTICE,
    PROFILE_OVERVIEW_REBUILDING_NOTICE,
    PROFILE_OVERVIEW_USAGE_DISABLED_NOTICE,
    valid_remaining_statements,
)
from bridges.profiles.overview_repository import (
    InMemoryProfileOverviewStore,
    SqliteProfileOverviewStore,
)
from bridges.storage.database import BridgesDatabase

ALICE = "account-alice"
BOB = "account-bob"
NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)

TEXT_NAME = "我叫小林"
TEXT_CAT = "我养了一只猫叫团子"
TEXT_HALF_HOUR = "我每晚只能抽出半小时"


class _Controls:
    def __init__(self) -> None:
        self.usage_enabled = True
        self.usage_control_version = 1

    def projection(self) -> ProfileAccountControlsProjection:
        return ProfileAccountControlsProjection(
            recording_enabled=True,
            usage_enabled=self.usage_enabled,
            usage_control_version=self.usage_control_version,
            usage_updated_at=None,
        )


def _env(
    *,
    store: InMemoryProfileOverviewStore | None = None,
    messages: dict[str, ProfileSourceMessage] | None = None,
) -> tuple[
    AtomicProfileService,
    ProfileOverviewService,
    _Controls,
    InMemoryProfileOverviewStore,
]:
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    atomic = AtomicProfileService(four, InMemoryAtomicProfileRepository())
    controls = _Controls()
    overview_store = store or InMemoryProfileOverviewStore()
    overview = ProfileOverviewService(
        atomic=atomic,
        overview_store=overview_store,
        controls_reader=lambda account_id: controls.projection(),
        message_reader=(
            (lambda account_id, message_id: messages.get(message_id))
            if messages is not None
            else None
        ),
    )
    atomic.set_profile_overview_listener(overview)
    return atomic, overview, controls, overview_store


def _message(message_id: str, content: str) -> ProfileSourceMessage:
    return ProfileSourceMessage(
        message_id=message_id,
        conversation_id="conversation-1",
        role="user",
        status="sent",
        content=content,
        created_at=NOW,
    )


def _seed(atomic: AtomicProfileService) -> dict[str, str]:
    name = atomic.remember(ALICE, TEXT_NAME, topics=["称呼"])
    cat = atomic.remember(ALICE, TEXT_CAT, topics=["兴趣爱好"])
    half_hour = atomic.remember(ALICE, TEXT_HALF_HOUR, topics=["现实约束"])
    assert name is not None and cat is not None and half_hour is not None
    return {
        "name": name.profile_item_id,
        "cat": cat.profile_item_id,
        "half_hour": half_hour.profile_item_id,
    }


def _all_segment_ids(overview) -> list[str]:
    return [
        segment.profile_item_id
        for statement in overview.statements
        for segment in statement.segments
    ]


def test_statements_are_verbatim_compositions_with_versions() -> None:
    atomic, overview_service, _, _ = _env()
    ids = _seed(atomic)
    items = {item.profile_item_id: item for item in atomic.list_items(ALICE)}

    overview = overview_service.list_overview(ALICE, now=NOW)

    assert overview.state is ProfileOverviewState.CURRENT
    assert overview.usage_enabled is True
    assert overview.notice is None
    assert overview.generated_at is not None
    for statement in overview.statements:
        # 陈述正文 = 各段原文用「；」连接：不产生新句子、不换措辞。
        assert statement.text == "；".join(
            segment.text for segment in statement.segments
        )
        for segment in statement.segments:
            item = items[segment.profile_item_id]
            assert segment.text == item.text
            assert segment.version == item.version
    # 三个条目全部逐段出现且只出现一次（多段集合无复制）。
    assert sorted(_all_segment_ids(overview)) == sorted(ids.values())


def test_primary_topic_grouping_single_appearance_custom_and_untopiced() -> None:
    atomic, overview_service, _, _ = _env()
    multi = atomic.remember(
        ALICE, "我在学线性代数", topics=["兴趣", "学习背景"]
    )
    custom = atomic.remember(ALICE, "我周末去社区做志愿者", topics=["志愿活动"])
    plain = atomic.remember(ALICE, "我家有一只狗")
    assert multi is not None and custom is not None and plain is not None

    overview = overview_service.list_overview(ALICE, now=NOW)

    by_segment = {
        segment.profile_item_id: statement
        for statement in overview.statements
        for segment in statement.segments
    }
    # 多主题条目只出现一次，归属默认主题顺序更靠前的「兴趣与喜好」。
    assert by_segment[multi.profile_item_id].topic == "兴趣与喜好"
    assert _all_segment_ids(overview).count(multi.profile_item_id) == 1
    # 自定义主题保留原文成组；无主题条目归入无主题陈述（最后），不丢失。
    assert by_segment[custom.profile_item_id].topic == "志愿活动"
    assert by_segment[plain.profile_item_id].topic is None
    assert overview.statements[-1].topic is None


def test_topic_alias_normalization_is_exact_and_identity_neutral() -> None:
    # 精确近义归一：空白折叠后完全相同才折叠；未知主题原样保留。
    assert normalize_topics(["爱好", " 兴趣 ", "宠物"]) == [
        "兴趣与喜好",
        "宠物",
    ]
    assert normalize_topics(["我的爱好清单"]) == ["我的爱好清单"]

    atomic, overview_service, _, _ = _env()
    item = atomic.remember(ALICE, "我喜欢爬山", topics=["爱好"])
    assert item is not None and item.topics == ["兴趣与喜好"]
    original_id = item.profile_item_id
    original_version = item.version
    original_text = item.text

    # 调整主题：不改变条目标识、用途与事实身份，只推进版本。
    updated = atomic.modify_item(
        ALICE,
        original_id,
        AtomicProfileItemModifyRequest(
            text=original_text, version=original_version, topics=["兴趣", "日常习惯"]
        ),
    )
    assert updated.profile_item_id == original_id
    assert updated.usage_purpose is None
    assert updated.topics == ["兴趣与喜好", "日常习惯与现实约束"]
    assert updated.version == original_version + 1

    overview = overview_service.list_overview(ALICE, now=NOW)
    statement = next(
        s for s in overview.statements if s.topic == "兴趣与喜好"
    )
    assert statement.segments[0].profile_item_id == item.profile_item_id
    assert statement.segments[0].version == updated.version


def test_overview_rebuilds_after_edit_and_delete_never_serves_old_content() -> None:
    atomic, overview_service, _, _ = _env()
    ids = _seed(atomic)
    cold = overview_service.list_overview(ALICE, now=NOW)
    assert cold.state is ProfileOverviewState.CURRENT
    assert TEXT_CAT in "".join(s.text for s in cold.statements)

    item = next(
        entry
        for entry in atomic.list_items(ALICE)
        if entry.profile_item_id == ids["cat"]
    )
    atomic.modify_item(
        ALICE,
        ids["cat"],
        AtomicProfileItemModifyRequest(
            text="我养了一只猫叫小白", version=item.version
        ),
    )
    edited = overview_service.list_overview(ALICE, now=NOW)
    joined = "".join(statement.text for statement in edited.statements)
    assert "小白" in joined
    assert TEXT_CAT not in joined

    fresh_item = next(
        entry
        for entry in atomic.list_items(ALICE)
        if entry.profile_item_id == ids["cat"]
    )
    atomic.delete_item(ALICE, ids["cat"], fresh_item.version)
    deleted = overview_service.list_overview(ALICE, now=NOW)
    joined = "".join(statement.text for statement in deleted.statements)
    assert "小白" not in joined
    assert ids["cat"] not in _all_segment_ids(deleted)


def test_projection_canonicalizes_legacy_topics_for_consistent_browsing() -> None:
    """旧数据的近义主题在页面投影里同样归一到与新写入一致的分组标签。"""

    legacy = AtomicProfileItem(
        profile_item_id="legacy-1",
        owner_account_id=ALICE,
        text="我喜欢爬山",
        identity_key="legacy-key",
        topics=["爱好", " 兴趣 ", "宠物"],
        status="active",
        write_origin=AtomicProfileWriteOrigin.AUTOMATIC,
        confidence=FourDimensionConfidence.HIGH,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    projection = AtomicProfileService.project(legacy)
    assert projection.topics == ["兴趣与喜好", "宠物"]


def test_late_rebuild_cannot_restore_old_content() -> None:
    atomic, overview_service, _, _ = _env()
    ids = _seed(atomic)
    overview_service.list_overview(ALICE, now=NOW)

    # 一个重建方先捕获世代与快照，随后用户完成了一次真实编辑。
    stored, snapshot = overview_service.begin_overview_rebuild(ALICE)
    assert stored is not None
    item = next(
        entry
        for entry in atomic.list_items(ALICE)
        if entry.profile_item_id == ids["cat"]
    )
    atomic.modify_item(
        ALICE,
        ids["cat"],
        AtomicProfileItemModifyRequest(
            text="我养了一只猫叫小白", version=item.version
        ),
    )

    # 迟到重建以旧世代提交必然失败，不能把旧内容写回。
    assert (
        overview_service.commit_overview_rebuild(
            ALICE, stored, snapshot, now=NOW
        )
        is False
    )

    refreshed = overview_service.list_overview(ALICE, now=NOW)
    joined = "".join(statement.text for statement in refreshed.statements)
    assert "小白" in joined and TEXT_CAT not in joined


def test_source_message_change_invalidates_fingerprint() -> None:
    messages = {"msg-cat": _message("msg-cat", TEXT_CAT)}
    atomic, overview_service, _, store = _env(messages=messages)
    item = atomic.commit_fact(
        ALICE,
        ProfileFactSubmission(
            text=TEXT_CAT, evidence_message_id="msg-cat", topics=("兴趣爱好",)
        ),
    )
    assert item is not None
    first = overview_service.list_overview(ALICE, now=NOW)
    assert first.state is ProfileOverviewState.CURRENT
    stored = store.read(ALICE)
    assert stored is not None
    fingerprint_before = stored.dependency_fingerprint

    messages["msg-cat"] = _message("msg-cat", "我养了一只猫叫团团")
    refreshed = overview_service.list_overview(ALICE, now=NOW)
    assert refreshed.state is ProfileOverviewState.CURRENT
    stored = store.read(ALICE)
    assert stored is not None
    assert stored.dependency_fingerprint != fingerprint_before


def test_usage_control_change_is_honest_and_keeps_management_view() -> None:
    atomic, overview_service, controls, _ = _env()
    _seed(atomic)
    overview_service.list_overview(ALICE, now=NOW)

    controls.usage_enabled = False
    controls.usage_control_version += 1
    overview_service.invalidate(ALICE, reason="controls")

    overview = overview_service.list_overview(ALICE, now=NOW)
    assert overview.state is ProfileOverviewState.CURRENT
    assert overview.usage_enabled is False
    assert overview.notice == PROFILE_OVERVIEW_USAGE_DISABLED_NOTICE
    # 关闭使用仍可查看与管理（与原子记忆的使用控制边界一致）。
    assert overview.statements


def test_empty_state_writes_no_overview_row() -> None:
    _, overview_service, _, store = _env()
    overview = overview_service.list_overview(ALICE, now=NOW)
    assert overview.state is ProfileOverviewState.EMPTY
    assert overview.statements == []
    assert overview.generated_at is None
    assert store.read(ALICE) is None


def test_rebuild_competition_returns_partial_and_rebuilding_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    atomic, overview_service, _, _ = _env()
    ids = _seed(atomic)
    overview_service.list_overview(ALICE, now=NOW)

    # 模拟重建竞争持续失败（CAS 始终丢失）：不得回退旧内容。
    monkeypatch.setattr(
        overview_service, "commit_overview_rebuild", lambda *a, **k: False
    )
    item = next(
        entry
        for entry in atomic.list_items(ALICE)
        if entry.profile_item_id == ids["cat"]
    )
    atomic.modify_item(
        ALICE,
        ids["cat"],
        AtomicProfileItemModifyRequest(
            text="我养了一只猫叫小白", version=item.version
        ),
    )

    partial = overview_service.list_overview(ALICE, now=NOW)
    assert partial.state is ProfileOverviewState.PARTIAL
    assert partial.notice == PROFILE_OVERVIEW_PARTIAL_NOTICE
    remaining_ids = _all_segment_ids(partial)
    # 只有依赖仍一致的旧陈述保留；被编辑条目的旧陈述不再展示。
    assert ids["cat"] not in remaining_ids
    assert ids["name"] in remaining_ids and ids["half_hour"] in remaining_ids

    # 全部条目的旧陈述都失效时如实返回「整理中」，不显示任何陈述。
    for key in ("name", "half_hour"):
        entry = next(
            item
            for item in atomic.list_items(ALICE)
            if item.profile_item_id == ids[key]
        )
        atomic.modify_item(
            ALICE,
            ids[key],
            AtomicProfileItemModifyRequest(
                text=entry.text + "（更新）", version=entry.version
            ),
        )
    rebuilding = overview_service.list_overview(ALICE, now=NOW)
    assert rebuilding.state is ProfileOverviewState.REBUILDING
    assert rebuilding.notice == PROFILE_OVERVIEW_REBUILDING_NOTICE
    assert rebuilding.statements == []


def test_valid_remaining_statements_filters_changed_versions() -> None:
    atomic, overview_service, _, store = _env()
    ids = _seed(atomic)
    current = overview_service.list_overview(ALICE, now=NOW)
    stored = store.read(ALICE)
    assert stored is not None
    items = atomic.list_items(ALICE)
    assert len(valid_remaining_statements(stored, items)) == len(
        current.statements
    )

    edited = [
        item.model_copy(update={"version": item.version + 1})
        if item.profile_item_id == ids["cat"]
        else item
        for item in items
    ]
    remaining = valid_remaining_statements(stored, edited)
    assert ids["cat"] not in {
        segment.profile_item_id
        for statement in remaining
        for segment in statement.segments
    }


def test_account_isolation_and_store_rows_are_per_account() -> None:
    atomic, overview_service, _, store = _env()
    _seed(atomic)
    bob_item = atomic.remember(BOB, "我叫小王", topics=["称呼"])
    assert bob_item is not None

    alice = overview_service.list_overview(ALICE, now=NOW)
    bob = overview_service.list_overview(BOB, now=NOW)
    alice_ids = set(_all_segment_ids(alice))
    bob_ids = set(_all_segment_ids(bob))
    assert bob_item.profile_item_id in bob_ids
    assert bob_item.profile_item_id not in alice_ids
    assert not (alice_ids & bob_ids)
    assert store.read(ALICE) is not None
    assert store.read(BOB) is not None


def test_sqlite_overview_store_epoch_cas(tmp_path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    store = SqliteProfileOverviewStore(database)
    now = datetime.now(UTC)

    assert store.read(ALICE) is None
    store.invalidate(ALICE, reason="remembered", now=now)
    row = store.read(ALICE)
    assert row is not None and row.epoch == 1 and not row.is_current

    assert store.commit_rebuild(
        ALICE,
        captured_epoch=1,
        context_version=PROFILE_OVERVIEW_CONTRACT_VERSION,
        dependency_fingerprint="fp-1",
        payload_json="[]",
        now=now,
    )
    row = store.read(ALICE)
    assert row is not None and row.epoch == 2 and row.is_current

    # 失效后旧世代提交失败：迟到重建不能覆盖新状态。
    store.invalidate(ALICE, reason="edited", now=now)
    assert (
        store.commit_rebuild(
            ALICE,
            captured_epoch=2,
            context_version=PROFILE_OVERVIEW_CONTRACT_VERSION,
            dependency_fingerprint="fp-old",
            payload_json="[]",
            now=now,
        )
        is False
    )
    row = store.read(ALICE)
    assert row is not None and row.dependency_fingerprint == "fp-1"
    database.close()
