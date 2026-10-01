"""工单 08 迁移结构：跨轮任务域对象与 v60 → v61 增量升级。

断言新表/新列真实存在，并验证旧版本库升级后既有数据保留、任务域可用。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.storage.database import (
    MIGRATIONS,
    REQUIRED_TABLES,
    SCHEMA_VERSION,
    BridgesDatabase,
)

TASK_TABLES = (
    "conversation_tasks",
    "task_versions",
    "task_conditions",
    "task_waits",
    "task_events",
)

LEGACY_VERSION = 60


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


def test_fresh_database_has_task_domain_objects(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert set(TASK_TABLES) <= _table_names(path)
    assert "current_task_id" in _columns(path, "conversations")
    # 任务版本快照含结果引用列（任务内容第 2 条「结果引用」）。
    assert "result_refs_json" in _columns(path, "task_versions")
    # 任务表携带账户列，账户隔离由 scoped() 强制。
    for table in TASK_TABLES:
        assert "account_id" in _columns(path, table), table


def test_task_tables_are_required_at_startup() -> None:
    """任务五表纳入启动完整性清单：迁移半执行时启动失败关闭，而非接口 500。"""
    assert set(TASK_TABLES) <= REQUIRED_TABLES


def test_upgrade_from_v60_preserves_data_and_adds_task_domain(tmp_path: Path) -> None:
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
            "INSERT INTO conversations(conversation_id, account_id, title, mode,"
            " pinned, created_at, updated_at)"
            " VALUES ('conv-1', 'acc-1', '旧对话', 'companion', 0,"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        connection.commit()
    finally:
        connection.close()

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert set(TASK_TABLES) <= _table_names(path)
    assert "current_task_id" in _columns(path, "conversations")
    with sqlite3.connect(path) as verify:
        row = verify.execute(
            "SELECT title, current_task_id FROM conversations WHERE conversation_id = 'conv-1'"
        ).fetchone()
    assert row is not None
    assert row[0] == "旧对话"
    # 旧会话指针为空，兼容既有数据。
    assert row[1] is None
