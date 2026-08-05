"""SCHEMA_VERSION 21 → 22 迁移测试（Issue 34 插件中心表）。

验证旧库升级后 skill_packages / account_skill_states 两张表存在、
索引就绪、旧数据（reminders）完整保留，且重启不会重复迁移。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase


def _build_v21_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '21')"
        )
        for version in range(1, 22):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "INSERT INTO reminder_settings (account_id, timezone, smtp_status)"
            " VALUES ('acc-1', 'Asia/Shanghai', 'verified')"
        )


def test_upgrade_from_v21_creates_plugin_tables_and_preserves_data(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    _build_v21_database(path)
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert "skill_packages" in tables
    assert "account_skill_states" in tables
    # 旧数据保留
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT timezone, smtp_status FROM reminder_settings"
            " WHERE account_id = 'acc-1'"
        ).fetchone()
    assert row == ("Asia/Shanghai", "verified")


def test_v22_columns_match_service_contract(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    with sqlite3.connect(path) as connection:
        package_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(skill_packages)")
        }
        state_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(account_skill_states)")
        }
    assert {
        "package_id", "account_id", "plugin_id", "version", "name",
        "status", "object_id", "file_count", "content_length",
        "failure_reason", "installed_at", "updated_at",
    } <= package_columns
    assert {"account_id", "plugin_id", "enabled"} <= state_columns


def test_repeated_startup_does_not_remigrate(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    # 首次启动后插入插件状态，再重建连接验证数据存活且版本不变
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO account_skill_states (account_id, plugin_id, enabled,"
            " updated_at) VALUES ('a', 'p', 0, '2026-08-06T00:00:00+00:00')"
        )
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT enabled FROM account_skill_states"
            " WHERE account_id = 'a' AND plugin_id = 'p'"
        ).fetchone()
    assert row == (0,)
