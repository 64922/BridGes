"""Issue 18：画像有效期、目标生命周期与语义撤回传播。

覆盖验收：相对时间按来源消息时间锚解析一次、过期不再注入，长期偏好不设
统一 TTL；删除后旧证据、近义普通提及与重放都不复活，明确重新记住保留新
授权来源；编辑换身份后旧值被抑制；暂停/完成/恢复按明确信号执行；撤回按
依赖传播（监听通知与切片版本失效）；LOW 自动/迁移条目不作为确定事实召回，
用户编辑权威独立；墓碑审计不含正文且账户隔离。

内存与 SQLite 两种 adapter 跑同一组场景，保证持久化形态不改变业务结果。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bridges.contracts.atomic_profile import (
    AtomicProfileFactIdentity,
    AtomicProfileFactRelation,
    AtomicProfileGoalState,
    AtomicProfileItem,
    AtomicProfileItemModifyRequest,
    AtomicProfileItemStatus,
    AtomicProfileWriteOrigin,
)
from bridges.contracts.profiles import (
    FourDimension,
    FourDimensionConfidence,
    ProfileSlice,
)
from bridges.profiles.adapters import (
    InMemoryProfileRepository,
    ProfileRepository,
)
from bridges.profiles.atomic import (
    RECALL_LOW_CONFIDENCE_REASON,
    AtomicProfileRepository,
    AtomicProfileService,
    GoalLifecycleKind,
    InMemoryAtomicProfileRepository,
    SqliteAtomicProfileRepository,
    fact_identity_key,
    identity_key,
    parse_fact_identity,
    parse_goal_lifecycle_signal,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileRepository,
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.storage.database import BridgesDatabase

ACCOUNT = "account-alice"
OTHER_ACCOUNT = "account-bob"
#: 可控来源时间锚：2026-01-05 是周一，方便核对「下周」的绝对区间。
ANCHOR = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
EXAM_TEXT = "我下周有考试"
RUN_TEXT = "我喜欢跑步"
JOG_TEXT = "我喜欢慢跑"
GOAL_TEXT = "考研"


@dataclass
class _Stack:
    database: BridgesDatabase | None
    dimensions: FourDimensionProfileRepository
    atomic: AtomicProfileRepository

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
        )
    return _Stack(
        database=None,
        dimensions=InMemoryFourDimensionProfileRepository(),
        atomic=InMemoryAtomicProfileRepository(),
    )


class _Listener:
    """记录撤回传播调用（消息来源与原因）的测试替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], str]] = []

    def on_profile_revocation(
        self, account_id: str, *, message_ids: list[str], reason: str
    ) -> None:
        self.calls.append((account_id, list(message_ids), reason))


class _TransactionalListener:
    """在撤回通知里开自己的事务：若通知仍在事务内发出，SQLite 会嵌套失败。"""

    def __init__(self, repository: AtomicProfileRepository) -> None:
        self._repository = repository
        self.calls: list[tuple[str, list[str], str]] = []
        self.reentered = False

    def on_profile_revocation(
        self, account_id: str, *, message_ids: list[str], reason: str
    ) -> None:
        with self._repository.transaction():
            self.reentered = True
        self.calls.append((account_id, list(message_ids), reason))


class _Harness:
    """原子画像服务 + 四维来源仓库，共用同一份存储。"""

    def __init__(self, stack: _Stack) -> None:
        self._stack = stack
        self.dimensions = FourDimensionProfileService(
            source_repository=_source_repository(),
            repository=stack.dimensions,
        )
        self.repository = stack.atomic
        self.atomic = AtomicProfileService(self.dimensions, stack.atomic)

    def close(self) -> None:
        self._stack.close()

    def mirror(
        self,
        content: str,
        *,
        dimension: FourDimension = FourDimension.KNOWLEDGE_INTEREST,
        message_id: str | None = None,
        source_at: datetime | None = None,
        confidence: FourDimensionConfidence = FourDimensionConfidence.MEDIUM,
        account: str = ACCOUNT,
    ) -> AtomicProfileItem | None:
        record = self.dimensions.upsert_automatic_record(
            account,
            dimension=dimension,
            content=content,
            action="create",
            confidence=confidence,
            evidence_message_id=message_id,
            migration_version="profile-auto-v2",
        )
        return self.atomic.mirror_record(
            account,
            record,
            evidence_message_id=message_id,
            source_at=source_at,
        )

    def item_of(self, text: str, *, account: str = ACCOUNT) -> AtomicProfileItem:
        return next(
            item for item in self.atomic.list_items(account) if item.text == text
        )

    def reload(self, item_id: str, *, account: str = ACCOUNT) -> AtomicProfileItem:
        return self.atomic.get_item(account, item_id)

    def slice(self, *, now: datetime = ANCHOR) -> ProfileSlice:
        return self.atomic.compile_chat_slice(
            ACCOUNT, run_id="run-verify", current_question=None, now=now
        )


