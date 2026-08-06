"""账户删除状态机迁移测试（Issue 37）：v24→v25 升级保留旧数据并加新表。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase


def _build_v24_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '24')"
        )
        for version in range(1, 25):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        # 旧数据：v24 的会话与消息。
        connection.execute(
            "INSERT INTO conversations (conversation_id, account_id, title, mode,"
            " pinned, project_id, plugin_selection, created_at, updated_at) VALUES"
            " ('conv-1', 'acc-1', '旧会话', 'companion', 0, NULL, NULL,"
            " '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO messages (message_id, conversation_id, account_id, role,"
            " attempt_number, status, content, created_at, updated_at) VALUES"
            " ('msg-1', 'conv-1', 'acc-1', 'user', 1, 'done', '旧消息',"
            " '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')"
        )


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }


def test_upgrade_from_v24_adds_deletion_table_and_preserves_data(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    _build_v24_database(path)
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert "account_deletions" in _tables(path)
    # 旧数据保留。
    with sqlite3.connect(path) as connection:
        conversation = connection.execute(
            "SELECT title FROM conversations WHERE conversation_id = 'conv-1'"
        ).fetchone()
    assert conversation[0] == "旧会话"


def test_account_deletions_roundtrip(tmp_path: Path) -> None:
    """新表可写入删除状态并读回（运行面）。"""
    path = tmp_path / "bridges.db"
    _build_v24_database(path)
    BridgesDatabase(path).initialize()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO account_deletions(deletion_id, account_id, status,"
            " retry_count, last_error, pending_hashes, started_at)"
            " VALUES ('d-1', 'acc-1', 'failed', 2, '模拟失败', '[\"h1\"]',"
            " '2026-08-06T00:00:00+00:00')"
        )
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT status, retry_count, pending_hashes FROM account_deletions"
            " WHERE deletion_id = 'd-1'"
        ).fetchone()
    assert row[0] == "failed"
    assert row[1] == 2
    assert "h1" in row[2]


def test_reinitialize_does_not_duplicate_migration(tmp_path: Path) -> None:
    """重启不重复迁移：第二次 initialize 幂等且版本号一致。"""
    path = tmp_path / "bridges.db"
    _build_v24_database(path)
    first = BridgesDatabase(path)
    assert first.initialize() == SCHEMA_VERSION
    second = BridgesDatabase(path)
    assert second.initialize() == SCHEMA_VERSION
