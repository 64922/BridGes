"""Issue 19：Schema v9 迁移——learning_projects 表与 document_records 项目来源重建。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bridges.storage import SCHEMA_VERSION, BridgesDatabase
from bridges.storage.database import MIGRATIONS


def _build_v8_database(path: Path) -> None:
    """复刻真实 v1..v8 模式并写入版本记录（升级路径的起点）。"""
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '8')"
        )
        for version in range(1, 9):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.commit()


def _seed_v8_rows(connection: sqlite3.Connection) -> None:
    """在 v8 模式写入一条知识库摄取记录及其分块（验证重建保留数据）。"""
    connection.execute(
        "INSERT INTO accounts(account_id, email, created_at)"
        " VALUES ('acc-1', 'v8@example.com', '2026-01-01T00:00:00')"
    )
    connection.execute(
        "INSERT INTO objects(object_id, account_id, content_hash, original_filename,"
        " media_type, content_length, status, created_at, updated_at)"
        " VALUES ('obj-1', 'acc-1', 'hash-1', '资料.md', 'text/markdown', 8,"
        " 'active', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
    )
    connection.execute(
        "INSERT INTO document_records"
        " (document_id, account_id, object_id, conversation_id, content_hash,"
        "  parser_version, status, chunk_count, source, created_at, updated_at)"
        " VALUES ('doc-obj-1', 'acc-1', 'obj-1', NULL, 'hash-1',"
        " 'markdown-v1', 'ready', 1, 'knowledge_base',"
        " '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
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


def test_upgrade_from_v8_preserves_rows_and_rebuilds_table(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    _build_v8_database(path)
    with sqlite3.connect(path) as connection:
        _seed_v8_rows(connection)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION == 9
    assert database.initialize() == SCHEMA_VERSION  # 重复启动幂等

    row = database.connection.execute(
        "SELECT status, chunk_count, source, project_id"
        " FROM document_records WHERE document_id = 'doc-obj-1'"
    ).fetchone()
    assert row is not None
    assert str(row["status"]) == "ready"
    assert int(row["chunk_count"]) == 1
    assert str(row["source"]) == "knowledge_base"
    # 新增 project_id 列对存量行为 NULL
    assert row["project_id"] is None
    # 指向旧表的分块/向量行在表重建后完整保留（外键校验通过）
    chunk = database.connection.execute(
        "SELECT content FROM document_chunks WHERE document_id = 'doc-obj-1'"
    ).fetchone()
    assert chunk is not None and str(chunk["content"]) == "内容"
    vector = database.connection.execute(
        "SELECT dimension_count FROM index_vectors"
        " WHERE vector_id = 'idx-1:doc-obj-1:0'"
    ).fetchone()
    assert vector is not None and int(vector["dimension_count"]) == 1024
    # 索引随重建恢复
    indexes = {
        str(row[1])
        for row in database.connection.execute(
            "PRAGMA index_list('document_records')"
        ).fetchall()
    }
    assert "idx_document_records_object" in indexes
    assert "idx_document_records_account_source" in indexes
    database.close()


def test_v9_accepts_project_file_source_and_learning_projects(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    with database.transaction():
        database.connection.execute(
            "INSERT INTO accounts(account_id, email, created_at)"
            " VALUES ('acc-1', 'v9@example.com', '2026-01-01T00:00:00')"
        )
        database.connection.execute(
            "INSERT INTO objects(object_id, account_id, content_hash,"
            " original_filename, media_type, content_length, status,"
            " created_at, updated_at)"
            " VALUES ('obj-1', 'acc-1', 'hash-1', '笔记.txt', 'text/plain', 8,"
            " 'active', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
        database.connection.execute(
            "INSERT INTO learning_projects"
            " (project_id, account_id, name, description, created_at, updated_at)"
            " VALUES ('proj-1', 'acc-1', '高等数学', '',"
            " '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
        # 项目文件：source 为 project_file，project_id 指向学习项目
        database.connection.execute(
            "INSERT INTO document_records"
            " (document_id, account_id, object_id, conversation_id, content_hash,"
            "  parser_version, status, source, project_id, created_at, updated_at)"
            " VALUES ('doc-obj-1', 'acc-1', 'obj-1', NULL, 'hash-1',"
            " 'text-utf8-v1', 'queued', 'project_file', 'proj-1',"
            " '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
    row = database.connection.execute(
        "SELECT source, project_id FROM document_records"
        " WHERE document_id = 'doc-obj-1'"
    ).fetchone()
    assert row is not None
    assert str(row["source"]) == "project_file"
    assert str(row["project_id"]) == "proj-1"

    # 旧 source 值仍被 CHECK 接受；未知值被拒绝
    with database.transaction():
        database.connection.execute(
            "INSERT INTO objects(object_id, account_id, content_hash,"
            " original_filename, media_type, content_length, status,"
            " created_at, updated_at)"
            " VALUES ('obj-2', 'acc-1', 'hash-2', '资料.md', 'text/markdown', 8,"
            " 'active', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
        database.connection.execute(
            "INSERT INTO document_records"
            " (document_id, account_id, object_id, conversation_id, content_hash,"
            "  parser_version, status, source, created_at, updated_at)"
            " VALUES ('doc-obj-2', 'acc-1', 'obj-2', NULL, 'hash-2',"
            " 'markdown-v1', 'queued', 'knowledge_base',"
            " '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
    with pytest.raises(sqlite3.IntegrityError), database.transaction():
        database.connection.execute(
            "INSERT INTO document_records"
            " (document_id, account_id, object_id, conversation_id, content_hash,"
            "  parser_version, status, source, created_at, updated_at)"
            " VALUES ('doc-obj-3', 'acc-1', 'obj-2', NULL, 'hash-2',"
            " 'markdown-v1', 'queued', 'unknown_source',"
            " '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )

    # learning_projects 外键级联：删除账户后项目行随之删除
    with database.transaction():
        database.connection.execute(
            "INSERT INTO accounts(account_id, email, created_at)"
            " VALUES ('acc-2', 'cascade@example.com', '2026-01-01T00:00:00')"
        )
        database.connection.execute(
            "INSERT INTO learning_projects"
            " (project_id, account_id, name, created_at, updated_at)"
            " VALUES ('proj-2', 'acc-2', '临时项目',"
            " '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
    with database.transaction():
        database.connection.execute(
            "DELETE FROM accounts WHERE account_id = 'acc-2'"
        )
    orphan = database.connection.execute(
        "SELECT 1 FROM learning_projects WHERE project_id = 'proj-2'"
    ).fetchone()
    assert orphan is None
    database.close()