def _source_repository() -> ProfileRepository:
    return InMemoryProfileRepository()


@pytest.fixture(params=["memory", "sqlite"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Harness]:
    built = _Harness(_open_stack(request.param, tmp_path / "bridges.db"))
    try:
        yield built
    finally:
        built.close()


def _included_texts(slice_: ProfileSlice) -> set[str]:
    return {item.value_or_rule for item in slice_.included_items}


def _exclusion_reason(slice_: ProfileSlice, assertion_id: str) -> str | None:
    return next(
        (
            item.exclusion_reason
            for item in slice_.unused_items
            if item.assertion_id == assertion_id
        ),
        None,
    )


# ---------------------------------------------------------------------------
# 有效期：来源时间锚、过期过滤与长期偏好
# ---------------------------------------------------------------------------


def test_relative_exam_phrase_anchors_once_and_expires(harness: _Harness) -> None:
    """「下周」按来源消息时间解析成绝对区间，之后不按使用当天重解释。"""

    harness.mirror(EXAM_TEXT, message_id="m-exam", source_at=ANCHOR)
    item = harness.item_of(EXAM_TEXT)
    assert item.validity_phrase == "下周"
    assert item.validity_anchor_at == ANCHOR
    assert item.valid_from == datetime(2026, 1, 12, tzinfo=UTC)
    assert item.valid_until == datetime(
        2026, 1, 18, 23, 59, 59, tzinfo=UTC
    )

    during = harness.slice(now=ANCHOR + timedelta(days=8))
    assert EXAM_TEXT in _included_texts(during)

    # 即便「使用当天」已过去很久，窗口仍是写入时锚定的那一周。
    expired = harness.slice(now=ANCHOR + timedelta(days=20))
    assert EXAM_TEXT not in _included_texts(expired)
    reason = _exclusion_reason(expired, item.profile_item_id)
    assert reason is not None and reason.startswith("已过期")


def test_new_source_message_reanchors_relative_phrase(harness: _Harness) -> None:
    """同一句话再次出现（新来源）时，相对时间按新来源时间重新锚定。"""

    harness.mirror(EXAM_TEXT, message_id="m-exam-1", source_at=ANCHOR)
    later = ANCHOR + timedelta(days=21)
    harness.mirror(EXAM_TEXT, message_id="m-exam-2", source_at=later)
    item = harness.item_of(EXAM_TEXT)
    assert item.validity_anchor_at == later
    assert item.valid_from == datetime(2026, 2, 2, tzinfo=UTC)
    assert item.valid_until == datetime(
        2026, 2, 8, 23, 59, 59, tzinfo=UTC
    )
    assert set(item.source_message_ids) == {"m-exam-1", "m-exam-2"}


def test_long_term_preference_has_no_unified_ttl(harness: _Harness) -> None:
    """没有明示时间的长期偏好不设期限，多年后仍可召回。"""

    harness.mirror(RUN_TEXT, message_id="m-run", source_at=ANCHOR)
    item = harness.item_of(RUN_TEXT)
    assert item.valid_until is None
    much_later = harness.slice(now=ANCHOR + timedelta(days=3650))
    assert RUN_TEXT in _included_texts(much_later)


def test_current_scope_statement_is_not_recalled(harness: _Harness) -> None:
    """只适用于提出时那一轮的说法（「这次」）不作为长期信息召回。"""

    text = "这次考试我想考好"
    harness.mirror(text, message_id="m-now", source_at=ANCHOR)
    item = harness.item_of(text)
    slice_ = harness.slice()
    assert text not in _included_texts(slice_)
    reason = _exclusion_reason(slice_, item.profile_item_id)
    assert reason is not None and reason.startswith("仅适用于提出时的那一轮")


def test_legacy_item_backfills_window_with_its_own_created_at(
    harness: _Harness,
) -> None:
    """升级前写入的条目在迁移时按自身创建时间锚补窗口，不按迁移当天。"""

    text = EXAM_TEXT
    identity = parse_fact_identity(text)
    legacy = AtomicProfileItem(
        profile_item_id="legacy-1",
        owner_account_id=ACCOUNT,
        text=text,
        identity_key=identity_key(ACCOUNT, text),
        fact_subject=identity.subject,
        fact_relation=identity.relation,
        fact_object=identity.object,
        fact_scope=identity.scope,
        fact_key=fact_identity_key(ACCOUNT, identity),
        status=AtomicProfileItemStatus.ACTIVE,
        write_origin=AtomicProfileWriteOrigin.MIGRATION,
        confidence=FourDimensionConfidence.HIGH,
        version=1,
        created_at=ANCHOR,
        updated_at=ANCHOR,
    )
    harness.repository.save_item(legacy)

    harness.atomic.migrate_account(ACCOUNT)

    reloaded = harness.reload("legacy-1")
    assert reloaded.validity_anchor_at == ANCHOR
    assert reloaded.valid_until == datetime(
        2026, 1, 18, 23, 59, 59, tzinfo=UTC
    )


def test_projection_exposes_validity_and_goal_state(harness: _Harness) -> None:
    """页面投影带上明示期限与目标状态，长期偏好为空/active。"""

    harness.mirror(EXAM_TEXT, message_id="m-exam", source_at=ANCHOR)
    harness.mirror(RUN_TEXT, message_id="m-run", source_at=ANCHOR)
    projections = {
        projection.text: projection
        for projection in harness.atomic.projections(ACCOUNT)
    }
    exam = projections[EXAM_TEXT]
    assert exam.validity_phrase == "下周"
    assert exam.valid_until == datetime(2026, 1, 18, 23, 59, 59, tzinfo=UTC)
    assert exam.goal_state is AtomicProfileGoalState.ACTIVE
    run = projections[RUN_TEXT]
    assert run.valid_from is None and run.valid_until is None
    assert run.validity_phrase is None


# ---------------------------------------------------------------------------
# 召回门：LOW 档位与用户编辑权威
# ---------------------------------------------------------------------------


def test_low_confidence_automatic_not_recalled_until_user_edits(
    harness: _Harness,
) -> None:
    """LOW 自动条目不作为确定事实召回；用户编辑后权威独立于档位。"""

    harness.mirror(
        RUN_TEXT,
        message_id="m-low",
        source_at=ANCHOR,
        confidence=FourDimensionConfidence.LOW,
    )
    item = harness.item_of(RUN_TEXT)
    slice_ = harness.slice()
    assert RUN_TEXT not in _included_texts(slice_)
    reason = _exclusion_reason(slice_, item.profile_item_id)
    assert reason == RECALL_LOW_CONFIDENCE_REASON
    assert "可靠程度不足" in reason

    harness.atomic.modify_item(
        ACCOUNT,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(text="我喜欢游泳", version=item.version),
    )
    edited = harness.reload(item.profile_item_id)
    assert edited.confidence is FourDimensionConfidence.LOW
    assert edited.user_edited_at is not None
    after = harness.slice()
    assert "我喜欢游泳" in _included_texts(after)


def test_migration_origin_low_confidence_not_recalled(harness: _Harness) -> None:
    """迁移/镜像降级来源不能绕过使用门被当作确定事实。"""

    text = "我住在南昌"
    identity = AtomicProfileFactIdentity(
        subject="user",
        relation=AtomicProfileFactRelation.STATEMENT,
        object=text,
    )
    slow = AtomicProfileItem(
        profile_item_id="legacy-low",
        owner_account_id=ACCOUNT,
        text=text,
        identity_key=identity_key(ACCOUNT, text),
        fact_subject=identity.subject,
        fact_relation=identity.relation,
        fact_object=identity.object,
        fact_scope=identity.scope,
        fact_key=fact_identity_key(ACCOUNT, identity),
        status=AtomicProfileItemStatus.ACTIVE,
        write_origin=AtomicProfileWriteOrigin.MIGRATION,
        confidence=FourDimensionConfidence.LOW,
        version=1,
        created_at=ANCHOR,
        updated_at=ANCHOR,
    )
    harness.repository.save_item(slow)

    slice_ = harness.slice()
    assert text not in _included_texts(slice_)
    assert _exclusion_reason(slice_, "legacy-low") == RECALL_LOW_CONFIDENCE_REASON


# ---------------------------------------------------------------------------
# 目标生命周期：暂停/完成/恢复
# ---------------------------------------------------------------------------


def _mirror_goal(harness: _Harness, message_id: str = "m-goal") -> AtomicProfileItem:
    harness.mirror(
        GOAL_TEXT,
        dimension=FourDimension.STAGE_GOAL,
        message_id=message_id,
        source_at=ANCHOR,
    )
    return harness.item_of(GOAL_TEXT)


def test_pause_signal_stops_recall_and_resume_restores(harness: _Harness) -> None:
    goal = _mirror_goal(harness)
    assert goal.fact_relation is AtomicProfileFactRelation.GOAL

    paused = harness.atomic.apply_lifecycle_signal(
        ACCOUNT, "暂时不考研", source_message_id="m-pause", source_at=ANCHOR
    )
    assert paused is not None
    assert paused.kind is GoalLifecycleKind.PAUSE
    assert paused.matched_count == 1

    reloaded = harness.reload(goal.profile_item_id)
    assert reloaded.goal_state is AtomicProfileGoalState.PAUSED
    assert reloaded.text == GOAL_TEXT
    assert "m-pause" in reloaded.source_message_ids
    slice_ = harness.slice()
    assert GOAL_TEXT not in _included_texts(slice_)
    reason = _exclusion_reason(slice_, goal.profile_item_id)
    assert reason is not None and reason.startswith("目标已暂停")

    resumed = harness.atomic.apply_lifecycle_signal(
        ACCOUNT, "继续准备", source_message_id="m-resume", source_at=ANCHOR
    )
    assert resumed is not None
    assert resumed.kind is GoalLifecycleKind.RESUME
    assert resumed.matched_count == 1
    reloaded = harness.reload(goal.profile_item_id)
    assert reloaded.goal_state is AtomicProfileGoalState.ACTIVE
    assert GOAL_TEXT in _included_texts(harness.slice())


def test_complete_signal_excludes_goal_and_merge_keeps_state(
    harness: _Harness,
) -> None:
    goal = _mirror_goal(harness, message_id="m-goal-1")
    done = harness.atomic.apply_lifecycle_signal(
        ACCOUNT, "考完了", source_message_id="m-done", source_at=ANCHOR
    )
    assert done is not None
    assert done.kind is GoalLifecycleKind.COMPLETE
    assert done.matched_count == 1

    reloaded = harness.reload(goal.profile_item_id)
    assert reloaded.goal_state is AtomicProfileGoalState.COMPLETED
    slice_ = harness.slice()
    assert GOAL_TEXT not in _included_texts(slice_)
    reason = _exclusion_reason(slice_, goal.profile_item_id)
    assert reason is not None and reason.startswith("目标已完成")

    # 普通补证据不会把已完成目标重置回活动状态。
    _mirror_goal(harness, message_id="m-goal-2")
    again = harness.reload(goal.profile_item_id)
    assert again.goal_state is AtomicProfileGoalState.COMPLETED


def test_remember_with_lifecycle_signal_pauses_existing_goal(
    harness: _Harness,
) -> None:
    """显式「记住暂时不考研」走生命周期对账，不写成新事实。"""

    goal = _mirror_goal(harness)
    remembered = harness.atomic.remember(
        ACCOUNT, "暂时不考研", source_message_id="m-user-pause"
    )
    assert remembered.profile_item_id == goal.profile_item_id
    assert remembered.goal_state is AtomicProfileGoalState.PAUSED
    assert "m-user-pause" in remembered.source_message_ids


@pytest.mark.parametrize(
    "text",
    [
        "我已经不想继续准备考研了",
        "别继续考研了",
        "我还没考完",
        "不暂停考研",
        "不再准备考研",
    ],
)
def test_negated_lifecycle_phrases_are_not_signals(text: str) -> None:
    """否定式表述不是变更声明：不想继续/别继续/没考完都不改目标状态。"""

    assert parse_goal_lifecycle_signal(text) is None


@pytest.mark.parametrize("text", ["继续准备", "考完了", "暂时不考研"])
def test_affirmative_lifecycle_phrases_still_parse(text: str) -> None:
    assert parse_goal_lifecycle_signal(text) is not None


def test_pause_targets_only_named_goal(harness: _Harness) -> None:
    """「暂时不考研」只暂停考研：考公与考研共用「考」字不得被误伤。"""

    kaoyan = _mirror_goal(harness)
    harness.mirror(
        "考公",
        dimension=FourDimension.STAGE_GOAL,
        message_id="m-goal-2",
        source_at=ANCHOR,
    )
    kaogong = harness.item_of("考公")

    outcome = harness.atomic.apply_lifecycle_signal(
        ACCOUNT, "暂时不考研", source_message_id="m-pause", source_at=ANCHOR
    )
    assert outcome is not None
    assert outcome.matched_count == 1
    assert (
        harness.reload(kaoyan.profile_item_id).goal_state
        is AtomicProfileGoalState.PAUSED
    )
    assert (
        harness.reload(kaogong.profile_item_id).goal_state
        is AtomicProfileGoalState.ACTIVE
    )
    assert "考公" in _included_texts(harness.slice())


def test_goal_suppression_keeps_distinct_goal_writable(harness: _Harness) -> None:
    """忘掉考研后，考公（共用「考」字）仍应正常写入，不算近义复活。"""

    _mirror_goal(harness)
    harness.atomic.forget(ACCOUNT, GOAL_TEXT)
    written = harness.mirror(
        "考公",
        dimension=FourDimension.STAGE_GOAL,
        message_id="m-goal-2",
        source_at=ANCHOR,
    )
    assert written is not None
    assert written.fact_object == "考公"


def test_lifecycle_keyword_without_target_is_written_as_fact(
    harness: _Harness,
) -> None:
    """「作业搞定了」没有可对账目标：按普通事实写入，不动无关目标。"""

    goal = _mirror_goal(harness)
    item = harness.mirror("作业搞定了", message_id="m-homework", source_at=ANCHOR)
    assert item is not None
    assert item.text == "作业搞定了"
    assert (
        harness.reload(goal.profile_item_id).goal_state
        is AtomicProfileGoalState.ACTIVE
    )


# ---------------------------------------------------------------------------
# 语义撤回：删除/编辑后的重放与近义提及
# ---------------------------------------------------------------------------


def test_delete_then_replay_and_near_synonym_stay_revoked(
    harness: _Harness,
) -> None:
    record = harness.dimensions.upsert_automatic_record(
        ACCOUNT,
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content=RUN_TEXT,
        action="create",
        confidence=FourDimensionConfidence.MEDIUM,
        evidence_message_id="m-run",
        migration_version="profile-auto-v2",
    )
    item = harness.atomic.mirror_record(
        ACCOUNT, record, evidence_message_id="m-run", source_at=ANCHOR
    )
    assert item is not None
    harness.atomic.delete_item(ACCOUNT, item.profile_item_id, item.version)

    # 旧证据重放不复活。
    assert (
        harness.atomic.mirror_record(
            ACCOUNT, record, evidence_message_id="m-run", source_at=ANCHOR
        )
        is None
    )
    # 普通近义提及（未明确重新记住）暂缓写入。
    assert harness.mirror(JOG_TEXT, message_id="m-jog", source_at=ANCHOR) is None
    assert _included_texts(harness.slice()) == set()

    # 明确重新记住：解除抑制、保留新授权来源。
    restored = harness.atomic.remember(
        ACCOUNT, JOG_TEXT, source_message_id="m-user-remember"
    )
    assert restored.write_origin is AtomicProfileWriteOrigin.USER
    assert restored.source_message_ids == ["m-user-remember"]
    assert JOG_TEXT in _included_texts(harness.slice())


def test_edit_identity_change_suppresses_old_value(harness: _Harness) -> None:
    record = harness.dimensions.upsert_automatic_record(
        ACCOUNT,
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content=RUN_TEXT,
        action="create",
        confidence=FourDimensionConfidence.MEDIUM,
        evidence_message_id="m-run",
        migration_version="profile-auto-v2",
    )
    item = harness.atomic.mirror_record(
        ACCOUNT, record, evidence_message_id="m-run", source_at=ANCHOR
    )
    assert item is not None
    harness.atomic.modify_item(
        ACCOUNT,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(text="我喜欢游泳", version=item.version),
    )

    # 旧证据重放只回到用户版本，不把旧值写回来；近义普通提及暂缓写入。
    replayed = harness.atomic.mirror_record(
        ACCOUNT, record, evidence_message_id="m-run", source_at=ANCHOR
    )
    assert replayed is not None
    assert replayed.profile_item_id == item.profile_item_id
    assert harness.mirror(JOG_TEXT, message_id="m-jog", source_at=ANCHOR) is None
    texts = _included_texts(harness.slice())
    assert "我喜欢游泳" in texts
    assert RUN_TEXT not in texts
    assert JOG_TEXT not in texts


# ---------------------------------------------------------------------------
# 撤回传播：监听通知、切片版本与墓碑审计
# ---------------------------------------------------------------------------


def test_revocation_listener_receives_sources_by_reason(harness: _Harness) -> None:
    listener = _Listener()
    harness.atomic.set_revocation_listener(listener)

    harness.mirror(RUN_TEXT, message_id="m-run", source_at=ANCHOR)
    run_item = harness.item_of(RUN_TEXT)
    harness.atomic.delete_item(ACCOUNT, run_item.profile_item_id, run_item.version)
    assert listener.calls[-1] == (ACCOUNT, ["m-run"], "deleted")

    harness.mirror(
        GOAL_TEXT,
        dimension=FourDimension.STAGE_GOAL,
        message_id="m-goal",
        source_at=ANCHOR,
    )
    result = harness.atomic.forget(ACCOUNT, GOAL_TEXT)
    assert result.kind.value == "forget"
    assert listener.calls[-1] == (ACCOUNT, ["m-goal"], "forgotten")

    location = "我住在南昌"
    harness.mirror(location, message_id="m-home", source_at=ANCHOR)
    home = harness.item_of(location)
    harness.atomic.modify_item(
        ACCOUNT,
        home.profile_item_id,
        AtomicProfileItemModifyRequest(text="我住在杭州", version=home.version),
    )
    assert listener.calls[-1] == (ACCOUNT, ["m-home"], "edited")


def test_revocation_notification_is_sent_after_commit(harness: _Harness) -> None:
    """监听方在通知里开事务不得嵌套失败：替代与编辑的撤回都在提交后发出。"""

    listener = _TransactionalListener(harness.repository)
    harness.atomic.set_revocation_listener(listener)

    harness.mirror(
        GOAL_TEXT,
        dimension=FourDimension.STAGE_GOAL,
        message_id="m-goal",
        source_at=ANCHOR,
    )
    harness.mirror(
        "我不考研了，转为准备就业",
        dimension=FourDimension.STAGE_GOAL,
        message_id="m-change",
        source_at=ANCHOR,
    )
    assert listener.reentered
    assert listener.calls[-1] == (ACCOUNT, ["m-goal"], "superseded")

    listener.calls.clear()
    listener.reentered = False
    replacement = harness.item_of("我不考研了，转为准备就业")
    harness.atomic.modify_item(
        ACCOUNT,
        replacement.profile_item_id,
        AtomicProfileItemModifyRequest(
            text="我准备直接就业", version=replacement.version
        ),
    )
    assert listener.reentered
    assert listener.calls[-1] == (ACCOUNT, ["m-change"], "edited")


def test_revocation_version_invalidates_compiled_slice(harness: _Harness) -> None:
    harness.mirror(RUN_TEXT, message_id="m-run", source_at=ANCHOR)
    slice_ = harness.slice()
    assert slice_.revocation_version is not None
    assert harness.atomic.is_slice_current(ACCOUNT, slice_.revocation_version)

    item = harness.item_of(RUN_TEXT)
    harness.atomic.delete_item(ACCOUNT, item.profile_item_id, item.version)

    assert not harness.atomic.is_slice_current(ACCOUNT, slice_.revocation_version)
    assert not harness.atomic.is_slice_current(OTHER_ACCOUNT, slice_.revocation_version)


def test_tombstone_audit_is_text_free_and_account_scoped(harness: _Harness) -> None:
    harness.mirror(RUN_TEXT, message_id="m-run", source_at=ANCHOR)
    item = harness.item_of(RUN_TEXT)
    harness.atomic.delete_item(ACCOUNT, item.profile_item_id, item.version)

    audit = harness.atomic.tombstone_audit(ACCOUNT)
    entry = next(
        one for one in audit if one.profile_item_id == item.profile_item_id
    )
    assert entry.status is AtomicProfileItemStatus.WITHDRAWN
    assert entry.fact_relation is AtomicProfileFactRelation.INTEREST
    assert entry.has_fact_suppression
    assert entry.source_message_ids == ["m-run"]
    assert not hasattr(entry, "text")

    assert harness.atomic.tombstone_audit(OTHER_ACCOUNT) == []


def test_atomic_profile_is_account_scoped(harness: _Harness) -> None:
    harness.mirror(RUN_TEXT, message_id="m-run", source_at=ANCHOR)
    other = harness.atomic.compile_chat_slice(
        OTHER_ACCOUNT, run_id="run-other", current_question=None, now=ANCHOR
    )
    assert _included_texts(other) == set()
    assert harness.atomic.list_items(OTHER_ACCOUNT) == []

