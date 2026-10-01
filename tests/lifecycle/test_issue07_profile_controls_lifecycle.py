"""工单 07：真实控制状态行的导出、删除隔离与备份恢复。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from harness import Harness

from bridges.profiles.automatic import SqliteAutomaticProfileRepository


def test_controls_export_and_account_deletion_are_isolated(tmp_path) -> None:
    harness = Harness(tmp_path)
    repository = SqliteAutomaticProfileRepository(harness.database, initialize=False)
    with repository.transaction():
        for account in (harness.acc1, harness.acc2):
            repository.block_recording(account, None, datetime.now(UTC))
            repository.set_profile_usage_enabled(account, False, datetime.now(UTC))
    _, payload = harness.export.export_data(harness.acc1)
    items = json.loads(payload)["categories"]["profile"]["items"]
    control = next(item for item in items if "profile_usage_enabled" in item)
    assert control["account_id"] == harness.acc1
    assert control["profile_usage_enabled"] == 0
    assert control["usage_control_version"] == 1
    assert all(item["account_id"] == harness.acc1 for item in items)
    harness.delete_account(harness.acc1)
    assert repository.get_profile_usage_state(harness.acc1).version == 0
    assert repository.is_recording_blocked(harness.acc1) is False
    assert repository.get_profile_usage_state(harness.acc2).enabled is False
    assert repository.is_recording_blocked(harness.acc2) is True


def test_controls_survive_backup_restore(tmp_path) -> None:
    harness = Harness(tmp_path)
    repository = SqliteAutomaticProfileRepository(harness.database, initialize=False)
    with repository.transaction():
        repository.block_recording(harness.acc1, None, datetime.now(UTC))
        repository.set_profile_usage_enabled(harness.acc1, False, datetime.now(UTC))
    _, backup = harness.backup.create_backup("控制状态恢复测试口令")
    with repository.transaction():
        repository.unblock_recording(harness.acc1)
        repository.set_profile_usage_enabled(harness.acc1, True, datetime.now(UTC))
    result = harness.backup.restore_backup("控制状态恢复测试口令", backup, confirmation="恢复")
    assert result.ok, result.reasons
    assert repository.is_recording_blocked(harness.acc1) is True
    restored = repository.get_profile_usage_state(harness.acc1)
    assert restored.enabled is False
    assert restored.version == 1
