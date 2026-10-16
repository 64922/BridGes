"""画像工单 15：v88 迁移新增整体概览派生表。

概览是派生视图（可重建、不持有事实真相）：随账户删除与备份快照，但不
进入导出类别与逻辑摘要；从 v87 升级后新表可用、重复初始化幂等。
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
from bridges.profiles.overview_repository import SqliteProfileOverviewStore
from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase

ALICE = "account-alice"
OVERVIEW_TABLE = "profile_overview"


def _build_v87_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        for version in range(1, 88):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '87')")


def test_upgrade_from_v87_adds_overview_table(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-87.db"
    _build_v87_database(path)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 88

    tables = {
        str(row["name"])
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert OVERVIEW_TABLE in tables
    columns = {
        str(row["name"])
        for row in database.connection.execute(
            f"PRAGMA table_info({OVERVIEW_TABLE})"
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
    } <= columns

    # 升级幂等：重复初始化不重复执行迁移。
    assert database.initialize() == SCHEMA_VERSION
    database.close()


def test_overview_is_derived_account_scoped_and_backup_visible(
    tmp_path: Path,
) -> None:
    """概览参与删除与备份、不进导出与逻辑摘要，删除顺序在事实表之前。"""

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    store = SqliteProfileOverviewStore(database)
    now = datetime.now(UTC)
    store.invalidate(ALICE, reason="remembered", now=now)
    assert store.commit_rebuild(
        ALICE,
        captured_epoch=1,
        context_version="profile-overview-v1",
        dependency_fingerprint="fp",
        payload_json="[]",
        now=now,
    )

    # 导出边界：派生数据不入导出类别与逻辑摘要（见 lifecycle.catalog 文档）。
    exported_tables = {
        table for category in EXPORT_CATEGORIES for table in category.tables
    }
    assert OVERVIEW_TABLE not in exported_tables
    assert OVERVIEW_TABLE not in logical_summary(database, ALICE)

    # 删除顺序：派生概览先于事实表（同清单单一事实源）。
    assert ACCOUNT_TABLES.index(OVERVIEW_TABLE) < ACCOUNT_TABLES.index(
        "profile_items"
    )

    # 备份快照包含概览行（可随恢复一起回来；内容为可重建派生数据）。
    backup_path = tmp_path / "bridges-backup.db"
    with database.snapshot_lock():
        database.snapshot_to(backup_path)
    restored = BridgesDatabase(backup_path)
    assert restored.initialize() == SCHEMA_VERSION
    row = restored.connection.execute(
        f"SELECT COUNT(*) AS count FROM {OVERVIEW_TABLE} WHERE account_id = ?",
        (ALICE,),
    ).fetchone()
    assert int(row["count"]) == 1
    restored.close()

    # 账户删除清空概览行。
    delete_account_rows(database, ALICE)
    row = database.connection.execute(
        f"SELECT COUNT(*) AS count FROM {OVERVIEW_TABLE} WHERE account_id = ?",
        (ALICE,),
    ).fetchone()
    assert int(row["count"]) == 0
    database.close()
