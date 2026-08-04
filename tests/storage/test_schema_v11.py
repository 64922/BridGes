"""Issue 21/22 数据库升级路径测试：v10 → v12 增加两类搜索投影列。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.storage import SCHEMA_VERSION, BridgesDatabase
from bridges.storage.database import MIGRATIONS


def _build_v10_database(path: Path) -> None:
    """复刻真实 v1..v10 模式，并写入一条存量消息。"""
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '10')"
        )
        for version in range(1, 11):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "INSERT INTO conversations"
            " (conversation_id, account_id, title, mode, created_at, updated_at)"
            " VALUES ('conv-1', 'account-a', '存量对话', 'companion',"
            " '2026-08-04T00:00:00Z', '2026-08-04T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO messages"
            " (message_id, conversation_id, account_id, role, status, content,"
            "  created_at, updated_at)"
            " VALUES ('msg-1', 'conv-1', 'account-a', 'assistant', 'done', '存量消息',"
            " '2026-08-04T00:00:00Z', '2026-08-04T00:00:00Z')"
        )


def test_upgrade_from_v10_adds_search_projections_and_preserves_messages(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bridges.db"
    _build_v10_database(path)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert database.initialize() == SCHEMA_VERSION

    columns = {
        str(row[1])
        for row in database.connection.execute("PRAGMA table_info(messages)").fetchall()
    }
    assert "web_search" in columns
    assert "arxiv_search" in columns
    row = database.connection.execute(
        "SELECT content, web_search, arxiv_search"
        " FROM messages WHERE message_id = 'msg-1'"
    ).fetchone()
    assert row is not None
    assert str(row["content"]) == "存量消息"
    assert row["web_search"] is None
    assert row["arxiv_search"] is None
