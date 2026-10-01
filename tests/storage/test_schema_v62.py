"""Issue 07 迁移结构：账户级画像控制表与 v61 → v62 增量升级。

断言新表真实存在、进入启动完整性清单，旧版本库升级后既有数据保留；
同时核对该表已登记进账户删除与导出目录（单一事实源）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.lifecycle.catalog import ACCOUNT_TABLES, DELETION_ORDER, EXPORT_CATEGORIES
from bridges.storage.database import (
    MIGRATIONS,
    REQUIRED_TABLES,
    SCHEMA_VERSION,
    BridgesDatabase,
)

CONTROLS_TABLE = "profile_account_controls"

LEGACY_VERSION = 61


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }


def _columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def test_fresh_database_has_profile_controls_table(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert SCHEMA_VERSION >= LEGACY_VERSION + 1
    assert CONTROLS_TABLE in _table_names(path)
    columns = _columns(path, CONTROLS_TABLE)
    assert "account_id" in columns
    assert "profile_usage_enabled" in columns
    assert "controls_version" in columns
    assert "usage_control_version" in columns
    assert "updated_at" in columns


def test_controls_table_is_required_at_startup() -> None:
    """控制表纳入启动完整性清单：迁移缺失时启动失败关闭，而非静默全开。"""
    assert CONTROLS_TABLE in REQUIRED_TABLES


def test_upgrade_from_v61_preserves_data_and_adds_controls(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        for version in range(1, LEGACY_VERSION + 1):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta"
            " (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', ?)",
            (str(LEGACY_VERSION),),
        )
        connection.execute(
            "INSERT INTO profile_extraction_privacy_blocks"
            "(block_id, account_id, scope, normalized_value, created_at)"
            " VALUES ('blk-1', 'acc-1', 'account', '', '2026-01-01T00:00:00+00:00')"
        )
        connection.commit()
    finally:
        connection.close()

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert CONTROLS_TABLE in _table_names(path)
    # 既有停止记录规则在升级后保留（记录开关的既有持久状态不迁移、不丢失）。
    with sqlite3.connect(path) as verify:
        row = verify.execute(
            "SELECT scope FROM profile_extraction_privacy_blocks WHERE block_id = 'blk-1'"
        ).fetchone()
    assert row is not None and row[0] == "account"
    # 新表空行按默认读取（服务层默认两个开关均开启）。
    with sqlite3.connect(path) as verify:
        assert verify.execute(
            f"SELECT COUNT(*) FROM {CONTROLS_TABLE}"
        ).fetchone()[0] == 0


def test_controls_table_is_registered_for_deletion_and_export() -> None:
    """账户删除顺序与导出目录共用同一清单：新表随账户删除并进入导出。"""
    assert CONTROLS_TABLE in ACCOUNT_TABLES
    assert CONTROLS_TABLE in DELETION_ORDER
    profile_category = next(
        category for category in EXPORT_CATEGORIES if category.key == "profile"
    )
    assert CONTROLS_TABLE in profile_category.tables
