"""画像工单 13：进行中事项接续、暂停/完成与近期工作上下文守卫。

覆盖验收：
- 新会话接续正确事项及进展（目标 + 同组进展/待续问题按 ``ongoing`` 依据
  进入编译，不要求重复背景）；
- 并行事项独立，暂停/完成/恢复只作用于精确目标；
- 待续问题来自真实条目与来源消息，不新增摘要句子；
- 删除依赖事实、修改来源消息、关使用让上下文及时失效；迟到重建不能覆盖
  新状态；领域引用只存引用、状态实时解析（未接通不伪造）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridges.contracts.atomic_profile import (
    AtomicProfileDomainReference,
    AtomicProfileGoalState,
    AtomicProfileItemModifyRequest,
)
from bridges.contracts.ongoing_profile import (
    ONGOING_CONTEXT_CONTRACT_VERSION,
    OngoingContextState,
    OngoingDomainReferenceState,
    OngoingGoalAction,
    OngoingItemRole,
)
from bridges.contracts.profile_extraction import ProfileAccountControlsProjection
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileError,
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    ProfileSourceMessage,
)
from bridges.profiles.commit import ProfileFactSubmission
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.ongoing import (
    ONGOING_USAGE_DISABLED_NOTICE,
    OngoingItemsService,
)
from bridges.profiles.ongoing_repository import (
    InMemoryOngoingContextStore,
    SqliteOngoingContextStore,
)
from bridges.profiles.purpose import build_purpose
from bridges.storage.database import BridgesDatabase

ALICE = "account-alice"
BOB = "account-bob"
NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)

GOAL_TEXT = "我在准备考研英语"
PROGRESS_TEXT = "考研英语的阅读已经刷完一轮"
PENDING_TEXT = "考研英语的冲刺班还没决定报不报"


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
    messages: dict[str, ProfileSourceMessage] | None = None,
    store: InMemoryOngoingContextStore | None = None,
) -> tuple[AtomicProfileService, OngoingItemsService, _Controls]:
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    atomic = AtomicProfileService(four, InMemoryAtomicProfileRepository())
    controls = _Controls()
    ongoing = OngoingItemsService(
        atomic=atomic,
        context_store=store or InMemoryOngoingContextStore(),
        controls_reader=lambda account_id: controls.projection(),
        message_reader=(
            (lambda account_id, message_id: messages.get(message_id))
            if messages is not None
            else None
        ),
    )
    atomic.set_ongoing_context_listener(ongoing)
    atomic.set_ongoing_context_provider(ongoing)
    return atomic, ongoing, controls


def _message(message_id: str, content: str) -> ProfileSourceMessage:
    return ProfileSourceMessage(
        message_id=message_id,
        conversation_id="conversation-1",
        role="user",
        status="sent",
        content=content,
        created_at=NOW,
    )


def _seed_matter(atomic: AtomicProfileService) -> tuple[str, str, str]:
    goal = atomic.remember(ALICE, GOAL_TEXT, source_message_id="msg-goal")
    progress = atomic.commit_fact(
        ALICE,
        ProfileFactSubmission(text=PROGRESS_TEXT, evidence_message_id="msg-progress"),
    )
    pending = atomic.commit_fact(
        ALICE,
        ProfileFactSubmission(text=PENDING_TEXT, evidence_message_id="msg-pending"),
    )
    assert progress is not None and pending is not None
    assert progress.related_goal_item_id == goal.profile_item_id
    assert pending.related_goal_item_id == goal.profile_item_id
    return goal.profile_item_id, progress.profile_item_id, pending.profile_item_id


def test_projection_groups_goal_progress_and_pending_without_inventing_texts() -> None:
    """目标、进展与待续问题按角色成组；展示的是条目原文与真实来源。"""

    atomic, ongoing, _ = _env()
    goal_id, _, _ = _seed_matter(atomic)

    projection = ongoing.list_items(ALICE)

    assert projection.usage_enabled is True
    assert projection.notice is None
    assert len(projection.items) == 1
    item = projection.items[0]
    assert item.goal.profile_item_id == goal_id
    assert item.goal.text == GOAL_TEXT
    assert item.goal.goal_state is AtomicProfileGoalState.ACTIVE
    assert [entry.text for entry in item.progress] == [PROGRESS_TEXT]
    assert [entry.role for entry in item.progress] == [OngoingItemRole.PROGRESS]
    assert [entry.text for entry in item.pending_questions] == [PENDING_TEXT]
    assert [entry.role for entry in item.pending_questions] == [
        OngoingItemRole.PENDING_QUESTION
    ]
    # 待续问题带回真实交流依据（来源消息标识），供页面展开核对。
    assert item.pending_questions[0].source_message_ids == ["msg-pending"]
    # 领域引用未接通前如实为空，不伪造领域状态。
    assert item.domain_references == []


def test_continuation_compile_adopts_whole_matter_with_ongoing_basis() -> None:
    """「继续上次的考研英语准备」按 ongoing 依据采用目标与同组条目。"""

    atomic, ongoing, _ = _env()
    _seed_matter(atomic)

    group = ongoing.continuation_group(ALICE, query="继续上次的考研英语准备。")

    assert group is not None
    assert group.goal_item_id is not None
    assert len(group.entry_item_ids) == 2
    adopted = atomic.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="继续上次的考研英语准备。"),
        run_id="run-b",
    )
    by_text = {entry.fact_text: entry for entry in adopted.adopted_items}
    for text in (GOAL_TEXT, PROGRESS_TEXT, PENDING_TEXT):
        assert text in by_text
        assert "ongoing" in (by_text[text].recall_basis or "").split("+")


def test_continuation_is_withheld_when_ambiguous_or_unrelated() -> None:
    """并行事项同时命中或问题无指向时不接续，不按主题批量归属。"""

    atomic, ongoing, _ = _env()
    _seed_matter(atomic)
    atomic.remember(ALICE, "我在准备计算机三级", source_message_id="msg-second")

    both = ongoing.continuation_group(ALICE, query="考研英语和计算机三级怎么安排？")
    unrelated = ongoing.continuation_group(ALICE, query="今天天气怎么样？")
    semantic = ongoing.continuation_group(
        ALICE, query="", semantic_item_ids=["not-an-item"]
    )

    assert both is None
    assert unrelated is None
    assert semantic is None


def test_semantic_hit_on_entry_continues_its_matter() -> None:
    """语义命中同组条目（而非目标正文）时仍能接续该事项。"""

    atomic, ongoing, _ = _env()
    goal_id, progress_id, _ = _seed_matter(atomic)

    group = ongoing.continuation_group(
        ALICE, query="", semantic_item_ids=[progress_id]
    )

    assert group is not None
    assert group.goal_item_id == goal_id
    assert progress_id in group.entry_item_ids


def test_pause_resume_and_complete_touch_only_the_exact_goal() -> None:
    """并行事项独立：暂停/恢复/完成精确到条目，不影响另一目标。"""

    atomic, ongoing, _ = _env()
    goal_id, _, _ = _seed_matter(atomic)
    other = atomic.remember(ALICE, "我在准备计算机三级", source_message_id="msg-second")

    paused = ongoing.set_state(
        ALICE, goal_id, OngoingGoalAction.PAUSE, version=atomic.get_item(ALICE, goal_id).version
    )
    assert paused.goal_state is AtomicProfileGoalState.PAUSED
    assert atomic.get_item(ALICE, other.profile_item_id).goal_state is (
        AtomicProfileGoalState.ACTIVE
    )
    # 暂停的目标不再作为当前目标接续；页面仍列出供管理。
    assert ongoing.continuation_group(ALICE, query="继续考研英语") is None
    statuses = {
        item.goal.profile_item_id: item.goal.goal_state
        for item in ongoing.list_items(ALICE).items
    }
    assert statuses[goal_id] is AtomicProfileGoalState.PAUSED
    assert statuses[other.profile_item_id] is AtomicProfileGoalState.ACTIVE

    resumed = ongoing.set_state(
        ALICE, goal_id, OngoingGoalAction.RESUME, version=paused.version
    )
    assert resumed.goal_state is AtomicProfileGoalState.ACTIVE
    assert ongoing.continuation_group(ALICE, query="继续考研英语") is not None

    with pytest.raises(AtomicProfileError, match="版本冲突"):
        ongoing.set_state(
            ALICE, goal_id, OngoingGoalAction.COMPLETE, version=resumed.version + 3
        )

    completed = ongoing.set_state(
        ALICE, goal_id, OngoingGoalAction.COMPLETE, version=resumed.version
    )
    assert completed.goal_state is AtomicProfileGoalState.COMPLETED
    remaining = {item.goal.profile_item_id for item in ongoing.list_items(ALICE).items}
    assert remaining == {other.profile_item_id}
    # 完成只让事项区隐藏；同组事实仍保留在记忆里。
    kept = {item.profile_item_id for item in atomic.list_items(ALICE)}
    assert goal_id in kept and other.profile_item_id in kept


def test_domain_reference_stores_reference_and_resolves_live() -> None:
    """领域引用只存引用；未接通/解析失败如实未解析，接通后实时解析。"""

    atomic, ongoing, _ = _env()
    goal_id, _, _ = _seed_matter(atomic)
    reference = AtomicProfileDomainReference(domain="learning", reference_id="lp-1")

    goal = atomic.get_item(ALICE, goal_id)
    updated = ongoing.set_domain_reference(
        ALICE, goal_id, reference, version=goal.version
    )
    unresolved = ongoing.list_items(ALICE).items[0].domain_references
    assert len(unresolved) == 1
    assert unresolved[0].resolved is False
    assert unresolved[0].display is None

    class _Provider:
        def resolve(self, account_id: str, ref: AtomicProfileDomainReference) -> (
            OngoingDomainReferenceState | None
        ):
            if account_id != ALICE:
                raise AssertionError("跨账户解析请求")
            return OngoingDomainReferenceState(display="考研英语学习项", state_code="active")

    ongoing.register_domain_provider("learning", _Provider())
    resolved = ongoing.list_items(ALICE).items[0].domain_references[0]
    assert resolved.resolved is True
    assert resolved.display == "考研英语学习项"
    assert resolved.state_code == "active"

    class _Failing:
        def resolve(self, account_id: str, ref: AtomicProfileDomainReference) -> None:
            raise RuntimeError("领域暂不可用")

    ongoing.register_domain_provider("learning", _Failing())
    degraded = ongoing.list_items(ALICE).items[0].domain_references[0]
    assert degraded.resolved is False and degraded.display is None

    cleared = ongoing.set_domain_reference(
        ALICE, goal_id, None, version=updated.version
    )
    assert cleared.domain_reference is None
    assert ongoing.list_items(ALICE).items[0].domain_references == []


def test_context_invalidates_on_edit_delete_and_control_change() -> None:
    """条目修改、目标删除、关使用都让上下文失效；重新读取给出新版本。"""

    atomic, ongoing, controls = _env()
    goal_id, progress_id, _ = _seed_matter(atomic)

    first = ongoing.read_context(ALICE)
    assert first.state is OngoingContextState.CURRENT
    assert first.version >= 1
    assert first.dependency_fingerprint
    assert len(first.items) == 1

    progress = atomic.get_item(ALICE, progress_id)
    atomic.modify_item(
        ALICE,
        progress_id,
        AtomicProfileItemModifyRequest(
            text="考研英语的阅读已经刷完两轮", version=progress.version
        ),
    )

    after_edit = ongoing.read_context(ALICE)
    assert after_edit.state is OngoingContextState.CURRENT
    assert after_edit.version > first.version
    assert after_edit.dependency_fingerprint != first.dependency_fingerprint
    assert after_edit.items[0].progress[0].text == "考研英语的阅读已经刷完两轮"

    delete_version = atomic.get_item(ALICE, goal_id).version
    atomic.delete_item(ALICE, goal_id, delete_version)
    after_delete = ongoing.read_context(ALICE)
    assert after_delete.state is OngoingContextState.CURRENT
    assert after_delete.items == []
    assert after_delete.dependency_fingerprint != after_edit.dependency_fingerprint

    controls.usage_enabled = False
    off = ongoing.read_context(ALICE)
    assert off.state is OngoingContextState.REBUILDING
    assert off.items == []
    assert ongoing.continuation_group(ALICE, query="继续考研英语") is None
    projection = ongoing.list_items(ALICE)
    assert projection.usage_enabled is False
    assert projection.notice == ONGOING_USAGE_DISABLED_NOTICE

    controls.usage_enabled = True
    assert ongoing.read_context(ALICE).state is OngoingContextState.CURRENT


def test_source_message_change_invalidates_context() -> None:
    """来源消息删除或内容变化让上下文失效并在下次读取时重建。"""

    messages = {
        "msg-goal": _message("msg-goal", "我在准备考研英语"),
        "msg-progress": _message("msg-progress", PROGRESS_TEXT),
        "msg-pending": _message("msg-pending", PENDING_TEXT),
    }
    atomic, ongoing, _ = _env(messages=messages)
    _seed_matter(atomic)

    first = ongoing.read_context(ALICE)
    assert first.state is OngoingContextState.CURRENT

    messages["msg-pending"] = _message("msg-pending", "考研英语的冲刺班还没决定报不报。")
    after_change = ongoing.read_context(ALICE)
    assert after_change.state is OngoingContextState.CURRENT
    assert after_change.dependency_fingerprint != first.dependency_fingerprint

    del messages["msg-progress"]
    after_delete = ongoing.read_context(ALICE)
    assert after_delete.state is OngoingContextState.CURRENT
    assert after_delete.dependency_fingerprint != after_change.dependency_fingerprint
    assert after_delete.items and len(after_delete.items[0].progress) == 1


def test_late_rebuild_cannot_overwrite_new_state() -> None:
    """迟到重建的世代 CAS 必须失败；读路径随后给出反映新状态的上下文。"""

    atomic, ongoing, _ = _env()
    _seed_matter(atomic)
    first = ongoing.read_context(ALICE)
    stored, snapshot = ongoing.begin_context_rebuild(ALICE)
    assert stored is not None

    atomic.remember(ALICE, "我在准备雅思考试", source_message_id="msg-new")
    assert ongoing.commit_context_rebuild(ALICE, stored, snapshot) is False
    assert ongoing._store.read(ALICE).epoch > stored.epoch  # type: ignore[attr-defined]

    current = ongoing.read_context(ALICE)
    assert current.state is OngoingContextState.CURRENT
    assert current.version > first.version
    goals = {item.goal.text for item in current.items}
    assert "我在准备雅思考试" in goals and GOAL_TEXT in goals

    # 两个并发重建：先提交者生效，后到的陈旧世代被拒绝。
    ticket_a = ongoing.begin_context_rebuild(ALICE)
    ticket_b = ongoing.begin_context_rebuild(ALICE)
    assert ongoing.commit_context_rebuild(ALICE, *ticket_a) is True
    assert ongoing.commit_context_rebuild(ALICE, *ticket_b) is False


def test_accounts_are_isolated() -> None:
    """事项投影与上下文按账户隔离：其他账户没有内容也没有接续。"""

    atomic, ongoing, _ = _env()
    _seed_matter(atomic)
    ongoing.read_context(ALICE)

    assert ongoing.list_items(BOB).items == []
    assert ongoing.read_context(BOB).state is OngoingContextState.EMPTY
    assert ongoing.continuation_group(BOB, query="继续上次的考研英语准备") is None
    with pytest.raises(AtomicProfileError):
        ongoing.set_state(BOB, "not-mine", OngoingGoalAction.PAUSE, version=1)


def test_sqlite_context_store_epoch_cas(tmp_path: Path) -> None:
    """SQLite 上下文存储：失效递增世代，重建以捕获世代做 CAS。"""

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    store = SqliteOngoingContextStore(database)
    moment = datetime.now(UTC)

    assert store.read(ALICE) is None
    store.invalidate(ALICE, reason="seed", now=moment)
    row = store.read(ALICE)
    assert row is not None and row.epoch == 1 and not row.is_current

    assert store.commit_rebuild(
        ALICE,
        captured_epoch=1,
        context_version=ONGOING_CONTEXT_CONTRACT_VERSION,
        dependency_fingerprint="fp-1",
        payload_json="[]",
        now=moment,
    )
    current = store.read(ALICE)
    assert current is not None and current.is_current and current.epoch == 2
    assert current.dependency_fingerprint == "fp-1"

    assert not store.commit_rebuild(
        ALICE,
        captured_epoch=1,
        context_version=ONGOING_CONTEXT_CONTRACT_VERSION,
        dependency_fingerprint="late",
        payload_json="[]",
        now=moment,
    )
    store.invalidate(ALICE, reason="edited", now=moment)
    invalidated = store.read(ALICE)
    assert invalidated is not None and invalidated.epoch == 3
    assert invalidated.invalidated_reason == "edited"
    assert not invalidated.is_current
    # 失效行绝不冒充当前内容：读取方必须重建后再用。
    assert invalidated.payload_json == "[]"


def test_migration_87_upgrades_existing_database(tmp_path: Path) -> None:
    """迁移 87：既有库补两列并建上下文表；结构校验失败关闭。"""

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    connection = database.connection
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(profile_items)")
    }
    assert {"related_goal_item_id", "domain_reference_json"} <= columns
    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name='profile_ongoing_context'"
        ).fetchone()
        is not None
    )

    # 模拟 86 库：去掉 87 新增结构后重新初始化必须补回。
    connection.execute("ALTER TABLE profile_items DROP COLUMN related_goal_item_id")
    connection.execute("ALTER TABLE profile_items DROP COLUMN domain_reference_json")
    connection.execute("DROP TABLE profile_ongoing_context")
    connection.execute("UPDATE schema_meta SET value = '86' WHERE key = 'version'")
    database.initialize()

    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(profile_items)")
    }
    assert {"related_goal_item_id", "domain_reference_json"} <= columns
    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name='profile_ongoing_context'"
        ).fetchone()
        is not None
    )
