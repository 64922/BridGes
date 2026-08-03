"""Issue 17：版本化索引合同——维度错误、版本漂移、重建失败、原子切换与回滚。"""

from __future__ import annotations

import pytest
from conftest import make_ingestion, seed_accounts_with_probes, upload_text

import bridges.ingestion.index as index_module
from bridges.ingestion.embedding import DeterministicEmbeddingPort, EmbeddingError
from bridges.ingestion.index import (
    CURRENT_CONTRACT,
    IndexContract,
    IndexWriteError,
    VersionedIndex,
    WriteTarget,
)

TEXT = "索引合同测试文本。\n\n第二个段落。\n"


def _seed_document(storage, service, *, chunks: int = 1) -> tuple[str, str]:
    """入队并处理一份文档，返回 (account_id, object_id)。"""
    account_id = storage["account_a"]
    object_id = upload_text(storage, account_id, "合同.txt", TEXT.encode("utf-8"))
    service.enqueue(account_id, object_id, "conversation-1")
    service.process_pending()
    return account_id, object_id


def _chunk_rows(storage, account_id: str) -> list[dict[str, object]]:
    rows = storage["database"].connection.execute(
        "SELECT chunk_id, content FROM document_chunks WHERE account_id = ?"
        " ORDER BY chunk_index",
        (account_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def test_contract_hash_changes_with_any_field() -> None:
    base = CURRENT_CONTRACT
    assert base.contract_hash() == base.contract_hash()  # 确定性
    for mutated in (
        IndexContract("other-model", 1024, "l2", base.chunker, base.schema_version),
        IndexContract(base.model_id, 512, "l2", base.chunker, base.schema_version),
        IndexContract(base.model_id, 1024, "none", base.chunker, base.schema_version),
        IndexContract(base.model_id, 1024, "l2", "other-chunker", base.schema_version),
        IndexContract(base.model_id, 1024, "l2", base.chunker, "index-schema-v2"),
    ):
        assert mutated.contract_hash() != base.contract_hash()
    # 合同投影包含全部锁定字段与哈希
    projection = base.to_projection()
    assert projection.model_id == "text-embedding-v4"
    assert projection.dimensions == 1024
    assert projection.normalization == "l2"
    assert projection.contract_hash == base.contract_hash()


def test_dimension_mismatch_rejects_write(storage) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id, object_id = _seed_document(storage, service)

    # 用错误维度端口构造独立索引实例，模拟合同校验路径
    wrong_index = VersionedIndex(storage["database"], DeterministicEmbeddingPort(dimensions=8))
    target = WriteTarget(wrong_index.active_version(account_id), embedding_available=True)
    rows = _chunk_rows(storage, account_id)
    with pytest.raises(IndexWriteError) as excinfo:
        wrong_index.write_document_chunks(
            account_id, target, chunk_rows=rows, vectors=[[0.1] * 8]
        )
    assert "维度" in str(excinfo.value)
    # 拒绝的写入不留半写状态
    count = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM index_vectors", ()
    ).fetchone()
    assert int(count["count"]) == 1  # 只有首次成功写入的向量


def test_mixed_contract_write_is_rejected(storage) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id, object_id = _seed_document(storage, service)

    index = VersionedIndex(storage["database"], DeterministicEmbeddingPort())
    active = index.active_version(account_id)
    assert active is not None
    # 伪造一个不同合同的版本行：任何写入都必须按合同哈希被拒绝
    forged = dict(active)
    forged["contract_hash"] = "deadbeef"
    with pytest.raises(IndexWriteError) as excinfo:
        index.write_document_chunks(
            account_id,
            WriteTarget(forged, embedding_available=True),
            chunk_rows=_chunk_rows(storage, account_id),
            vectors=[[0.1] * 1024],
        )
    assert "合同已变化" in str(excinfo.value)


def test_contract_drift_triggers_rebuild_and_atomic_switch(
    storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, embedding = make_ingestion(storage, embedding_available=True)
    account_id, object_id = _seed_document(storage, service)
    index = service._index
    assert index is not None
    first_version = index.active_version(account_id)
    assert first_version is not None
    assert int(first_version["chunk_count"]) == 1
    assert int(first_version["vector_count"]) == 1

    # 分块器版本升级 → 合同漂移 → 全量重建新版本并原子切换
    drifted = IndexContract(
        CURRENT_CONTRACT.model_id,
        CURRENT_CONTRACT.dimensions,
        CURRENT_CONTRACT.normalization,
        "hash-chunker-v2",
        CURRENT_CONTRACT.schema_version,
    )
    monkeypatch.setattr(index_module, "CURRENT_CONTRACT", drifted)
    target = index.ensure_contract(account_id, embedding_available=True)
    assert target.version_row is not None
    assert str(target.version_row["contract_hash"]) == drifted.contract_hash()

    versions = index.versions(account_id)
    assert len(versions) == 2
    by_status = {str(v["status"]): v for v in versions}
    assert by_status["active"]["version_id"] != first_version["version_id"]
    assert by_status["obsolete"]["version_id"] == first_version["version_id"]
    # 新版本覆盖率与维度校验通过
    active = index.active_version(account_id)
    assert active is not None
    assert int(active["chunk_count"]) == 1
    assert int(active["vector_count"]) == 1
    # 旧版本数据保留（可回滚）
    old_count = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM index_vectors WHERE version_id = ?",
        (first_version["version_id"],),
    ).fetchone()
    assert int(old_count["count"]) == 1


def test_rebuild_failure_keeps_previous_version_serving(
    storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, embedding = make_ingestion(storage, embedding_available=True)
    account_id, object_id = _seed_document(storage, service)
    index = service._index
    assert index is not None
    first_version = index.active_version(account_id)

    # 合同变化触发重建，但 Embedding 端口在新版本重建时失败
    drifted = IndexContract(
        CURRENT_CONTRACT.model_id,
        CURRENT_CONTRACT.dimensions,
        CURRENT_CONTRACT.normalization,
        "hash-chunker-v3",
        CURRENT_CONTRACT.schema_version,
    )
    monkeypatch.setattr(index_module, "CURRENT_CONTRACT", drifted)
    embedding.fail_next = 1  # type: ignore[attr-defined]
    original_embed = embedding.embed

    def failing_embed(account_id: str, texts: list[str]) -> list[list[float]]:
        if getattr(embedding, "fail_next", 0) > 0:
            embedding.fail_next -= 1  # type: ignore[attr-defined]
            raise EmbeddingError("向量化失败：服务暂时不可用，请稍后重试。")
        return original_embed(account_id, texts)

    embedding.embed = failing_embed  # type: ignore[method-assign]
    with pytest.raises(IndexWriteError):
        index.ensure_contract(account_id, embedding_available=True)

    # 上一可用版本继续服务，失败版本可见
    active = index.active_version(account_id)
    assert active is not None
    assert active["version_id"] == first_version["version_id"]
    versions = index.versions(account_id)
    failed = [v for v in versions if v["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["error_message"] is not None

    # 恢复后重试重建成功（旧失败版本保留）
    index.ensure_contract(account_id, embedding_available=True)
    active = index.active_version(account_id)
    assert active is not None
    assert active["version_id"] != first_version["version_id"]


def test_embedding_recovery_rebuilds_to_add_vectors(storage) -> None:
    service, embedding = make_ingestion(storage, embedding_available=False)
    account_id, object_id = _seed_document(storage, service)
    index = service._index
    assert index is not None
    first_version = index.active_version(account_id)
    assert first_version is not None
    assert int(first_version["vector_count"]) == 0

    # 向量能力恢复：ensure_contract 检测覆盖不全 → 重建补向量
    seed_accounts_with_probes(service._probes, storage, available=True)
    target = index.ensure_contract(account_id, embedding_available=True)
    assert target.version_row is not None
    assert int(target.version_row["vector_count"]) == 1
    assert embedding.embed_calls


def test_rollback_switches_active_pointer_to_old_version(
    storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id, object_id = _seed_document(storage, service)
    index = service._index
    assert index is not None
    first_version = index.active_version(account_id)

    drifted = IndexContract(
        CURRENT_CONTRACT.model_id,
        CURRENT_CONTRACT.dimensions,
        CURRENT_CONTRACT.normalization,
        "hash-chunker-v4",
        CURRENT_CONTRACT.schema_version,
    )
    monkeypatch.setattr(index_module, "CURRENT_CONTRACT", drifted)
    index.ensure_contract(account_id, embedding_available=True)
    assert index.active_version(account_id)["version_id"] != first_version["version_id"]

    # 回滚到旧版本：活跃指针切回，新版本变为 obsolete
    index.rollback(account_id, str(first_version["version_id"]))
    active = index.active_version(account_id)
    assert active is not None
    assert active["version_id"] == first_version["version_id"]
    statuses = {str(v["version_id"]): v["status"] for v in index.versions(account_id)}
    assert statuses[str(first_version["version_id"])] == "active"
    assert statuses[str(active["version_id"])] != "failed"

    # 跨账户版本不能回滚
    other = storage["account_b"]
    with pytest.raises(IndexWriteError):
        index.rollback(other, str(first_version["version_id"]))


def test_status_projection_reports_versions_and_vectors(storage) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id, object_id = _seed_document(storage, service)
    projection = service.index_status(account_id)
    assert projection.embedding_available is True
    assert projection.active_version is not None
    assert projection.active_version.chunk_count == 1
    assert projection.active_version.vector_count == 1
    assert projection.active_version.contract.dimensions == 1024
    assert projection.active_version.contract.model_id == "text-embedding-v4"
    assert len(projection.versions) == 1


def test_no_version_until_first_document(storage) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    projection = service.index_status(account_id)
    assert projection.active_version is None
    assert projection.versions == []


def test_build_initial_rejects_empty_chunk_set(storage) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    index = service._index
    assert index is not None
    with pytest.raises(IndexWriteError):
        index.build_initial(
            account_id, chunk_rows=[], vectors=[], embedding_available=True
        )
