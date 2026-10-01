"""跨轮任务持久状态的生命周期测试（改进工单 08，验收 5）。

覆盖新增持久状态要求的四件事：

- 版本化迁移：v61 建表由 tests/storage 覆盖；此处验证真实行可写读；
- 导出：任务/版本/条件/等待/审计按账户进入导出类别；
- 删除：账户删除清空任务域，且不影响其他账户；
- 备份/恢复：快照与还原后任务域逐表条数一致（可重建/恢复）。
"""

from __future__ import annotations

import json

from harness import Harness

from bridges.contracts.tasks import (
    ConditionScope,
    TaskConditionInput,
    TaskRelation,
    TaskTurnRequest,
)
from bridges.tasks.repository import TaskRepository
from bridges.tasks.service import TaskService, user_condition

PASSPHRASE = "task-lifecycle-passphrase"

_TASK_TABLES = (
    "conversation_tasks",
    "task_versions",
    "task_conditions",
    "task_waits",
    "task_events",
)


def _count(harness: Harness, account_id: str, table: str) -> int:
    row = (
        harness.database.scoped(account_id)
        .execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE account_id = ?",
            (account_id,),
        )
        .fetchone()
    )
    return int(row["count"])


def _seed_tasks(harness: Harness, account_id: str, tag: str) -> str:
    conversation_id = harness.create_conversation(
        account_id, f"任务{tag}", message_count=1
    )
    service = TaskService(TaskRepository(harness.database))
    source = f"u-{tag}"
    result = service.apply_turn(
        account_id,
        TaskTurnRequest(
            conversation_id=conversation_id,
            user_message_id=source,
            relation=TaskRelation.NEW,
            goal=f"目标 {tag}",
            conditions=[
                TaskConditionInput(
                    kind="budget", text=f"预算 {tag}", source_message_id=source
                ),
                user_condition(
                    kind="exclusion", text=f"不要二手 {tag}",
                    source_message_id=source, scope=ConditionScope.CONVERSATION,
                ),
            ],
        ),
    )
    task_id = result.task.task.task_id
    service.open_wait(
        account_id,
        conversation_id=conversation_id,
        question="预算大概是多少？",
        missing_fields=["budget"],
        origin_message_id=f"a-{tag}",
        source_message_id=source,
    )
    return task_id


def test_task_tables_are_populated_and_exported(tmp_path) -> None:
    harness = Harness(tmp_path)
    _seed_tasks(harness, harness.acc1, "A")
    document = json.loads(harness.export.export_data(harness.acc1)[1])
    category = document["categories"]["tasks"]
    assert category["label"] == "跨轮任务与澄清"
    for table in _TASK_TABLES:
        assert _count(harness, harness.acc1, table) > 0, table
    # 导出内容覆盖五张表（任务目标、条件正文与来源消息都可审阅）。
    raw = json.dumps(category, ensure_ascii=False)
    assert "目标 A" in raw
    assert "预算 A" in raw
    assert "不要二手 A" in raw
    assert "u-A" in raw


def test_account_deletion_clears_tasks_without_touching_other_account(tmp_path) -> None:
    harness = Harness(tmp_path)
    _seed_tasks(harness, harness.acc1, "A")
    _seed_tasks(harness, harness.acc2, "B")
    before = {table: _count(harness, harness.acc1, table) for table in _TASK_TABLES}
    assert all(count > 0 for count in before.values())

    harness.delete_account(harness.acc1)
    for table in _TASK_TABLES:
        assert _count(harness, harness.acc1, table) == 0, table
        assert _count(harness, harness.acc2, table) > 0, table


def test_backup_and_restore_round_trip_tasks(tmp_path) -> None:
    harness = Harness(tmp_path)
    _seed_tasks(harness, harness.acc1, "A")
    _seed_tasks(harness, harness.acc2, "B")
    before = {table: _count(harness, harness.acc1, table) for table in _TASK_TABLES}
    _, backup_bytes = harness.backup.create_backup(PASSPHRASE)

    harness.delete_account(harness.acc1)
    for table in _TASK_TABLES:
        assert _count(harness, harness.acc1, table) == 0, table

    preview = harness.backup.restore_backup(
        PASSPHRASE, backup_bytes, confirmation="恢复"
    )
    assert preview.ok, preview.reasons
    for table in _TASK_TABLES:
        assert _count(harness, harness.acc1, table) == before[table], table
        assert _count(harness, harness.acc2, table) > 0, table
