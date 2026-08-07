"""Issue 18：知识库服务——上传、投影、级联删除、重试、重建、隔离与降级呈现。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any
from zipfile import ZipFile

import pytest
from kb_support import make_knowledge_base

from bridges.contracts.ingestion import DocumentIngestionStatus
from bridges.ingestion.service import IngestionError
from bridges.knowledge_base import KnowledgeBaseError
from bridges.storage.errors import StorageError


def _count(database: Any, sql: str, params: tuple[object, ...] = ()) -> int:
    row = database.connection.execute(sql, params).fetchone()
    assert row is not None
    return int(row[0])


def _hold_lease(database: Any, document_id: str, *, expired: bool = False) -> None:
    """模拟后台执行器持有处理租约（或租约已过期的中断现场）。"""
    moment = datetime.now(UTC) + timedelta(seconds=-1 if expired else 1800)
    with database.transaction():
        database.connection.execute(
            "UPDATE document_records SET status = 'parsing', claimed_at = ?,"
            " lease_expires_at = ? WHERE document_id = ?",
            (
                datetime.now(UTC).isoformat(timespec="seconds"),
                moment.isoformat(timespec="seconds"),
                document_id,
            ),
        )


def test_upload_list_detail_happy_path(storage: dict[str, Any]) -> None:
    knowledge_base, ingestion = make_knowledge_base(storage)
    account = storage["account_a"]
    content = "第一段。\n\n第二段。".encode()

    projection, created = knowledge_base.upload(account, "课程笔记.txt", content)
    assert created is True
    assert projection.status == DocumentIngestionStatus.QUEUED
    assert projection.filename == "课程笔记.txt"
    assert projection.media_type == "text/plain"
    assert projection.content_length == len(content)
    assert projection.content_hash_summary == projection.content_hash[:12]
    assert len(projection.content_hash) == 64
    assert projection.source == "本地上传"
    assert projection.usable_for_chat is False
    assert projection.document_id == f"doc-{projection.object_id}"

    # 同名同内容重复上传幂等复用，不产生新材料
    duplicate, duplicate_created = knowledge_base.upload(account, "课程笔记.txt", content)
    assert duplicate_created is False
    assert duplicate.object_id == projection.object_id

    second, _ = knowledge_base.upload(account, "第二份.txt", "另一份材料。".encode())
    materials = knowledge_base.list_materials(account)
    # 最新上传在前；聊天附件不出现在知识库列表
    assert [m.object_id for m in materials] == [second.object_id, projection.object_id]

    detail = knowledge_base.get_material(account, projection.object_id)
    assert detail.object_id == projection.object_id

    summary = ingestion.process_pending()
    assert "处理 2 份文档" in summary
    ready = knowledge_base.get_material(account, projection.object_id)
    assert ready.status == DocumentIngestionStatus.READY
    assert ready.usable_for_chat is True
    assert ready.vector_indexed is True
    assert ready.index_version_id is not None
    assert ready.title == "第一段。"


def test_upload_rejects_unsupported_media_type(storage: dict[str, Any]) -> None:
    knowledge_base, _ = make_knowledge_base(storage)
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
    with pytest.raises(KnowledgeBaseError) as exc_info:
        knowledge_base.upload(storage["account_a"], "表格.xlsx", buffer.getvalue())
    assert exc_info.value.code == "unsupported_media_type"
    assert "知识库" in exc_info.value.message


def test_cascade_delete_removes_all_derived_data(storage: dict[str, Any]) -> None:
    knowledge_base, ingestion = make_knowledge_base(storage)
    account = storage["account_a"]
    database = storage["database"]
    projection, _ = knowledge_base.upload(account, "资料.txt", "级联删除测试内容。".encode())
    ingestion.process_pending()
    document_id = f"doc-{projection.object_id}"

    assert _count(database, "SELECT COUNT(*) FROM document_chunks WHERE document_id = ?",
                  (document_id,)) > 0
    assert _count(database, "SELECT COUNT(*) FROM fts_chunks WHERE account_id = ?",
                  (account,)) > 0
    assert _count(database, "SELECT COUNT(*) FROM index_vectors WHERE account_id = ?",
                  (account,)) > 0
    assert _count(database, "SELECT COUNT(*) FROM document_parse_cache"
                  " WHERE account_id = ?", (account,)) == 1
    assert _count(database, "SELECT COUNT(*) FROM document_records"
                  " WHERE document_id = ?", (document_id,)) == 1

    knowledge_base.delete(account, projection.object_id)

    assert _count(database, "SELECT COUNT(*) FROM document_chunks WHERE document_id = ?",
                  (document_id,)) == 0
    assert _count(database, "SELECT COUNT(*) FROM fts_chunks WHERE account_id = ?",
                  (account,)) == 0
    assert _count(database, "SELECT COUNT(*) FROM index_vectors WHERE account_id = ?",
                  (account,)) == 0
    assert _count(database, "SELECT COUNT(*) FROM document_parse_cache"
                  " WHERE account_id = ?", (account,)) == 0
    assert _count(database, "SELECT COUNT(*) FROM document_records"
                  " WHERE document_id = ?", (document_id,)) == 0
    # 对象行走 pending_cleanup 路径后物理移除
    assert storage["repository"].list_objects(account) == []
    with pytest.raises(KnowledgeBaseError) as exc_info:
        knowledge_base.get_material(account, projection.object_id)
    assert exc_info.value.code == "material_not_found"


def test_retry_failed_material_is_idempotent(storage: dict[str, Any]) -> None:
    knowledge_base, ingestion = make_knowledge_base(storage)
    account = storage["account_a"]
    projection, _ = knowledge_base.upload(
        account, "坏文件.pdf", b"%PDF-1.7\ngarbage not a real pdf"
    )
    ingestion.process_pending()
    failed = knowledge_base.get_material(account, projection.object_id)
    assert failed.status == DocumentIngestionStatus.ERROR
    assert failed.failure_stage == "parse"
    assert failed.failure_reason is not None

    retried = knowledge_base.retry(account, projection.object_id)
    assert retried.status == DocumentIngestionStatus.QUEUED
    # 重复重试幂等：不重复入队、不产生额外摄取记录
    again = knowledge_base.retry(account, projection.object_id)
    assert again.status == DocumentIngestionStatus.QUEUED
    database = storage["database"]
    assert _count(database, "SELECT COUNT(*) FROM document_records"
                  " WHERE account_id = ?", (account,)) == 1


def test_rebuild_produces_new_index_version_and_is_idempotent(
    storage: dict[str, Any],
) -> None:
    knowledge_base, ingestion = make_knowledge_base(storage)
    account = storage["account_a"]
    database = storage["database"]
    projection, _ = knowledge_base.upload(account, "重建材料.txt", "显式重建测试内容。".encode())
    ingestion.process_pending()
    active_before = database.connection.execute(
        "SELECT version_id FROM index_active WHERE account_id = ?", (account,)
    ).fetchone()
    assert active_before is not None
    versions_before = _count(
        database, "SELECT COUNT(*) FROM index_versions WHERE account_id = ?", (account,)
    )

    queued = knowledge_base.rebuild(account, projection.object_id)
    assert queued.status == DocumentIngestionStatus.QUEUED
    assert queued.chunk_count == 0
    document_id = f"doc-{projection.object_id}"
    assert _count(database, "SELECT COUNT(*) FROM document_chunks WHERE document_id = ?",
                  (document_id,)) == 0
    # 已 queued 时重复重建幂等，不重复清理
    assert knowledge_base.rebuild(account, projection.object_id).status == (
        DocumentIngestionStatus.QUEUED
    )

    ingestion.process_pending()
    ready = knowledge_base.get_material(account, projection.object_id)
    assert ready.status == DocumentIngestionStatus.READY
    versions_after = _count(
        database, "SELECT COUNT(*) FROM index_versions WHERE account_id = ?", (account,)
    )
    assert versions_after == versions_before + 1
    active_after = database.connection.execute(
        "SELECT version_id FROM index_active WHERE account_id = ?", (account,)
    ).fetchone()
    assert active_after is not None
    assert str(active_after["version_id"]) != str(active_before["version_id"])
    assert ready.index_version_id == str(active_after["version_id"])
    # 旧版本保留为可回滚的 obsolete
    old = database.connection.execute(
        "SELECT status FROM index_versions WHERE version_id = ?",
        (str(active_before["version_id"]),),
    ).fetchone()
    assert old is not None and str(old["status"]) == "obsolete"


def test_rebuild_and_delete_conflict_while_processing(storage: dict[str, Any]) -> None:
    knowledge_base, ingestion = make_knowledge_base(storage)
    account = storage["account_a"]
    database = storage["database"]
    projection, _ = knowledge_base.upload(account, "处理中.txt", "处理中冲突测试。".encode())
    ingestion.process_pending()
    document_id = f"doc-{projection.object_id}"

    _hold_lease(database, document_id)
    with pytest.raises(KnowledgeBaseError) as rebuild_exc:
        knowledge_base.rebuild(account, projection.object_id)
    assert rebuild_exc.value.code == "material_processing"
    assert rebuild_exc.value.status_code == 409
    with pytest.raises(KnowledgeBaseError) as delete_exc:
        knowledge_base.delete(account, projection.object_id)
    assert delete_exc.value.code == "material_processing"
    assert "稍后重试" in delete_exc.value.message

    # 租约过期（处理中断）后不再视为占用，删除成功
    _hold_lease(database, document_id, expired=True)
    knowledge_base.delete(account, projection.object_id)
    with pytest.raises(KnowledgeBaseError):
        knowledge_base.get_material(account, projection.object_id)


def test_account_isolation_for_all_operations(storage: dict[str, Any]) -> None:
    knowledge_base, ingestion = make_knowledge_base(storage)
    account_a = storage["account_a"]
    account_b = storage["account_b"]
    projection, _ = knowledge_base.upload(account_a, "私有材料.txt", "账户 A 的私有内容。".encode())
    ingestion.process_pending()

    assert knowledge_base.list_materials(account_b) == []
    with pytest.raises(KnowledgeBaseError) as exc_info:
        knowledge_base.get_material(account_b, projection.object_id)
    assert exc_info.value.code == "material_not_found"
    assert exc_info.value.status_code == 404
    with pytest.raises(KnowledgeBaseError):
        knowledge_base.download(account_b, projection.object_id)
    with pytest.raises(KnowledgeBaseError):
        knowledge_base.retry(account_b, projection.object_id)
    with pytest.raises(KnowledgeBaseError):
        knowledge_base.rebuild(account_b, projection.object_id)
    with pytest.raises(KnowledgeBaseError):
        knowledge_base.delete(account_b, projection.object_id)

    # 属主下载原文成功（内容完整往返）
    material, content = knowledge_base.download(account_a, projection.object_id)
    assert content == "账户 A 的私有内容。".encode()
    assert material.filename == "私有材料.txt"


def test_new_account_vectors_without_probes(storage: dict[str, Any]) -> None:
    """GQ-05：新账户零账户 Key、零探测记录即可摄取并构建向量索引。"""
    knowledge_base, ingestion = make_knowledge_base(storage)
    account = storage["account_a"]
    projection, _ = knowledge_base.upload(account, "就绪材料.txt", "向量就绪测试内容。".encode())
    ingestion.process_pending()

    material = knowledge_base.get_material(account, projection.object_id)
    assert material.status == DocumentIngestionStatus.READY
    assert material.usable_for_chat is True  # 全文与向量均可用
    assert material.embedding_available is True
    assert material.vector_enabled is True
    assert material.vector_indexed is True
    assert material.vector_unavailable_reason is None
    assert material.index_version_id is not None  # 索引版本真实存在


def test_rebuild_conflict_when_lease_acquired_between_check_and_purge(
    storage: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOCTOU 防护：存在性检查之后、清理事务之内被 worker 领取时拒绝且不清理。"""
    knowledge_base, ingestion = make_knowledge_base(storage)
    account = storage["account_a"]
    database = storage["database"]
    projection, _ = knowledge_base.upload(account, "竞态.txt", "竞态测试内容。".encode())
    ingestion.process_pending()
    document_id = f"doc-{projection.object_id}"
    chunks_before = _count(
        database,
        "SELECT COUNT(*) FROM document_chunks WHERE document_id = ?",
        (document_id,),
    )
    assert chunks_before > 0

    original = ingestion._material_record
    calls = {"count": 0}

    def racing_record(account_id: str, object_id: str) -> Any:
        row = original(account_id, object_id)
        calls["count"] += 1
        if calls["count"] == 1 and row is not None:
            # 模拟外层检查返回后、清理事务读取前租约被后台执行器获取
            _hold_lease(database, document_id)
        return row

    monkeypatch.setattr(ingestion, "_material_record", racing_record)
    with pytest.raises(KnowledgeBaseError) as exc_info:
        knowledge_base.rebuild(account, projection.object_id)
    assert exc_info.value.code == "material_processing"
    assert exc_info.value.status_code == 409
    # 冲突路径没有清理任何派生数据，状态未被重置
    assert _count(
        database,
        "SELECT COUNT(*) FROM document_chunks WHERE document_id = ?",
        (document_id,),
    ) == chunks_before


