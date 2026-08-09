"""Issue 02：学习项目文件迁入账户级全局知识库。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bridges.chat.repository import ConversationRepository
from bridges.contracts.chat import ChatMode
from bridges.ingestion.embedding import DeterministicEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.service import IngestionService
from bridges.knowledge_base.service import KnowledgeBaseService
from bridges.learning_projects.service import LearningProjectService
from bridges.learning_projects.migration import ProjectMigrationService


def _services(storage):
    embedding = DeterministicEmbeddingPort()
    ingestion = IngestionService(
        database=storage["database"],
        object_repository=storage["repository"],
        embedding=embedding,
        index=VersionedIndex(storage["database"], embedding),
    )
    conversations = ConversationRepository(storage["database"])
    projects = LearningProjectService(
        storage["database"], storage["repository"], ingestion, conversations
    )
    knowledge_base = KnowledgeBaseService(
        storage["database"], storage["repository"], ingestion
    )
    migration = ProjectMigrationService(
        database=storage["database"],
        object_repository=storage["repository"],
        ingestion_service=ingestion,
    )
    return projects, knowledge_base, migration, conversations, ingestion


def _project_file(storage, projects, ingestion, account_id: str, name: str, content: bytes):
    project = projects.create_project(account_id, name, None)
    stored = storage["repository"].create_object(
        account_id, "笔记.txt", content, media_type="text/plain"
    )
    ingestion.enqueue(account_id, stored.object_id, project_id=project.project_id)
    return project.project_id, stored.object_id


def test_migration_deduplicates_same_account_content_and_detaches_conversations(storage):
    projects, knowledge_base, migration, conversations, ingestion = _services(storage)
    account_id = storage["account_a"]
    first_project_id, first_object_id = _project_file(
        storage, projects, ingestion, account_id, "微积分", "相同内容".encode()
    )
    second_project_id, second_object_id = _project_file(
        storage, projects, ingestion, account_id, "线性代数", "相同内容".encode()
    )
    conversations.create_conversation(
        account_id=account_id,
        conversation_id="conversation-a",
        title="旧项目对话",
        mode=ChatMode.STUDY.value,
        created_at=datetime.now(UTC),
        project_id=first_project_id,
    )

    # 先把旧项目源摄取到稳定状态；迁移不改写该源记录或对象。
    ingestion.process_pending()
    started = migration.start(account_id)
    assert started.total_files == 2
    assert started.status == "running"

    completed = migration.process_pending(account_id=account_id)
    assert completed.status == "completed"
    assert completed.completed_files == 2

    target_rows = storage["database"].scoped(account_id).execute(
        "SELECT document_id, object_id, source, project_id, status"
        " FROM document_records WHERE account_id = ? AND source = 'knowledge_base'",
        (account_id,),
    ).fetchall()
    assert len(target_rows) == 1
    assert target_rows[0]["project_id"] is None
    assert target_rows[0]["status"] == "ready"

    mappings = storage["database"].scoped(account_id).execute(
        "SELECT source_object_id, target_object_id, status"
        " FROM learning_project_migrations WHERE account_id = ?"
        " ORDER BY source_object_id",
        (account_id,),
    ).fetchall()
    assert len(mappings) == 2
    assert {str(row["source_object_id"]) for row in mappings} == {
        first_object_id,
        second_object_id,
    }
    assert {str(row["target_object_id"]) for row in mappings} == {
        str(target_rows[0]["object_id"])
    }
    assert {str(row["status"]) for row in mappings} == {"completed"}

    # 原项目来源和对象保留，只有会话运行时项目归属被解除；审计映射保留旧名称。
    old_sources = storage["database"].scoped(account_id).execute(
        "SELECT object_id, source, project_id FROM document_records"
        " WHERE account_id = ? AND source = 'project_file'",
        (account_id,),
    ).fetchall()
    assert {str(row["object_id"]) for row in old_sources} == {
        first_object_id,
        second_object_id,
    }
    conversation = conversations.get_conversation(account_id, "conversation-a")
    assert conversation is not None and conversation.project_id is None
    assert conversation.legacy_project_name == "微积分"
    audit = storage["database"].scoped(account_id).execute(
        "SELECT project_name FROM learning_project_migration_conversations"
        " WHERE account_id = ? AND conversation_id = 'conversation-a'",
        (account_id,),
    ).fetchone()
    assert audit is not None and audit["project_name"] == "微积分"

    # 重跑只读取已完成映射，不复制对象、文档或索引。
    again = migration.process_pending(account_id=account_id)
    assert again.completed_files == 2
    assert storage["database"].scoped(account_id).execute(
        "SELECT COUNT(*) AS count FROM document_records"
        " WHERE account_id = ? AND source = 'knowledge_base'",
        (account_id,),
    ).fetchone()["count"] == 1


def test_failed_migration_keeps_source_and_is_retryable(storage):
    projects, _, migration, _, ingestion = _services(storage)
    account_id = storage["account_a"]
    project_id = projects.create_project(account_id, "失败项目", None).project_id
    stored = storage["repository"].create_object(
        account_id, "坏文件.txt", b"\xff\xfe", media_type="text/plain"
    )
    ingestion.enqueue(account_id, stored.object_id, project_id=project_id)
    migration.start(account_id)

    failed = migration.process_pending(account_id=account_id)
    assert failed.status == "failed"
    row = storage["database"].scoped(account_id).execute(
        "SELECT status, failure_stage, failure_reason FROM learning_project_migrations"
        " WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert row is not None and row["status"] == "failed"
    assert row["failure_stage"] in {"source", "target", "index"}
    assert row["failure_reason"]
    assert storage["repository"].get_content(account_id, stored.object_id) == b"\xff\xfe"
    assert storage["database"].scoped(account_id).execute(
        "SELECT COUNT(*) AS count FROM document_records"
        " WHERE account_id = ? AND source = 'knowledge_base'",
        (account_id,),
    ).fetchone()["count"] == 0


def test_failed_source_ingestion_is_not_migrated(storage):
    projects, _, migration, _, ingestion = _services(storage)
    account_id = storage["account_a"]
    project_id, object_id = _project_file(
        storage, projects, ingestion, account_id, "摄取失败", "原始内容".encode()
    )
    ingestion.process_pending()
    storage["database"].scoped(account_id).execute(
        "UPDATE document_records SET status = 'error', failure_stage = 'parse',"
        " failure_reason = '旧来源解析失败'"
        " WHERE account_id = ? AND object_id = ?",
        (account_id, object_id),
    )

    migration.start(account_id)
    result = migration.process_pending(account_id=account_id)

    assert result.status == "failed"
    assert result.failed_files == 1
    assert result.items[0].failure_stage == "source"
    assert storage["database"].scoped(account_id).execute(
        "SELECT COUNT(*) AS count FROM document_records"
        " WHERE account_id = ? AND source = 'knowledge_base'",
        (account_id,),
    ).fetchone()["count"] == 0


def test_deleted_target_tombstone_blocks_recreation(storage):
    projects, knowledge_base, migration, _, ingestion = _services(storage)
    account_id = storage["account_a"]
    project_id, _ = _project_file(
        storage, projects, ingestion, account_id, "物理", "保留原文".encode()
    )
    ingestion.process_pending()
    migration.start(account_id)
    assert migration.process_pending(account_id=account_id).status == "completed"
    target = storage["database"].scoped(account_id).execute(
        "SELECT object_id FROM document_records"
        " WHERE account_id = ? AND source = 'knowledge_base'",
        (account_id,),
    ).fetchone()
    assert target is not None
    knowledge_base.delete(account_id, str(target["object_id"]))

    migration.process_pending(account_id=account_id)
    assert storage["database"].scoped(account_id).execute(
        "SELECT COUNT(*) AS count FROM document_records"
        " WHERE account_id = ? AND source = 'knowledge_base'",
        (account_id,),
    ).fetchone()["count"] == 0
    tombstone = storage["database"].scoped(account_id).execute(
        "SELECT source_document_id FROM learning_project_migration_tombstones"
        " WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert tombstone is not None


def test_migration_is_account_scoped_and_freezes_project_file_writes(storage):
    projects, _, migration, _, ingestion = _services(storage)
    account_a = storage["account_a"]
    account_b = storage["account_b"]
    project_id, _ = _project_file(
        storage, projects, ingestion, account_a, "A 的项目", b"A"
    )
    _project_file(storage, projects, ingestion, account_b, "B 的项目", b"B")
    migration.start(account_a)

    with pytest.raises(Exception) as excinfo:
        projects.upload_file(account_a, project_id, b"new", "new.txt")
    assert "迁移" in str(excinfo.value)
    status = migration.get_status(account_b)
    assert status is None
