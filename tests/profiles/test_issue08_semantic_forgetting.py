"""画像工单 08：忘掉与关闭个性化后的间接回流阻断（本票新增路径）。

覆盖验收：本轮不用是只影响当前回合的显式控制指令（登记为终态运行、不进入
自动抽取、重放幂等）；明确恢复只恢复指定范围，其他墓碑继续有效；记住/忘掉/
删除的审计只给原因与引用、不含正文；账号删除后迟到 worker（入口、提交前复核
与终结记账三处）不重建任何携带来源快照的行。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.contracts.atomic_profile import AtomicProfileMemoryStatus
from bridges.contracts.observability import AuditAction
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutcome,
    ProfileExtractionOutput,
    ProfileExtractionSource,
    ProfileExtractionStatus,
)
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
from bridges.lifecycle.catalog import delete_account_rows
from bridges.observability.service import ObservabilityService
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    SqliteAtomicProfileRepository,
)
from bridges.profiles.automatic import (
    AutomaticProfileService,
    InMemoryAutomaticProfileRepository,
    SqliteAutomaticProfileRepository,
    parse_turn_profile_directive,
)
from bridges.profiles.commit import ProfileFactSubmission
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.storage.database import BridgesDatabase

ACCOUNT = "account-alice"
OTHER_ACCOUNT = "account-bob"
TURN_DIRECTIVE = "这轮先不用我的画像，帮我安排复习计划"


def _memory_services() -> tuple[AtomicProfileService, AutomaticProfileService]:
    four = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )
    atomic = AtomicProfileService(four, InMemoryAtomicProfileRepository())
    automatic = AutomaticProfileService(
        four_dimension_service=four,
        repository=InMemoryAutomaticProfileRepository(),
        atomic_profile_service=atomic,
    )
    return atomic, automatic


def _fact(text: str) -> ProfileFactSubmission:
    return ProfileFactSubmission(
        text=text,
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        confidence=FourDimensionConfidence.MEDIUM,
        evidence_message_id="m-replay",
    )


class _AlwaysFailingExtractor:
    """每次调用都按可重试失败：留下 PENDING 任务与运行记账。"""

    version = "issue08-fail-v1"

    def extract(self, *, message_id: str, **_ignored: object) -> ProfileExtractionOutput:
        raise RuntimeError("网关暂时不可用")


def _sqlite_stack(
    tmp_path: Path,
    *,
    extractor: Any = None,
) -> tuple[
    BridgesDatabase,
    AtomicProfileService,
    SqliteAutomaticProfileRepository,
    AutomaticProfileService,
]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    four = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=SqliteFourDimensionProfileRepository(database),
    )
    atomic = AtomicProfileService(four, SqliteAtomicProfileRepository(database))
    state = SqliteAutomaticProfileRepository(database)
    automatic = AutomaticProfileService(
        four_dimension_service=four,
        repository=state,
        extractor=extractor,
        atomic_profile_service=atomic,
    )
    return database, atomic, state, automatic


def _mark_account_deleted(database: BridgesDatabase, account_id: str) -> None:
    """模拟删除编排的第一步：先落账户删除记录，再删数据行。"""

    with database.transaction():
        database.connection.execute(
            "INSERT INTO account_deletions(deletion_id, account_id, status,"
            " retry_count, last_error, pending_hashes, started_at)"
            " VALUES (?, ?, 'deleting', 0, NULL, '[]', ?)",
            (f"deletion-{account_id}", account_id, datetime.now(UTC).isoformat()),
        )


# ---------------------------------------------------------------------------
# 本轮不用：确定性识别与终态登记
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "这轮先不用我的画像",
        "这次不用画像",
        "本轮先不用记忆",
        "这一回不要用长期信息",
        "本次别参考个人信息",
    ],
)
def test_turn_directive_recognized(content: str) -> None:
    assert parse_turn_profile_directive(content)


@pytest.mark.parametrize(
    "content",
    [
        "这次不用复习了",
        "帮我记住我的画像偏好是详细回答",
        "我这次不用参考旧答案了",
        "下一轮再用画像",
    ],
)
def test_non_directive_text_not_recognized(content: str) -> None:
    assert not parse_turn_profile_directive(content)


def test_turn_control_registers_terminal_run_without_extraction() -> None:
    """控制消息登记为本地规则终态结论：不抽取、不写条目、重放幂等。"""

    atomic, automatic = _memory_services()
    result = automatic.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="m-turn",
        content=TURN_DIRECTIVE,
        run_id="run-turn",
        mode="companion",
    )
    assert result.run.outcome is ProfileExtractionOutcome.SUCCEEDED_TURN_CONTROL
    assert result.run.status is ProfileExtractionStatus.SUCCEEDED
    assert result.run.source is ProfileExtractionSource.LOCAL_RULE
    assert atomic.list_items(ACCOUNT) == []

    replay = automatic.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="m-turn",
        content=TURN_DIRECTIVE,
        run_id="run-turn",
        mode="companion",
    )
    assert replay.run.extraction_id == result.run.extraction_id
    assert replay.run.outcome is ProfileExtractionOutcome.SUCCEEDED_TURN_CONTROL
    assert atomic.list_items(ACCOUNT) == []

    # 控制只终结这一条消息：后续普通自述照常进入自动抽取。
    automatic.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="m-normal",
        content="我喜欢Python",
        run_id="run-normal",
        mode="companion",
    )
    assert [item.text for item in atomic.list_items(ACCOUNT)] != []


def test_turn_control_is_active_management_while_recording_stopped() -> None:
    """停止记录期间控制指令仍即时识别；不删除信息，也不改变记录开关。"""

    atomic, automatic = _memory_services()
    atomic.remember(ACCOUNT, "我喜欢跑步")
    automatic.set_account_controls(ACCOUNT, recording_enabled=False)

    result = automatic.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="m-turn",
        content=TURN_DIRECTIVE,
        run_id="run-turn",
        mode="companion",
    )
    assert result.run.outcome is ProfileExtractionOutcome.SUCCEEDED_TURN_CONTROL
    assert [item.text for item in atomic.list_items(ACCOUNT)] == ["我喜欢跑步"]
    assert (
        automatic.account_controls(ACCOUNT).recording_enabled is False
    ), "控制指令不改账户级开关"


# ---------------------------------------------------------------------------
# 恢复限定范围
# ---------------------------------------------------------------------------


def test_remember_restores_only_specified_scope() -> None:
    """明确重新记住只恢复指定条目；其他墓碑照旧拦截来源重放。"""

    atomic, _ = _memory_services()
    run_item = atomic.remember(ACCOUNT, "我喜欢跑步")
    home_item = atomic.remember(ACCOUNT, "我住在南昌")
    atomic.delete_item(ACCOUNT, run_item.profile_item_id, run_item.version)
    atomic.delete_item(ACCOUNT, home_item.profile_item_id, home_item.version)
    assert atomic.list_items(ACCOUNT) == []

    restored = atomic.remember(ACCOUNT, "我喜欢跑步", source_message_id="m-restore")
    assert restored.write_origin.value == "user"
    assert [item.text for item in atomic.list_items(ACCOUNT)] == ["我喜欢跑步"]

    # 未指定恢复的墓碑继续有效：旧来源重放不复活。
    assert atomic.commit_fact(ACCOUNT, _fact("我住在南昌")) is None
    assert [item.text for item in atomic.list_items(ACCOUNT)] == ["我喜欢跑步"]
    audit = atomic.tombstone_audit(ACCOUNT)
    assert any(entry.profile_item_id == home_item.profile_item_id for entry in audit)


# ---------------------------------------------------------------------------
# 审计：原因与引用，不含正文
# ---------------------------------------------------------------------------


def test_remember_forget_delete_audit_is_text_free() -> None:
    observability = ObservabilityService()
    four = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )
    atomic = AtomicProfileService(
        four,
        InMemoryAtomicProfileRepository(),
        observability_service=observability,
    )

    remembered = atomic.remember(ACCOUNT, "我喜欢跑步", source_message_id="m-remember")
    forgotten = atomic.forget(ACCOUNT, "我喜欢跑步")
    assert forgotten.status is AtomicProfileMemoryStatus.FORGOTTEN
    assert atomic.list_items(ACCOUNT) == []

    home = atomic.remember(ACCOUNT, "我住在南昌", source_message_id="m-home")
    atomic.delete_item(ACCOUNT, home.profile_item_id, home.version)

    events = observability.list_audit_events(account_id=ACCOUNT)
    pairs = {(event.action, event.reason) for event in events}
    assert (AuditAction.PROFILE_CREATE, "profile_remembered") in pairs
    assert (AuditAction.PROFILE_DELETE, "profile_forgotten") in pairs
    assert (AuditAction.PROFILE_DELETE, "profile_item_deleted") in pairs

    remembered_event = next(
        event for event in events if event.reason == "profile_remembered"
    )
    assert remembered.profile_item_id in (remembered_event.object_refs or [])
    forgotten_event = next(
        event for event in events if event.reason == "profile_forgotten"
    )
    assert forgotten_event.details is not None
    assert forgotten_event.details.get("matched_count") == 1

    dumped = json.dumps(
        [event.model_dump(mode="json") for event in events], ensure_ascii=False
    )
    assert "跑步" not in dumped
    assert "南昌" not in dumped


# ---------------------------------------------------------------------------
# 账号删除：迟到 worker 不重建内容
# ---------------------------------------------------------------------------


def test_stale_worker_task_after_account_deletion_writes_nothing(
    tmp_path: Path,
) -> None:
    """删除后仍握着旧任务对象的 worker 不重建运行/任务记账（含来源快照）。"""

    database, atomic, state, automatic = _sqlite_stack(
        tmp_path, extractor=_AlwaysFailingExtractor()
    )
    try:
        result = automatic.preprocess_message(
            ACCOUNT,
            conversation_id="conversation-1",
            message_id="m-late",
            content="我喜欢Python",
            run_id="run-late",
            mode="companion",
        )
        assert result.run.status is ProfileExtractionStatus.PENDING
        task = state.list_tasks(ACCOUNT)[0]

        _mark_account_deleted(database, ACCOUNT)
        delete_account_rows(database, ACCOUNT)
        assert not state.account_exists(ACCOUNT)

        status = automatic._run_retry_task(task, task.attempts + 1)  # noqa: SLF001
        assert status is ProfileExtractionStatus.EXHAUSTED
        assert state.list_tasks(ACCOUNT) == []
        assert state.list_runs(ACCOUNT) == []
        assert atomic.list_items(ACCOUNT) == []
    finally:
        database.close()


def test_commit_recheck_fails_closed_when_deleted_mid_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型调用期间账户被删除：提交前复核失败关闭，不写条目也不重建记账。"""

    database, atomic, state, automatic = _sqlite_stack(tmp_path)
    try:
        original = automatic._extract_once  # noqa: SLF001

        def delete_after_extract(**kwargs: object) -> ProfileExtractionOutput:
            output = original(**kwargs)
            _mark_account_deleted(database, ACCOUNT)
            delete_account_rows(database, ACCOUNT)
            return output

        monkeypatch.setattr(automatic, "_extract_once", delete_after_extract)

        automatic.preprocess_message(
            ACCOUNT,
            conversation_id="conversation-1",
            message_id="m-race",
            content="我喜欢Python",
            run_id="run-race",
            mode="companion",
        )
        assert atomic.list_items(ACCOUNT) == []
        assert state.list_tasks(ACCOUNT) == []
        assert state.list_runs(ACCOUNT) == []
    finally:
        database.close()


def test_other_account_worker_is_unaffected_by_deletion(tmp_path: Path) -> None:
    """同库另一账户没有删除记录：其迟到任务照常按重试状态机推进。"""

    database, _atomic, state, automatic = _sqlite_stack(
        tmp_path, extractor=_AlwaysFailingExtractor()
    )
    try:
        for account, message_id in ((ACCOUNT, "m-a"), (OTHER_ACCOUNT, "m-b")):
            automatic.preprocess_message(
                account,
                conversation_id="conversation-1",
                message_id=message_id,
                content="我喜欢Python",
                run_id=f"run-{message_id}",
                mode="companion",
            )
        _mark_account_deleted(database, ACCOUNT)
        delete_account_rows(database, ACCOUNT)
        assert state.account_exists(OTHER_ACCOUNT)

        # 后台轮每次只推进一件任务；两轮覆盖两个账户。
        automatic.run_retry_tick()
        automatic.run_retry_tick()

        assert state.list_tasks(ACCOUNT) == []
        assert state.list_runs(ACCOUNT) == []
        other_tasks = state.list_tasks(OTHER_ACCOUNT)
        assert len(other_tasks) == 1
        assert other_tasks[0].status is ProfileExtractionStatus.PENDING
    finally:
        database.close()
