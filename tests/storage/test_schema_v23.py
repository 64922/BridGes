"""MCP 表迁移测试（Issue 35）：v22→v23 升级保留旧数据并建新表。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase


def _build_v22_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '22')"
        )
        for version in range(1, 23):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        # 旧数据：v22 的插件中心记录。
        connection.execute(
            "INSERT INTO skill_packages (package_id, account_id, plugin_id,"
            " version, name, status, object_id, file_count, content_length,"
            " installed_at, updated_at) VALUES"
            " ('pkg-1', 'acc-1', 'e2e-todo', '1.2.3', '待办整理', 'installed',"
            " 'obj-1', 5, 1024, '2026-08-01T00:00:00+00:00',"
            " '2026-08-01T00:00:00+00:00')"
        )


def test_upgrade_from_v22_creates_mcp_tables_and_preserves_data(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    _build_v22_database(path)
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "mcp_servers" in tables
    assert "mcp_calls" in tables
    # 旧数据保留。
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT name, status FROM skill_packages WHERE package_id = 'pkg-1'"
        ).fetchone()
    assert row == ("待办整理", "installed")


def test_v23_columns_match_service_contract(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    _build_v22_database(path)
    database = BridgesDatabase(path)
    database.initialize()
    with sqlite3.connect(path) as connection:
        server_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(mcp_servers)")
        }
        call_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(mcp_calls)")
        }
    assert {
        "mcp_id",
        "account_id",
        "name",
        "version",
        "source",
        "integrity",
        "integrity_sha256",
        "command",
        "permissions",
        "status",
        "enabled",
        "object_id",
        "failure_reason",
        "installed_at",
        "updated_at",
    } <= server_columns
    assert {
        "call_id",
        "account_id",
        "mcp_id",
        "tool",
        "status",
        "error_code",
        "error_message",
        "latency_ms",
        "sensitive_ops",
        "created_at",
    } <= call_columns


def test_repeated_startup_does_not_remigrate(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    _build_v22_database(path)
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    # 写入一条 MCP 记录后重建连接。
    database.close()
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "mcp_servers" in tables
