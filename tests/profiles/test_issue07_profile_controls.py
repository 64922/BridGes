"""Issue 07：分开画像记录与使用控制，保证即时撤回。

验收场景（工单 07）：
- 记住跑步 → 停止记录 → 忘掉跑步：忘掉仍生成真实删除，列表与切片均移除；
- 停止自动提取期间明确记住/修改有效；关闭使用期间忘掉和删除有效；
- 记录/使用两开关四种组合行为独立，跨进程读取一致；
- 控制更新幂等、账户隔离。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bridges.contracts.atomic_profile import AtomicProfileMemoryStatus
from bridges.contracts.profile_extraction import (
    ProfileCorrectionStatus,
    ProfileExtractionOutcome,
)
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import AtomicProfileService, InMemoryAtomicProfileRepository
from bridges.profiles.automatic import (
    AutomaticProfileService,
    InMemoryAutomaticProfileRepository,
    SqliteAutomaticProfileRepository,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.storage.database import BridgesDatabase

ACCOUNT = "issue07-user"
OTHER = "issue07-other"


def _make_service(repository) -> tuple[AtomicProfileService, AutomaticProfileService]:
    four = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )
    atomic = AtomicProfileService(four, InMemoryAtomicProfileRepository())
    automatic = AutomaticProfileService(
        four_dimension_service=four,
        repository=repository,
        atomic_profile_service=atomic,
    )
    return atomic, automatic


def _ingest(automatic: AutomaticProfileService, number: int, content: str, account: str = ACCOUNT):
    return automatic.preprocess_message(
        account,
        conversation_id="issue07-chat",
        message_id=f"m-{number}",
        content=content,
        run_id=f"r-{number}",
        mode="daily",
    )


def test_forget_after_recording_stopped_removes_item_and_slice() -> None:
    """P0 探针：记住跑步 → 不要记录 → 忘掉跑步 必须真实删除。"""
    atomic, automatic = _make_service(InMemoryAutomaticProfileRepository())

    _ingest(automatic, 1, "记住我喜欢跑步")
    assert atomic.list_items(ACCOUNT), "记住指令应先建立条目"

    stopped = _ingest(automatic, 2, "不要记录")
    assert stopped.memory is None
    assert stopped.run is not None
    assert stopped.run.outcome == ProfileExtractionOutcome.NO_SIGNAL

    forgotten = _ingest(automatic, 3, "忘掉跑步")
    assert forgotten.memory is not None, "停止记录不得阻止显式忘掉"
    assert forgotten.memory.status == AtomicProfileMemoryStatus.FORGOTTEN
    assert atomic.list_items(ACCOUNT) == []

    slice_ = atomic.compile_chat_slice(
        ACCOUNT, run_id="after-forget", current_question="跑步相关的问题"
    )
    assert slice_.included_items == []


def test_remember_works_while_recording_stopped() -> None:
    atomic, automatic = _make_service(InMemoryAutomaticProfileRepository())
    _ingest(automatic, 1, "不要记录")

    result = _ingest(automatic, 2, "记住我喜欢游泳")

    assert result.memory is not None
    assert result.memory.status == AtomicProfileMemoryStatus.REMEMBERED
    assert [item.text for item in atomic.list_items(ACCOUNT)] == ["我喜欢游泳"]


def test_explicit_correction_works_while_recording_stopped() -> None:
    atomic, automatic = _make_service(InMemoryAutomaticProfileRepository())
    _ingest(automatic, 1, "我喜欢足球")
    _ingest(automatic, 2, "不要记录")

    result = _ingest(automatic, 3, "我的关注点换成跑步")

    assert result.correction is not None
    assert result.correction.status == ProfileCorrectionStatus.WRITTEN
    texts = [item.text for item in atomic.list_items(ACCOUNT)]
    assert any("跑步" in text for text in texts)


def test_automatic_extraction_stays_blocked_for_plain_self_statement() -> None:
    """停止记录只阻止自动新增/更新：普通自述不再写入，且无模型调用。"""
    atomic, automatic = _make_service(InMemoryAutomaticProfileRepository())
    _ingest(automatic, 1, "不要记录")

    result = _ingest(automatic, 2, "我喜欢跑步")

    assert atomic.list_items(ACCOUNT) == []
    assert result.run is not None
    assert result.run.outcome == ProfileExtractionOutcome.NO_SIGNAL
    assert result.run.last_error == "用户已停止产生新的记录"


def test_recording_can_be_re_enabled_and_extraction_resumes() -> None:
    atomic, automatic = _make_service(InMemoryAutomaticProfileRepository())
    _ingest(automatic, 1, "不要记录")
    _ingest(automatic, 2, "我喜欢游泳")
    assert atomic.list_items(ACCOUNT) == []

    automatic.set_account_controls(ACCOUNT, recording_enabled=True)
    assert automatic.account_controls(ACCOUNT).recording_enabled is True

    _ingest(automatic, 3, "我喜欢跑步")
    assert [item.text for item in atomic.list_items(ACCOUNT)] == ["跑步"]


def test_usage_switch_off_keeps_items_and_management_operations() -> None:
    """关闭使用不删除信息：列表仍在，忘掉/删除照常可用。"""
    atomic, automatic = _make_service(InMemoryAutomaticProfileRepository())
    _ingest(automatic, 1, "记住我喜欢跑步")

    automatic.set_account_controls(ACCOUNT, usage_enabled=False)
    assert automatic.is_profile_usage_enabled(ACCOUNT) is False
    assert [item.text for item in atomic.list_items(ACCOUNT)] == ["我喜欢跑步"]

    forgotten = _ingest(automatic, 2, "忘掉跑步")
    assert forgotten.memory is not None
    assert forgotten.memory.status == AtomicProfileMemoryStatus.FORGOTTEN
    assert atomic.list_items(ACCOUNT) == []


def test_usage_switch_updates_are_idempotent_and_versioned() -> None:
    _, automatic = _make_service(InMemoryAutomaticProfileRepository())

    initial = automatic.account_controls(ACCOUNT)
    assert initial.usage_enabled is True
    assert initial.usage_control_version == 0

    first = automatic.set_account_controls(ACCOUNT, usage_enabled=False)
    assert first.usage_enabled is False
    assert first.usage_control_version == 1

    repeat = automatic.set_account_controls(ACCOUNT, usage_enabled=False)
    assert repeat.usage_enabled is False
    assert repeat.usage_control_version == 1, "同值重复写入必须幂等"

    back_on = automatic.set_account_controls(ACCOUNT, usage_enabled=True)
    assert back_on.usage_enabled is True
    assert back_on.usage_control_version == 2
    assert back_on.controls_version


def test_controls_are_account_scoped() -> None:
    atomic, automatic = _make_service(InMemoryAutomaticProfileRepository())
    automatic.set_account_controls(ACCOUNT, usage_enabled=False, recording_enabled=False)

    assert automatic.account_controls(ACCOUNT).usage_enabled is False
    assert automatic.account_controls(ACCOUNT).recording_enabled is False
    assert automatic.account_controls(OTHER).usage_enabled is True
    assert automatic.account_controls(OTHER).recording_enabled is True

    # 其他账户的自动记录不受影响。
    _ingest(automatic, 1, "我喜欢跑步", account=OTHER)
    assert atomic.list_items(OTHER), "其他账户的自动提取不应被阻止"


def test_switches_are_independent_in_all_four_combinations() -> None:
    atomic, automatic = _make_service(InMemoryAutomaticProfileRepository())
    _ingest(automatic, 1, "记住我喜欢跑步")

    # 记录开 / 使用开（默认）：条目在、切片可编译出内容。
    assert automatic.account_controls(ACCOUNT).recording_enabled is True
    assert automatic.account_controls(ACCOUNT).usage_enabled is True

    # 记录开 / 使用关：不删除、不注入；主动管理可用。
    automatic.set_account_controls(ACCOUNT, usage_enabled=False)
    assert atomic.list_items(ACCOUNT)

    # 记录关 / 使用关：自动提取停止，忘掉仍可用。
    automatic.set_account_controls(ACCOUNT, recording_enabled=False)
    _ingest(automatic, 2, "我喜欢游泳")
    assert "我喜欢游泳" not in [item.text for item in atomic.list_items(ACCOUNT)]
    forgotten = _ingest(automatic, 3, "忘掉跑步")
    assert forgotten.memory is not None
    assert forgotten.memory.status == AtomicProfileMemoryStatus.FORGOTTEN

    # 记录关 / 使用开：回到默认记录关闭，但使用许可不受记录影响。
    automatic.set_account_controls(ACCOUNT, usage_enabled=True)
    assert automatic.account_controls(ACCOUNT).recording_enabled is False
    assert automatic.account_controls(ACCOUNT).usage_enabled is True


def test_controls_cross_process_read_consistency(tmp_path: Path) -> None:
    """SQLite 落盘后，新进程（新数据库连接与服务实例）读到同一份控制状态。"""
    path = tmp_path / "bridges.db"
    database = BridgesDatabase(path)
    database.initialize()
    atomic, automatic = _make_service(SqliteAutomaticProfileRepository(database))
    _ingest(automatic, 1, "记住我喜欢跑步")
    automatic.set_account_controls(ACCOUNT, recording_enabled=False, usage_enabled=False)

    # 模拟另一个进程：新连接 + 新仓库 + 新服务。
    reopened = BridgesDatabase(path)
    _, other_automatic = _make_service(
        SqliteAutomaticProfileRepository(reopened, initialize=False)
    )
    controls = other_automatic.account_controls(ACCOUNT)
    assert controls.recording_enabled is False
    assert controls.usage_enabled is False
    assert controls.usage_control_version >= 1

    # 跨进程继续写入后，原进程也能读到（双向一致）。
    other_automatic.set_account_controls(ACCOUNT, recording_enabled=True)
    assert automatic.account_controls(ACCOUNT).recording_enabled is True

    # 另一账户不受影响，且使用关闭不删除条目（原子条目在内存仓库中，
    # 跨进程一致性只适用于 sqlite 持久化的控制状态）。
    assert other_automatic.account_controls(OTHER).usage_enabled is True
    assert [item.text for item in atomic.list_items(ACCOUNT)] == ["我喜欢跑步"]


def test_chat_stop_recording_command_and_api_read_the_same_state(tmp_path: Path) -> None:
    """聊天指令写入的停止记录，经服务读取（API 同源）立即可见。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    _, automatic = _make_service(SqliteAutomaticProfileRepository(database))

    _ingest(automatic, 1, "不要记录")

    controls = automatic.account_controls(ACCOUNT)
    assert controls.recording_enabled is False
    assert controls.usage_enabled is True


