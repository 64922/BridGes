"""画像工单 13：v87 迁移新增进行中事项的归组列与近期工作上下文。

上下文是派生视图（可重建、不持有事实真相）：随账户删除与备份快照，但不
进入导出类别与逻辑摘要；从 v86 升级后新列/新表可用、重复初始化幂等。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from bridges.lifecycle.catalog import (
    ACCOUNT_TABLES,
    EXPORT_CATEGORIES,
    delete_account_rows,
    logical_summary,
)
from bridges.profiles.ongoing_repository import SqliteOngoingContextStore
from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase

ALICE = "account-alice"
ONGOING_TABLE = "profile_ongoing_context"


def _build_v86_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        for version in range(1, 87):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '86')")


def test_upgrade_from_v86_adds_ongoing_context(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-86.db"
    _build_v86_database(path)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 87

    columns = {
        str(row["name"])
        for row in database.connection.execute("PRAGMA table_info(profile_items)")
    }
    assert {"related_goal_item_id", "domain_reference_json"} <= columns
    tables = {
        str(row["name"])
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert ONGOING_TABLE in tables
    context_columns = {
        str(row["name"])
        for row in database.connection.execute(
            f"PRAGMA table_info({ONGOING_TABLE})"
        )
    }
    assert {
        "account_id",
        "context_version",
        "epoch",
        "dependency_fingerprint",
        "payload_json",
        "built_at",
        "invalidated_at",
        "invalidated_reason",
    } <= context_columns

    # 升级幂等：重复初始化不重复执行迁移。
    assert database.initialize() == SCHEMA_VERSION
    database.close()


def test_ongoing_context_is_derived_account_scoped_and_backup_visible(
    tmp_path: Path,
) -> None:
    """上下文参与删除与备份、不进导出与逻辑摘要，删除顺序在事实表之前。"""

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    store = SqliteOngoingContextStore(database)
    now = datetime.now(UTC)
    store.invalidate(ALICE, reason="remembered", now=now)
    assert store.commit_rebuild(
        ALICE,
        captured_epoch=1,
        context_version="ongoing-context-v1",
        dependency_fingerprint="fp",
        payload_json="[]",
        now=now,
    )

    # 导出边界：派生数据不入导出类别与逻辑摘要（见 lifecycle.catalog 文档）。
    exported_tables = {
        table for category in EXPORT_CATEGORIES for table in category.tables
    }
    assert ONGOING_TABLE not in exported_tables
    assert ONGOING_TABLE not in logical_summary(database, ALICE)

    # 删除顺序：派生上下文先于事实表（同清单单一事实源）。
    assert ACCOUNT_TABLES.index(ONGOING_TABLE) < ACCOUNT_TABLES.index("profile_items")

    # 备份快照包含上下文行（可随恢复一起回来）。
    backup_path = tmp_path / "bridges-backup.db"
    with database.snapshot_lock():
        database.snapshot_to(backup_path)
    restored = BridgesDatabase(backup_path)
    assert restored.initialize() == SCHEMA_VERSION
    row = restored.connection.execute(
        f"SELECT COUNT(*) AS count FROM {ONGOING_TABLE} WHERE account_id = ?",
        (ALICE,),
    ).fetchone()
    assert int(row["count"]) == 1
    restored.close()

    # 账户删除清空上下文行。
    delete_account_rows(database, ALICE)
    row = database.connection.execute(
        f"SELECT COUNT(*) AS count FROM {ONGOING_TABLE} WHERE account_id = ?",
        (ALICE,),
    ).fetchone()
    assert int(row["count"]) == 0
    database.close()
