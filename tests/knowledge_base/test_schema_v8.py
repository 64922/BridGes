"""Issue 18：Schema v8 迁移——conversation_id 可空与 source 列的表重建。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.storage import SCHEMA_VERSION, BridgesDatabase
from bridges.storage.database import MIGRATIONS


def _build_v7_database(path: Path) -> None:
    """复刻真实 v1..v7 模式并写入版本记录（升级路径的起点）。"""
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '7')"
        )
        for version in range(1, 8):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.commit()


def _seed_v7_rows(connection: sqlite3.Connection) -> None:
    """在 v7 模式写入一条聊天附件摄取记录及其分块（验证重建保留数据）。"""
    connection.execute(
        "INSERT INTO accounts(account_id, email, created_at)"
        " VALUES ('acc-1', 'v7@example.com', '2026-01-01T00:00:00')"
    )
    connection.execute(
        "INSERT INTO objects(object_id, account_id, content_hash, original_filename,"
        " media_type, content_length, status, created_at, updated_at)"
        " VALUES ('obj-1', 'acc-1', 'hash-1', '笔记.txt', 'text/plain', 12,"
        " 'active', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
    )
    connection.execute(
        "INSERT INTO document_records"
        " (document_id, account_id, object_id, conversation_id, content_hash,"
        "  parser_version, status, chunk_count, created_at, updated_at)"
        " VALUES ('doc-obj-1', 'acc-1', 'obj-1', 'conv-1', 'hash-1',"
        " 'text-utf8-v1', 'ready', 1, '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
    )
    connection.execute(
        "INSERT INTO document_chunks"
        " (chunk_id, document_id, account_id, chunk_index, content, start_offset,"
        "  end_offset, content_hash, created_at, updated_at)"
        " VALUES ('doc-obj-1:0', 'doc-obj-1', 'acc-1', 0, '内容', 0, 2,"
        " 'chunk-hash', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
    )
    connection.execute(
        "INSERT INTO index_versions"
        " (version_id, account_id, contract_json, contract_hash, status,"
        "  expected_chunk_count, chunk_count, vector_count, created_at)"
        " VALUES ('idx-1', 'acc-1', '{}', 'contract-hash', 'active', 1, 1, 1,"
        " '2026-01-01T00:00:00')"
    )
    connection.execute(
        "INSERT INTO index_active(account_id, version_id) VALUES ('acc-1', 'idx-1')"
    )
    connection.execute(
        "INSERT INTO index_vectors"
        " (vector_id, version_id, account_id, chunk_id, vector_json,"
        "  dimension_count, created_at)"
        " VALUES ('idx-1:doc-obj-1:0', 'idx-1', 'acc-1', 'doc-obj-1:0', '[]', 1024,"
        " '2026-01-01T00:00:00')"
    )
    connection.commit()


def test_upgrade_from_v7_preserves_rows_and_rebuilds_table(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    _build_v7_database(path)
    with sqlite3.connect(path) as connection:
        _seed_v7_rows(connection)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION == 9
    assert database.initialize() == SCHEMA_VERSION  # 重复启动幂等

    row = database.connection.execute(
        "SELECT conversation_id, status, chunk_count, source"
        " FROM document_records WHERE document_id = 'doc-obj-1'"
    ).fetchone()
    assert row is not None
    assert str(row["conversation_id"]) == "conv-1"
    assert str(row["status"]) == "ready"
    assert int(row["chunk_count"]) == 1
    # 存量行默认标记为聊天附件来源
    assert str(row["source"]) == "chat_attachment"
    # 指向旧表的分块行在表重建后完整保留（外键校验通过）
    chunk = database.connection.execute(
        "SELECT content FROM document_chunks WHERE document_id = 'doc-obj-1'"
    ).fetchone()
    assert chunk is not None and str(chunk["content"]) == "内容"
    vector = database.connection.execute(
        "SELECT dimension_count FROM index_vectors"
        " WHERE vector_id = 'idx-1:doc-obj-1:0'"
    ).fetchone()
    assert vector is not None and int(vector["dimension_count"]) == 1024
    # 唯一索引随重建恢复
    indexes = {
        str(row[1])
        for row in database.connection.execute(
            "PRAGMA index_list('document_records')"
        ).fetchall()
    }
    assert "idx_document_records_object" in indexes
    database.close()


def test_v8_allows_null_conversation_id_and_knowledge_base_source(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    with database.transaction():
        database.connection.execute(
            "INSERT INTO accounts(account_id, email, created_at)"
            " VALUES ('acc-1', 'v8@example.com', '2026-01-01T00:00:00')"
        )
        database.connection.execute(
            "INSERT INTO objects(object_id, account_id, content_hash,"
            " original_filename, media_type, content_length, status,"
            " created_at, updated_at)"
            " VALUES ('obj-1', 'acc-1', 'hash-1', '资料.md', 'text/markdown', 8,"
            " 'active', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
        # 全局知识库材料：conversation_id 为 NULL，source 为 knowledge_base
        database.connection.execute(
            "INSERT INTO document_records"
            " (document_id, account_id, object_id, conversation_id, content_hash,"
            "  parser_version, status, source, created_at, updated_at)"
            " VALUES ('doc-obj-1', 'acc-1', 'obj-1', NULL, 'hash-1',"
            " 'markdown-v1', 'queued', 'knowledge_base',"
            " '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
    row = database.connection.execute(
        "SELECT conversation_id, source FROM document_records"
        " WHERE document_id = 'doc-obj-1'"
    ).fetchone()
    assert row is not None
    assert row["conversation_id"] is None
    assert str(row["source"]) == "knowledge_base"
    database.close()