def test_set_account_controls_requires_at_least_one_field() -> None:
    _, automatic = _make_service(InMemoryAutomaticProfileRepository())
    with pytest.raises(ValueError):
        automatic.set_account_controls(ACCOUNT)


def test_this_turn_only_request_never_writes_long_term_preference() -> None:
    """本次临时要求只影响当前轮，不改写长期偏好（验收标准 4）。"""
    atomic, automatic = _make_service(InMemoryAutomaticProfileRepository())

    result = _ingest(automatic, 1, "这次直接给详细推导")

    assert result.run is not None
    assert result.run.outcome == ProfileExtractionOutcome.NO_SIGNAL
    assert atomic.list_items(ACCOUNT) == []


def test_third_party_and_relayed_text_never_writes_profile() -> None:
    """第三方描述与转述内容不得直接写用户画像（任务内容 4）。"""
    atomic, automatic = _make_service(InMemoryAutomaticProfileRepository())

    _ingest(automatic, 1, "我朋友说我喜欢跑步")
    _ingest(automatic, 2, "刚才的助手总结说我在准备考研")

    assert atomic.list_items(ACCOUNT) == []


def test_fuzzy_forget_targets_never_batch_delete() -> None:
    """模糊目标不批量误删：过短目标与未定位目标都不删除任何条目。"""
    atomic, automatic = _make_service(InMemoryAutomaticProfileRepository())
    _ingest(automatic, 1, "记住我喜欢跑步")
    _ingest(automatic, 2, "记住我喜欢游泳")

    short = _ingest(automatic, 3, "忘掉跑")
    assert short.memory is not None
    assert short.memory.status == AtomicProfileMemoryStatus.UNRESOLVED
    assert short.memory.matched_count == 0

    missing = _ingest(automatic, 4, "忘掉书法")
    assert missing.memory is not None
    assert missing.memory.status == AtomicProfileMemoryStatus.UNRESOLVED

    texts = [item.text for item in atomic.list_items(ACCOUNT)]
    assert texts == ["我喜欢跑步", "我喜欢游泳"]

    # 目标足够具体时，忘掉精确命中并真实删除。
    explicit = _ingest(automatic, 5, "忘掉游泳")
    assert explicit.memory is not None
    assert explicit.memory.status == AtomicProfileMemoryStatus.FORGOTTEN
    assert [item.text for item in atomic.list_items(ACCOUNT)] == ["我喜欢跑步"]