def test_delete_conflict_when_lease_acquired_between_check_and_purge(
    storage: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOCTOU 防护：删除路径同样以事务内重读为准，竞态领取时不级联。"""
    knowledge_base, ingestion = make_knowledge_base(storage)
    account = storage["account_a"]
    database = storage["database"]
    projection, _ = knowledge_base.upload(account, "竞态删除.txt", "删除竞态内容。".encode())
    ingestion.process_pending()
    document_id = f"doc-{projection.object_id}"

    original = ingestion._material_record
    calls = {"count": 0}

    def racing_record(account_id: str, object_id: str) -> Any:
        row = original(account_id, object_id)
        calls["count"] += 1
        if calls["count"] == 1 and row is not None:
            _hold_lease(database, document_id)
        return row

    monkeypatch.setattr(ingestion, "_material_record", racing_record)
    with pytest.raises(KnowledgeBaseError) as exc_info:
        knowledge_base.delete(account, projection.object_id)
    assert exc_info.value.code == "material_processing"
    assert exc_info.value.status_code == 409
    # 摄取记录与分块均未被动过
    assert _count(
        database,
        "SELECT COUNT(*) FROM document_records WHERE document_id = ?",
        (document_id,),
    ) == 1
    assert _count(
        database,
        "SELECT COUNT(*) FROM document_chunks WHERE document_id = ?",
        (document_id,),
    ) > 0


def test_chat_attachment_record_not_mutable_via_kb_path(
    storage: dict[str, Any],
) -> None:
    """source 守卫：聊天附件的 object_id 经知识库重建/删除一律 404。"""
    knowledge_base, ingestion = make_knowledge_base(storage)
    account = storage["account_a"]
    stored = storage["repository"].create_object(
        account, "附件.txt", "聊天附件内容。".encode(), media_type="text/plain"
    )
    ingestion.enqueue(account, stored.object_id, "conversation-1")

    with pytest.raises(IngestionError) as rebuild_exc:
        ingestion.rebuild_material(account, stored.object_id)
    assert rebuild_exc.value.code == "material_not_found"
    with pytest.raises(IngestionError) as delete_exc:
        ingestion.delete_material(account, stored.object_id)
    assert delete_exc.value.code == "material_not_found"
    with pytest.raises(KnowledgeBaseError):
        knowledge_base.rebuild(account, stored.object_id)
    with pytest.raises(KnowledgeBaseError):
        knowledge_base.delete(account, stored.object_id)

    # 聊天附件摄取记录未被知识库路径改动
    row = storage["database"].connection.execute(
        "SELECT status, source FROM document_records WHERE object_id = ?",
        (stored.object_id,),
    ).fetchone()
    assert row is not None
    assert str(row["status"]) == "queued"
    assert str(row["source"]) == "chat_attachment"


def test_delete_object_failure_maps_to_retryable_error(
    storage: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """对象删除在数据库级联后失败：报 503 可重试错误，绝不伪装 404。"""
    knowledge_base, ingestion = make_knowledge_base(storage)
    account = storage["account_a"]
    projection, _ = knowledge_base.upload(account, "删除失败.txt", "删除失败映射内容。".encode())
    ingestion.process_pending()

    def failing_delete(account_id: str, object_id: str) -> None:
        raise StorageError("对象不存在或没有访问权限。")

    monkeypatch.setattr(ingestion._objects, "delete_object", failing_delete)
    with pytest.raises(KnowledgeBaseError) as exc_info:
        knowledge_base.delete(account, projection.object_id)
    assert exc_info.value.code == "material_delete_failed"
    assert exc_info.value.status_code == 503
    assert "重试" in exc_info.value.message
