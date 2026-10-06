"""工单 43：回滚演练（H-16/W-52）真实数据库验收。

复用生命周期测试夹具建造真实文件数据库与 V2/退役历史数据，再补充新旧
任务、事件、产物与画像条目，验证回滚后数据保留、退役能力不复活。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bridges.contracts.atomic_profile import (
    AtomicProfileItem,
    AtomicProfileItemStatus,
    AtomicProfileWriteOrigin,
)
from bridges.contracts.profiles import FourDimensionConfidence
from bridges.evaluation.rollback_rehearsal import (
    rehearsal_stop_new_writes,
    run_rollback_rehearsal,
    snapshot_content,
    snapshot_inventory,
)
from bridges.profiles.atomic import SqliteAtomicProfileRepository
from tests.lifecycle.harness import Harness


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _seed_tasks_and_artifacts(database, account_id: str, conversation_id: str) -> None:
    now = _now()
    with database.transaction():
        scoped = database.scoped(account_id)
        scoped.execute(
            "INSERT INTO conversation_tasks(task_id, account_id, conversation_id, goal,"
            " status, current_version, contract_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'task-v1', ?, ?)",
            (
                f"task-old-{account_id}",
                account_id,
                conversation_id,
                "整理旧任务",
                "completed",
                2,
                now,
                now,
            ),
        )
        scoped.execute(
            "INSERT INTO conversation_tasks(task_id, account_id, conversation_id, goal,"
            " status, current_version, contract_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'task-v1', ?, ?)",
            (
                f"task-new-{account_id}",
                account_id,
                conversation_id,
                "进行中的新任务",
                "active",
                1,
                now,
                now,
            ),
        )
        version_rows = (
            (f"task-old-{account_id}", 1),
            (f"task-old-{account_id}", 2),
            (f"task-new-{account_id}", 1),
        )
        for task_id, version in version_rows:
            scoped.execute(
                "INSERT INTO task_versions(version_id, task_id, account_id, version, goal,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (f"ver-{task_id}-{version}", task_id, account_id, version, "目标版本", now),
            )
        scoped.execute(
            "INSERT INTO task_events(event_id, account_id, conversation_id, task_id, kind,"
            " payload_json, created_at) VALUES (?, ?, ?, ?, 'version_created', '{}', ?)",
            (f"event-{account_id}", account_id, conversation_id, f"task-new-{account_id}", now),
        )
        scoped.execute(
            "INSERT INTO node_artifacts(artifact_id, account_id, conversation_id, run_id,"
            " task_id, task_version, recipe_id, recipe_version, node, artifact_type,"
            " schema_version, capability_version, trust_state, input_key, payload_json,"
            " content_hash, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 1, 'recipe-x', '1.0', 'synthesis', 'answer',"
            " '1', 'cap-v1', 'qualified', 'key-1', '{}', 'hash-1', ?, ?)",
            (
                f"artifact-{account_id}",
                account_id,
                conversation_id,
                f"run-{account_id}",
                f"task-new-{account_id}",
                now,
                now,
            ),
        )


def _seed_profile_item(database, account_id: str) -> str:
    item_id = f"profile-{account_id}"
    now = datetime.now(UTC)
    SqliteAtomicProfileRepository(database, initialize=False).save_item(
        AtomicProfileItem(
            profile_item_id=item_id,
            owner_account_id=account_id,
            text="用户偏好先给结论再看推导",
            identity_key=f"{account_id}:偏好先给结论",
            status=AtomicProfileItemStatus.ACTIVE,
            write_origin=AtomicProfileWriteOrigin.AUTOMATIC,
            confidence=FourDimensionConfidence.MEDIUM,
            version=1,
            created_at=now,
            updated_at=now,
        )
    )
    return item_id


def _build_harness(tmp_path: Path) -> tuple[Harness, str, str]:
    harness = Harness(tmp_path)
    account_id = harness.acc1
    harness.seed_everything()
    harness.seed_retired_and_v2_state(account_id)
    conversation_id = harness.database.scoped(account_id).execute(
        "SELECT conversation_id FROM conversations WHERE account_id = ?"
        " ORDER BY created_at LIMIT 1",
        (account_id,),
    ).fetchone()["conversation_id"]
    _seed_tasks_and_artifacts(harness.database, account_id, conversation_id)
    _seed_profile_item(harness.database, account_id)
    return harness, account_id, conversation_id


def test_rollback_rehearsal_preserves_state_and_retires_capabilities(tmp_path: Path) -> None:
    harness, account_id, _ = _build_harness(tmp_path)

    report = run_rollback_rehearsal(harness.database, account_id)

    assert report.failures() == ()
    assert report.passed is True
    assert report.inventory_after["conversation_tasks"] == 2
    assert report.inventory_after["task_versions"] == 3
    assert report.inventory_after["task_events"] == 1
    assert report.inventory_after["node_artifacts"] == 1
    assert report.inventory_after["profile_items"] == 1
    assert report.inventory_after["messages"] >= 4

    items = SqliteAtomicProfileRepository(
        harness.database, initialize=False
    ).list_items(account_id)
    assert [item.profile_item_id for item in items] == [f"profile-{account_id}"]

    scoped = harness.database.scoped(account_id)
    reminder_statuses = {
        row["status"]
        for row in scoped.execute(
            "SELECT status FROM reminders WHERE account_id = ?", (account_id,)
        ).fetchall()
    }
    assert reminder_statuses == {"retired"}
    assert int(
        scoped.execute(
            "SELECT COUNT(*) AS count FROM skill_packages"
            " WHERE account_id = ? AND status <> 'disabled'",
            (account_id,),
        ).fetchone()["count"]
    ) == 0
    assert int(
        scoped.execute(
            "SELECT COUNT(*) AS count FROM mcp_servers"
            " WHERE account_id = ? AND (status <> 'disabled' OR enabled <> 0)",
            (account_id,),
        ).fetchone()["count"]
    ) == 0


def test_stop_new_writes_check_flags_shrunk_inventory(tmp_path: Path) -> None:
    harness, account_id, _ = _build_harness(tmp_path)
    before = snapshot_inventory(harness.database, account_id)

    harness.database.scoped(account_id).execute(
        "DELETE FROM task_events WHERE account_id = ?", (account_id,)
    )

    checks = {
        check.name: check
        for check in rehearsal_stop_new_writes(harness.database, account_id, before)
    }
    assert checks["no_data_loss_after_stop"].passed is False
    assert checks["database_integrity_ok"].passed is True


def test_rollback_detects_content_change_with_equal_counts(tmp_path: Path) -> None:
    harness, account_id, _ = _build_harness(tmp_path)
    before = snapshot_inventory(harness.database, account_id)
    content = snapshot_content(harness.database, account_id)
    harness.database.scoped(account_id).execute(
        "UPDATE messages SET content = '历史内容被替换' WHERE account_id = ?", (account_id,)
    )
    checks = {check.name: check for check in rehearsal_stop_new_writes(
        harness.database, account_id, before, content
    )}
    assert checks["no_data_loss_after_stop"].passed
    assert not checks["historical_content_preserved"].passed
    assert checks["readonly_connection_rejects_writes"].passed
    assert checks["connection_mode_restored"].passed


def test_integrated_backup_restore_preserves_new_and_old_state(tmp_path: Path) -> None:
    harness, account_id, _ = _build_harness(tmp_path)
    before = snapshot_content(harness.database, account_id)
    other_before = snapshot_content(harness.database, harness.acc2)
    _, backup = harness.backup.create_backup("集成演练口令")
    harness.database.scoped(account_id).execute(
        "DELETE FROM node_artifacts WHERE account_id = ?", (account_id,)
    )
    harness.database.scoped(account_id).execute(
        "UPDATE messages SET content = '损坏正文' WHERE account_id = ?", (account_id,)
    )
    preview = harness.backup.restore_backup("集成演练口令", backup, confirmation="恢复")
    assert preview.ok, preview.reasons
    assert snapshot_content(harness.database, account_id) == before
    assert snapshot_content(harness.database, harness.acc2) == other_before
    assert run_rollback_rehearsal(harness.database, account_id).passed
