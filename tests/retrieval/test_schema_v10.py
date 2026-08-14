"""Issue 20 数据库升级路径测试：v9 → v10 保留数据并新增检索表。

沿用 v8/v9 的升级测试约定：从旧版本数据库构造迁移，验证存量行原样
保留、新表（retrieval_rounds / message_citations）可写且账户作用域
强制生效。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.storage import SCHEMA_VERSION, BridgesDatabase


def _build_v9_database(path: Path) -> None:
    """构造一个带 schema_meta version=9 的数据库骨架（无业务数据）。"""
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '9')"
        )
        connection.execute(
            "CREATE TABLE conversations ("
            " conversation_id TEXT PRIMARY KEY,"
            " account_id TEXT NOT NULL,"
            " title TEXT NOT NULL DEFAULT '',"
            " mode TEXT NOT NULL DEFAULT 'companion',"
            " pinned INTEGER NOT NULL DEFAULT 0,"
            " project_id TEXT,"
            " created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE messages ("
            " message_id TEXT PRIMARY KEY,"
            " conversation_id TEXT NOT NULL,"
            " account_id TEXT NOT NULL,"
            " role TEXT NOT NULL,"
            " attempt_number INTEGER NOT NULL DEFAULT 1,"
            " status TEXT NOT NULL DEFAULT 'done',"
            " content TEXT NOT NULL DEFAULT '',"
            " error_code TEXT,"
            " error_message TEXT,"
            " duration_ms INTEGER,"
            " model_id TEXT,"
            " run_lock_id TEXT,"
            " thinking TEXT,"
            " created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)"
        )
        # v7 迁移建立的摄取记录表：真实 v9 库必然存在（Issue 43 迁移 27
        # 的存量回填 SELECT 引用它），骨架须复刻以免升级路径缺表。
        connection.execute(
            "CREATE TABLE document_records ("
            " document_id TEXT PRIMARY KEY,"
            " account_id TEXT NOT NULL,"
            " object_id TEXT NOT NULL,"
            " conversation_id TEXT NOT NULL,"
            " content_hash TEXT NOT NULL,"
            " parser_version TEXT NOT NULL,"
            " status TEXT NOT NULL DEFAULT 'queued'"
            "   CHECK (status IN ('queued', 'parsing', 'processing',"
            "   'ready', 'empty', 'error')),"
            " failure_stage TEXT,"
            " failure_reason TEXT,"
            " retry_count INTEGER NOT NULL DEFAULT 0,"
            " title TEXT,"
            " page_count INTEGER NOT NULL DEFAULT 0,"
            " section_count INTEGER NOT NULL DEFAULT 0,"
            " chunk_count INTEGER NOT NULL DEFAULT 0,"
            " vector_enabled INTEGER NOT NULL DEFAULT 0,"
            " vector_indexed INTEGER NOT NULL DEFAULT 0,"
            " claimed_at TEXT,"
            " lease_expires_at TEXT,"
            " created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)"
        )
        # v5 迁移建立的聊天附件表：真实 v9 库必然存在（Issue 04 迁移 32
        # 的 bound 约束触发器引用它），骨架须复刻以免升级路径缺表。
        connection.execute(
            "CREATE TABLE chat_attachments ("
            " object_id TEXT PRIMARY KEY,"
            " account_id TEXT NOT NULL,"
            " conversation_id TEXT NOT NULL,"
            " message_id TEXT,"
            " upload_id TEXT NOT NULL UNIQUE,"
            " media_type TEXT NOT NULL,"
            " status TEXT NOT NULL DEFAULT 'uploaded'"
            "   CHECK (status IN ('uploaded', 'bound')),"
            " created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)"
        )
        # v2 迁移建立的模型运行锁表：真实 v9 库必然存在（Issue 10 迁移 45
        # 的 ALTER TABLE 引用它），骨架须复刻以免升级路径缺表。
        connection.execute(
            "CREATE TABLE model_run_locks ("
            " lock_id TEXT PRIMARY KEY,"
            " account_id TEXT NOT NULL,"
            " capability_name TEXT NOT NULL,"
            " capability_version TEXT NOT NULL,"
            " actual_model_id TEXT,"
            " region TEXT NOT NULL,"
            " status TEXT NOT NULL,"
            " error_code TEXT,"
            " error_message TEXT,"
            " usage TEXT,"
            " created_at TEXT NOT NULL)"
        )
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
            " VALUES ('msg-1', 'conv-1', 'account-a', 'user', 'done', '存量消息',"
            " '2026-08-04T00:00:00Z', '2026-08-04T00:00:00Z')"
        )


def test_upgrade_from_v9_preserves_rows_and_adds_retrieval_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bridges.db"
    _build_v9_database(path)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert database.initialize() == SCHEMA_VERSION  # 重复启动幂等

    # 存量对话与消息原样保留
    conversation = database.connection.execute(
        "SELECT title FROM conversations WHERE conversation_id = 'conv-1'"
    ).fetchone()
    assert conversation is not None
    assert str(conversation["title"]) == "存量对话"
    message = database.connection.execute(
        "SELECT content FROM messages WHERE message_id = 'msg-1'"
    ).fetchone()
    assert message is not None
    assert str(message["content"]) == "存量消息"

    # 新表存在且可写（账户作用域强制生效）
    with database.transaction():
        database.scoped("account-a").execute(
            "INSERT INTO retrieval_rounds"
            " (round_id, message_id, conversation_id, account_id, use_knowledge_base,"
            "  sufficiency, index_version_id, layers_json, note, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "rnd-1",
                "msg-a",
                "conv-1",
                "account-a",
                1,
                "no_hits",
                None,
                "[]",
                None,
                "2026-08-04T00:00:00Z",
            ),
        )
    row = database.scoped("account-a").execute(
        "SELECT sufficiency FROM retrieval_rounds"
        " WHERE round_id = 'rnd-1' AND account_id = 'account-a'",
        (),
    ).fetchone()
    assert row is not None
    assert str(row["sufficiency"]) == "no_hits"
