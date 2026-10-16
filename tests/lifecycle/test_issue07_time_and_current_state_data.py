"""画像工单 07：时间与周期数据的导出、账户删除隔离与备份恢复。

周期安排与明示期限都是账户级数据：导出必须完整带上（含 recurrence_json
与 validity_phrase），账户删除后另一账户不受影响，备份恢复回到备份时点
的状态（含目标生命周期标记）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from harness import Harness

from bridges.contracts.atomic_profile import AtomicProfileGoalState
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileService,
    SqliteAtomicProfileRepository,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    SqliteFourDimensionProfileRepository,
)

ACCOUNT = "unused"
ANCHOR = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def _atomic_service(harness: Harness) -> AtomicProfileService:
    four = FourDimensionProfileService(
        InMemoryProfileRepository(),
        SqliteFourDimensionProfileRepository(harness.database, initialize=False),
    )
    return AtomicProfileService(
        four, SqliteAtomicProfileRepository(harness.database, initialize=False)
    )


def test_time_fields_export_and_account_deletion_are_isolated(tmp_path) -> None:
    harness = Harness(tmp_path)
    service = _atomic_service(harness)
    service.remember(
        harness.acc1, "我每周三晚上去实验室做实验", source_at=ANCHOR
    )
    service.remember(harness.acc1, "我计划下周考六级", source_at=ANCHOR)
    service.remember(harness.acc2, "我每天背单词")

    _, payload = harness.export.export_data(harness.acc1)
    rows = json.loads(payload)["categories"]["profile"]["items"]
    recurrence_row = next(row for row in rows if row.get("text") == "我每周三晚上去实验室做实验")
    assert json.loads(recurrence_row["recurrence_json"])["phrase"] == "每周三晚上"
    goal_row = next(row for row in rows if row.get("text") == "我计划下周考六级")
    assert goal_row["validity_phrase"] == "下周"
    assert goal_row["valid_from"] and goal_row["valid_until"]
    assert all(row["account_id"] == harness.acc1 for row in rows)

    harness.delete_account(harness.acc1)

    assert service.list_items(harness.acc1) == []
    assert [item.text for item in service.list_items(harness.acc2)] == ["我每天背单词"]


def test_time_and_recurrence_survive_backup_restore(tmp_path) -> None:
    harness = Harness(tmp_path)
    service = _atomic_service(harness)
    service.remember(harness.acc1, "我每周三晚上去实验室做实验")
    service.remember(harness.acc1, "我计划下周考六级", source_at=ANCHOR)
    _, backup = harness.backup.create_backup("时间恢复测试口令")

    # 备份后打乱：删除周期条目、暂停目标。
    items = service.list_items(harness.acc1)
    recurrence_item = next(item for item in items if item.recurrence is not None)
    service.delete_item(
        harness.acc1, recurrence_item.profile_item_id, recurrence_item.version
    )
    service.apply_lifecycle_signal(harness.acc1, "暂时不考六级")
    disturbed = {item.text: item for item in service.list_items(harness.acc1)}
    assert "我每周三晚上去实验室做实验" not in disturbed
    assert disturbed["我计划下周考六级"].goal_state is AtomicProfileGoalState.PAUSED

    result = harness.backup.restore_backup(
        "时间恢复测试口令", backup, confirmation="恢复"
    )

    assert result.ok, result.reasons
    restored = {item.text: item for item in service.list_items(harness.acc1)}
    lab = restored["我每周三晚上去实验室做实验"]
    assert lab.recurrence is not None and lab.recurrence.phrase == "每周三晚上"
    goal = restored["我计划下周考六级"]
    assert goal.goal_state is AtomicProfileGoalState.ACTIVE
    assert goal.validity_phrase == "下周"
    assert goal.valid_until is not None
