"""对话级插件选择迁移测试（Issue 36）：v23→v24 升级保留旧数据并加新列。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase


def _build_v23_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '23')"
        )
        for version in range(1, 24):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        # 旧数据：v23 的会话与消息（无插件选择列）。
        connection.execute(
            "INSERT INTO conversations (conversation_id, account_id, title, mode,"
            " pinned, project_id, created_at, updated_at) VALUES"
            " ('conv-1', 'acc-1', '旧会话', 'companion', 0, NULL,"
            " '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO messages (message_id, conversation_id, account_id, role,"
            " attempt_number, status, content, created_at, updated_at) VALUES"
            " ('msg-1', 'conv-1', 'acc-1', 'user', 1, 'done', '旧消息',"
            " '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')"
        )


def _columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})")
        }


def test_upgrade_from_v23_adds_selection_columns_and_preserves_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bridges.db"
    _build_v23_database(path)
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert "plugin_selection" in _columns(path, "conversations")
    assert "mcp_call" in _columns(path, "messages")
    # 旧数据保留且新列默认空。
    with sqlite3.connect(path) as connection:
        conversation = connection.execute(
            "SELECT title, plugin_selection FROM conversations"
            " WHERE conversation_id = 'conv-1'"
        ).fetchone()
        message = connection.execute(
            "SELECT content, mcp_call FROM messages WHERE message_id = 'msg-1'"
        ).fetchone()
    assert conversation[0] == "旧会话"
    assert conversation[1] is None
    assert message[0] == "旧消息"
    assert message[1] is None


def test_selection_columns_roundtrip(tmp_path: Path) -> None:
    """新列可写入 JSON 选择与 mcp_call 投影并读回（迁移后运行面）。"""
    path = tmp_path / "bridges.db"
    _build_v23_database(path)
    BridgesDatabase(path).initialize()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE conversations SET plugin_selection = ?"
            " WHERE conversation_id = 'conv-1'",
            ('[{"kind": "skill", "plugin_id": "bridges-humanizer"}]',),
        )
        connection.execute(
            "UPDATE messages SET mcp_call = ? WHERE message_id = 'msg-1'",
            ('{"status": "succeeded", "tool": "echo"}',),
        )
    with sqlite3.connect(path) as connection:
        selection = connection.execute(
            "SELECT plugin_selection FROM conversations"
            " WHERE conversation_id = 'conv-1'"
        ).fetchone()[0]
        call = connection.execute(
            "SELECT mcp_call FROM messages WHERE message_id = 'msg-1'"
        ).fetchone()[0]
    assert "bridges-humanizer" in selection
    assert '"succeeded"' in call


def test_reinitialize_does_not_duplicate_migration(tmp_path: Path) -> None:
    """重启不重复迁移：第二次 initialize 幂等且版本号一致。"""
    path = tmp_path / "bridges.db"
    _build_v23_database(path)
    first = BridgesDatabase(path)
    assert first.initialize() == SCHEMA_VERSION
    second = BridgesDatabase(path)
    assert second.initialize() == SCHEMA_VERSION
