"""Issue 15：Embedding 接缝审计合同——真实远端批次与持久化运行锁一一对应。

测试对象是「真实 adapter + ModelGateway + Issue 10 recorder」的统一接缝：
假 Qwen 客户端只模拟供应商 HTTP 行为（成功/鉴权/限流/区域/网络/畸形响应），
锁的粒度、状态、业务关联与脱敏全部按生产接线断言。确定性 Embedding 只
用于合同校验路径，不用于证明真实调用（Issue 15 非目标）。

覆盖：空输入不发请求不建锁、单批/多批锁计数、数量/维度/空向量本地合同
校验（锁表示远端状态，本地另记稳定错误码）、鉴权/限流/区域/网络失败锁、
真实重试新增序号不覆盖旧锁、缺上下文/缺接缝/锁持久化失败失败关闭、
重启后可查与账户隔离、锁不含文本/向量/凭据。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from bridges.ai.adapters import AuthError, RateLimitError, RegionError, TransientError
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.embedding_adapter import QwenEmbeddingAdapter
from bridges.ai.errors import ModelRunLockPersistError
from bridges.ai.fixed_models import EMBEDDING_MODEL_ID
from bridges.ai.model_gateway import ModelGateway
from bridges.ai.ports import (
    EMBEDDING_BATCH_COUNT_MISMATCH,
    EMBEDDING_DIMENSION_MISMATCH,
    EMBEDDING_EMPTY_VECTOR,
    EMBEDDING_LOCK_PERSIST_FAILED,
    EMBEDDING_MISSING_CONTEXT,
    EMBEDDING_MISSING_RUN_LOCK,
    EmbeddingContext,
    EmbeddingOperation,
    ModelRunLockRecorder,
)
from bridges.ai.production import register_builtin_capabilities
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.contracts.ai import ModelCallStatus
from bridges.ingestion.embedding import (
    EMBEDDING_CAPABILITY_NAME,
    EMBEDDING_CAPABILITY_VERSION,
    EMBEDDING_DIMENSIONS,
    EmbeddingError,
    QwenEmbeddingPort,
)
from bridges.ingestion.index import IndexWriteError
from bridges.ingestion.service import IngestionService
from bridges.storage import BridgesDatabase
from tests.embedding_audit_support import (
    FakeQwenClient,
    embedding_response,
    make_embedding_seam,
)

TEXT_A = "第一条测试材料。"
TEXT_B = "第二条测试材料。"
TEXT_C = "第三条测试材料。"


def _write_context(
    operation: EmbeddingOperation,
    run_id: str,
    object_id: str,
    *,
    batch_ordinal: int = 1,
    call_ordinal: int = 1,
) -> EmbeddingContext:
    return EmbeddingContext(
        operation=operation,
        run_id=run_id,
        object_type={
            EmbeddingOperation.INGESTION_WRITE: "document",
            EmbeddingOperation.INDEX_REBUILD: "index_version",
            EmbeddingOperation.RETRIEVAL_QUERY: "retrieval_round",
        }[operation],
        object_id=object_id,
        batch_ordinal=batch_ordinal,
        call_ordinal=call_ordinal,
    )


def _lock_json(lock: Any) -> str:
    """锁的完整 JSON 序列化（脱敏扫描用）。"""
    return json.dumps(lock.model_dump(mode="json"), ensure_ascii=False)


@pytest.fixture()
def db(tmp_path: Path) -> BridgesDatabase:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    return database


# ---------------------------------------------------------------------------
# 空输入 / 单批 / 多批
# ---------------------------------------------------------------------------


def test_empty_input_no_remote_call_no_lock(db) -> None:
    client = FakeQwenClient()
    port, recorder = make_embedding_seam(db, client)

    vectors = port.embed(
        "acct-1",
        [],
        context=_write_context(
            EmbeddingOperation.INGESTION_WRITE, "run-1", "doc-1"
        ),
    )

    assert vectors == []
    assert client.calls == []
    assert recorder.list_locks_by_run("acct-1", "run-1") == []


def test_single_batch_success_records_one_lock_with_metadata(db) -> None:
    client = FakeQwenClient()
    port, recorder = make_embedding_seam(db, client)

    vectors = port.embed(
        "acct-1",
        [TEXT_A, TEXT_B, TEXT_C],
        context=_write_context(
            EmbeddingOperation.INGESTION_WRITE, "run-1", "doc-1"
        ),
    )

    assert len(vectors) == 3
    for vector in vectors:
        assert len(vector) == EMBEDDING_DIMENSIONS
        norm = math.sqrt(sum(value * value for value in vector))
        assert abs(norm - 1.0) < 1e-6  # L2 规范化合同
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == EMBEDDING_MODEL_ID
    assert client.calls[0]["input"] == [TEXT_A, TEXT_B, TEXT_C]

    locks = recorder.list_locks_by_run("acct-1", "run-1")
    assert len(locks) == 1  # 一个实际远端批次恰好一条锁
    lock = locks[0]
    assert lock.capability_name == "qwen_embedding"
    assert lock.actual_model_id == EMBEDDING_MODEL_ID
    assert lock.status == ModelCallStatus.SUCCESS
    assert lock.region == "cn-beijing"
    assert lock.parameters["batch_size"] == 3
    assert lock.parameters["batch_ordinal"] == 1
    assert lock.parameters["normalization"] == "l2"
    assert lock.parameters["dimensions"] == EMBEDDING_DIMENSIONS
    assert lock.parameters["provider"] == "qwen"
    assert isinstance(lock.parameters["latency_seconds"], float)
    assert lock.parameters["latency_seconds"] >= 0
    assert lock.usage == {"total_tokens": 12, "prompt_tokens": 12}
    assert [ref.operation for ref in lock.business_refs] == ["ingestion_write"]
    assert lock.business_refs[0].object_type == "document"
    assert lock.business_refs[0].object_id == "doc-1"
    assert lock.business_refs[0].attempt_ordinal == 1
    # 锁绝不包含输入文本
    assert "texts" not in lock.parameters
    assert TEXT_A not in _lock_json(lock)


def test_multi_batch_locks_ordered_by_batch_and_call(db) -> None:
    client = FakeQwenClient()
    port, recorder = make_embedding_seam(db, client)

    port.embed(
        "acct-1",
        [TEXT_A] * 16,
        context=_write_context(
            EmbeddingOperation.INDEX_REBUILD,
            "idx-1",
            "idx-1",
            batch_ordinal=1,
            call_ordinal=1,
        ),
    )
    port.embed(
        "acct-1",
        [TEXT_B] * 8,
        context=_write_context(
            EmbeddingOperation.INDEX_REBUILD,
            "idx-1",
            "idx-1",
            batch_ordinal=2,
            call_ordinal=2,
        ),
    )

    assert len(client.calls) == 2
    locks = recorder.list_locks_by_run("acct-1", "idx-1")
    assert len(locks) == 2
    assert [lock.business_refs[0].attempt_ordinal for lock in locks] == [1, 2]
    assert [lock.parameters["batch_ordinal"] for lock in locks] == [1, 2]
    assert [lock.parameters["batch_size"] for lock in locks] == [16, 8]
    # 多批次不能折叠为一条汇总锁
    assert len({lock.lock_id for lock in locks}) == 2


def test_retry_with_new_call_ordinal_never_overwrites_old_lock(db) -> None:
    client = FakeQwenClient()
    client.script = [RateLimitError("too many requests")]
    port, recorder = make_embedding_seam(db, client)

    with pytest.raises(EmbeddingError) as first:
        port.embed(
            "acct-1",
            [TEXT_A],
            context=_write_context(
                EmbeddingOperation.INGESTION_WRITE, "run-1", "doc-1", call_ordinal=1
            ),
        )
    assert first.value.retryable is True

    # 真实重试：新调用序号，新锁，不覆盖原失败锁
    vectors = port.embed(
        "acct-1",
        [TEXT_A],
        context=_write_context(
            EmbeddingOperation.INGESTION_WRITE, "run-1", "doc-1", call_ordinal=2
        ),
    )
    assert len(vectors) == 1

    locks = recorder.list_locks_by_run("acct-1", "run-1")
    assert len(locks) == 2
    assert locks[0].status == ModelCallStatus.RETRYABLE_FAIL
    assert locks[0].error_code == "rate_limit"
    assert locks[1].status == ModelCallStatus.SUCCESS
    assert locks[0].business_refs[0].attempt_ordinal == 1
    assert locks[1].business_refs[0].attempt_ordinal == 2


def test_retry_without_explicit_ordinal_auto_increments(db) -> None:
    """调用方不跟踪序号时，端口按同 run 已有锁自动递增（真实重调新增序号）。"""
    client = FakeQwenClient()
    client.script = [RateLimitError("429")]
    port, recorder = make_embedding_seam(db, client)

    with pytest.raises(EmbeddingError):
        port.embed(
            "acct-1",
            [TEXT_A],
            context=_write_context(
                EmbeddingOperation.INGESTION_WRITE, "run-1", "doc-1"
            ),
        )
    # 重试：仍以默认 call_ordinal=1 调用，序号自动递增为 2
    vectors = port.embed(
        "acct-1",
        [TEXT_A],
        context=_write_context(
            EmbeddingOperation.INGESTION_WRITE, "run-1", "doc-1"
        ),
    )
    assert len(vectors) == 1

    locks = recorder.list_locks_by_run("acct-1", "run-1")
    assert [lock.business_refs[0].attempt_ordinal for lock in locks] == [1, 2]
    assert locks[0].status == ModelCallStatus.RETRYABLE_FAIL
    assert locks[1].status == ModelCallStatus.SUCCESS


# ---------------------------------------------------------------------------
# 本地合同校验：供应商成功但响应不合法
# ---------------------------------------------------------------------------


def test_count_mismatch_records_success_lock_then_local_error(db) -> None:
    client = FakeQwenClient()
    client.script = [embedding_response([TEXT_A, TEXT_B, TEXT_C], count=2)]
    port, recorder = make_embedding_seam(db, client)

    with pytest.raises(EmbeddingError) as exc_info:
        port.embed(
            "acct-1",
            [TEXT_A, TEXT_B, TEXT_C],
            context=_write_context(
                EmbeddingOperation.INGESTION_WRITE, "run-1", "doc-1"
            ),
        )
    assert exc_info.value.code == EMBEDDING_BATCH_COUNT_MISMATCH
    assert exc_info.value.retryable is False
    # 锁表示远端调用状态（成功），本地另记稳定失败原因
    locks = recorder.list_locks_by_run("acct-1", "run-1")
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.SUCCESS


def test_dimension_mismatch_records_lock_then_local_error(db) -> None:
    client = FakeQwenClient()
    client.script = [embedding_response([TEXT_A], dimensions=8)]
    port, recorder = make_embedding_seam(db, client)

    with pytest.raises(EmbeddingError) as exc_info:
        port.embed(
            "acct-1",
            [TEXT_A],
            context=_write_context(
                EmbeddingOperation.RETRIEVAL_QUERY, "rnd-1", "rnd-1"
            ),
        )
    assert exc_info.value.code == EMBEDDING_DIMENSION_MISMATCH
    locks = recorder.list_locks_by_run("acct-1", "rnd-1")
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.SUCCESS


def test_empty_vector_records_lock_then_local_error(db) -> None:
    client = FakeQwenClient()
    client.script = [embedding_response([TEXT_A], empty=True)]
    port, recorder = make_embedding_seam(db, client)

    with pytest.raises(EmbeddingError) as exc_info:
        port.embed(
            "acct-1",
            [TEXT_A],
            context=_write_context(
                EmbeddingOperation.RETRIEVAL_QUERY, "rnd-1", "rnd-1"
            ),
        )
    assert exc_info.value.code == EMBEDDING_EMPTY_VECTOR
    locks = recorder.list_locks_by_run("acct-1", "rnd-1")
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.SUCCESS


def test_structural_garbage_records_blocked_lock(db) -> None:
    client = FakeQwenClient()
    client.script = [{"model": EMBEDDING_MODEL_ID, "data": "not-a-list"}]
    port, recorder = make_embedding_seam(db, client)

    with pytest.raises(EmbeddingError) as exc_info:
        port.embed(
            "acct-1",
            [TEXT_A],
            context=_write_context(
                EmbeddingOperation.RETRIEVAL_QUERY, "rnd-1", "rnd-1"
            ),
        )
    assert exc_info.value.code == "embedding_invalid_response"
    locks = recorder.list_locks_by_run("acct-1", "rnd-1")
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.BLOCKED


# ---------------------------------------------------------------------------
# 供应商失败：鉴权 / 限流 / 区域 / 网络
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected_code", "expected_status", "retryable"),
    [
        (AuthError("bad key"), "auth_error", ModelCallStatus.BLOCKED, False),
        (RateLimitError("429"), "rate_limit", ModelCallStatus.RETRYABLE_FAIL, True),
        (RegionError("no route"), "region_error", ModelCallStatus.BLOCKED, False),
        (TransientError("timeout"), "transient", ModelCallStatus.RETRYABLE_FAIL, True),
    ],
)
def test_vendor_failures_record_one_lock_each(
    db, exc, expected_code, expected_status, retryable
) -> None:
    client = FakeQwenClient()
    client.script = [exc]
    port, recorder = make_embedding_seam(db, client)

    with pytest.raises(EmbeddingError) as exc_info:
        port.embed(
            "acct-1",
            [TEXT_A],
            context=_write_context(
                EmbeddingOperation.INGESTION_WRITE, "run-1", "doc-1"
            ),
        )
    assert exc_info.value.retryable is retryable
    assert exc_info.value.code == expected_code
    locks = recorder.list_locks_by_run("acct-1", "run-1")
    assert len(locks) == 1
    assert locks[0].status == expected_status
    assert locks[0].error_code == expected_code
    # 失败原因不携带凭据/正文
    assert "sk-" not in _lock_json(locks[0])
    assert TEXT_A not in _lock_json(locks[0])


def test_actual_model_mismatch_fails_closed(db) -> None:
    client = FakeQwenClient()
    client.script = [embedding_response([TEXT_A], model="drift-model-9")]
    port, recorder = make_embedding_seam(db, client)

    with pytest.raises(EmbeddingError) as exc_info:
        port.embed(
            "acct-1",
            [TEXT_A],
            context=_write_context(
                EmbeddingOperation.RETRIEVAL_QUERY, "rnd-1", "rnd-1"
            ),
        )
    assert exc_info.value.code == "actual_model_mismatch"
    locks = recorder.list_locks_by_run("acct-1", "rnd-1")
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.BLOCKED
    assert locks[0].error_code == "actual_model_mismatch"
    assert locks[0].actual_model_id == "drift-model-9"  # 如实记录漂移值


# ---------------------------------------------------------------------------
# 失败关闭：缺上下文 / 缺接缝 / 锁持久化失败
# ---------------------------------------------------------------------------


def test_missing_context_fails_without_remote_call_or_lock(db) -> None:
    client = FakeQwenClient()
    port, recorder = make_embedding_seam(db, client)

    with pytest.raises(EmbeddingError) as exc_info:
        port.embed("acct-1", [TEXT_A], context=None)
    assert exc_info.value.code == EMBEDDING_MISSING_CONTEXT
    assert client.calls == []
    assert recorder.list_locks_by_run("acct-1", "run-1") == []


def test_missing_gateway_or_recorder_fails_closed(db) -> None:
    client = FakeQwenClient()
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        EMBEDDING_CAPABILITY_NAME,
        EMBEDDING_CAPABILITY_VERSION,
        QwenEmbeddingAdapter(client),
    )
    no_recorder = QwenEmbeddingPort(
        api_key=SecretStr("test-global-key"), gateway=gateway, recorder=None
    )
    with pytest.raises(EmbeddingError) as exc_info:
        no_recorder.embed(
            "acct-1",
            [TEXT_A],
            context=_write_context(
                EmbeddingOperation.INGESTION_WRITE, "run-1", "doc-1"
            ),
        )
    assert exc_info.value.code == EMBEDDING_MISSING_RUN_LOCK
    assert client.calls == []  # 审计闭环缺失：不发起远端调用


def test_lock_persist_failure_fails_closed(db) -> None:
    class _BrokenRecorder(ModelRunLockRecorder):
        def record(self, lock, *, business_ref):  # type: ignore[override]
            raise ModelRunLockPersistError("disk full")

        def record_many(self, requests):  # type: ignore[override]
            raise ModelRunLockPersistError("disk full")

        def get_lock(self, lock_id, account_id):  # type: ignore[override]
            return None

        def list_locks_by_run(self, account_id, run_id):  # type: ignore[override]
            return []

        def list_locks_by_business_ref(self, account_id, object_type, object_id):  # type: ignore[override]
            return []

    client = FakeQwenClient()
    port, _ = make_embedding_seam(db, client, recorder=_BrokenRecorder())

    with pytest.raises(EmbeddingError) as exc_info:
        port.embed(
            "acct-1",
            [TEXT_A],
            context=_write_context(
                EmbeddingOperation.INGESTION_WRITE, "run-1", "doc-1"
            ),
        )
    assert exc_info.value.code == EMBEDDING_LOCK_PERSIST_FAILED


# ---------------------------------------------------------------------------
# 重启后可查 + 账户隔离
# ---------------------------------------------------------------------------


def test_locks_survive_restart_and_are_account_scoped(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    database = BridgesDatabase(path)
    database.initialize()
    client = FakeQwenClient()
    port, recorder = make_embedding_seam(database, client)

    port.embed(
        "acct-a",
        [TEXT_A],
        context=_write_context(
            EmbeddingOperation.INGESTION_WRITE, "run-a", "doc-a"
        ),
    )
    port.embed(
        "acct-b",
        [TEXT_B],
        context=_write_context(
            EmbeddingOperation.INGESTION_WRITE, "run-b", "doc-b"
        ),
    )

    # 进程重启：同一数据文件上的新 recorder 仍可查询
    reopened = BridgesDatabase(path)
    reopened.initialize()
    restarted_recorder = SqliteModelRunLockRecorder(reopened)
    locks_a = restarted_recorder.list_locks_by_run("acct-a", "run-a")
    assert len(locks_a) == 1
    assert locks_a[0].account_id == "acct-a"
    assert locks_a[0].business_refs[0].object_id == "doc-a"
    # 两账户之间不可见
    assert restarted_recorder.list_locks_by_run("acct-a", "run-b") == []
    assert restarted_recorder.list_locks_by_run("acct-b", "run-a") == []
    assert restarted_recorder.get_lock(locks_a[0].lock_id, "acct-b") is None


# ---------------------------------------------------------------------------
# 摄取 / 重建集成：operation 标签、批次顺序与失败锁
# ---------------------------------------------------------------------------


def _make_audited_ingestion(
    storage: dict[str, Any], client: FakeQwenClient
) -> tuple[IngestionService, ModelRunLockRecorder]:
    """真实接缝（假客户端）驱动的摄取服务；返回 (service, recorder)。"""
    from bridges.ingestion.index import VersionedIndex

    port, recorder = make_embedding_seam(storage["database"], client)
    index = VersionedIndex(storage["database"], port)
    service = IngestionService(
        database=storage["database"],
        object_repository=storage["repository"],
        embedding=port,
        index=index,
    )
    return service, recorder


def test_ingestion_write_records_one_lock_with_document_context(storage) -> None:
    client = FakeQwenClient()
    service, recorder = _make_audited_ingestion(storage, client)
    account_id = storage["account_a"]
    object_id = storage["repository"].create_object(
        account_id, "材料.txt", TEXT_A.encode("utf-8"), media_type="text/plain"
    ).object_id

    service.enqueue(account_id, object_id)
    service.process_pending()

    projection = service.projection(account_id, object_id)
    assert projection is not None and projection.status.value == "ready"
    assert projection.vector_indexed is True
    assert len(client.calls) == 1
    locks = recorder.list_locks_by_run(account_id, projection.document_id)
    assert len(locks) == 1
    lock = locks[0]
    assert lock.business_refs[0].operation == "ingestion_write"
    assert lock.business_refs[0].object_type == "document"
    assert lock.business_refs[0].object_id == projection.document_id
    assert lock.status == ModelCallStatus.SUCCESS
    assert lock.parameters["batch_size"] == projection.chunk_count
    # 本地索引写入的是真向量（维度与规范化合同）
    row = storage["database"].connection.execute(
        "SELECT dimension_count FROM index_vectors WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert row is not None and int(row["dimension_count"]) == EMBEDDING_DIMENSIONS


def test_rebuild_multiple_batches_record_ordered_locks(storage) -> None:
    client = FakeQwenClient()
    service, recorder = _make_audited_ingestion(storage, client)
    account_id = storage["account_a"]
    # 20 个独立段落（每段 950 字符 > 目标块长）→ 20 个分块 → 重建按
    # EMBED_BATCH_SIZE=16 分 2 批（16 + 4）。
    paragraph = ("第 N 段材料内容，" * 95)[:950]
    content = "\n\n".join(
        paragraph.replace("N", str(index)) for index in range(20)
    )
    object_id = storage["repository"].create_object(
        account_id, "长材料.txt", content.encode("utf-8"), media_type="text/plain"
    ).object_id
    service.enqueue(account_id, object_id)
    service.process_pending()

    index = service._index
    assert index is not None
    before = index.active_version(account_id)
    client.calls.clear()
    index.rebuild(account_id, embedding_available=True)

    active = index.active_version(account_id)
    assert active is not None
    assert active["version_id"] != before["version_id"]
    assert int(active["chunk_count"]) == 20
    assert int(active["vector_count"]) == 20
    assert len(client.calls) == 2  # 两个实际远端批次

    version_id = str(active["version_id"])
    locks = recorder.list_locks_by_run(account_id, version_id)
    assert len(locks) == 2
    assert [lock.business_refs[0].attempt_ordinal for lock in locks] == [1, 2]
    assert [lock.parameters["batch_ordinal"] for lock in locks] == [1, 2]
    assert [lock.parameters["batch_size"] for lock in locks] == [16, 4]
    for lock in locks:
        assert lock.business_refs[0].operation == "index_rebuild"
        assert lock.business_refs[0].object_type == "index_version"
        assert lock.business_refs[0].object_id == version_id
        assert lock.status == ModelCallStatus.SUCCESS


def test_ingestion_remote_failure_degrades_and_keeps_failure_lock(storage) -> None:
    client = FakeQwenClient()
    # 首次向量化失败 → 文档按关键词降级；索引维护重建补向量再次失败
    # （能力未恢复），不写空向量、不伪装向量就绪。
    client.script = [TransientError("upstream timeout")] * 3
    service, recorder = _make_audited_ingestion(storage, client)
    account_id = storage["account_a"]
    object_id = storage["repository"].create_object(
        account_id, "材料.txt", TEXT_A.encode("utf-8"), media_type="text/plain"
    ).object_id

    service.enqueue(account_id, object_id)
    service.process_pending()

    projection = service.projection(account_id, object_id)
    assert projection is not None and projection.status.value == "ready"
    # 诚实降级：全文索引独立完成，不写空向量、不伪装向量就绪
    assert projection.vector_enabled is False
    assert projection.vector_indexed is False
    rows = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM index_vectors", ()
    ).fetchone()
    assert int(rows["count"]) == 0
    # 失败锁保留（文档 run 一次真实调用一条锁）
    locks = recorder.list_locks_by_run(account_id, projection.document_id)
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.RETRYABLE_FAIL
    assert locks[0].error_code == "transient"
    assert locks[0].business_refs[0].operation == "ingestion_write"


def test_remote_success_then_index_commit_failure_keeps_lock_no_false_switch(
    storage,
) -> None:
    """远端成功后、索引提交前失败：成功锁仍存在，版本不假切换。"""
    client = FakeQwenClient()
    service, recorder = _make_audited_ingestion(storage, client)
    account_id = storage["account_a"]
    paragraph = ("第 N 段材料内容，" * 95)[:950]
    content = "\n\n".join(
        paragraph.replace("N", str(index)) for index in range(20)
    )
    object_id = storage["repository"].create_object(
        account_id, "长材料.txt", content.encode("utf-8"), media_type="text/plain"
    ).object_id
    service.enqueue(account_id, object_id)
    service.process_pending()

    index = service._index
    assert index is not None
    first_version = index.active_version(account_id)
    assert first_version is not None
    # 重建编排：首批远端成功、第二批限流失败 → 索引提交前失败
    client.calls.clear()
    client.script = [embedding_response([TEXT_A] * 16), RateLimitError("429")]
    with pytest.raises(IndexWriteError):
        index.rebuild(account_id, embedding_available=True)

    # 锁仍存在：首批成功锁 + 第二批限流失败锁（同一索引版本 run）
    versions = index.versions(account_id)
    failed = [v for v in versions if v["status"] == "failed"]
    assert len(failed) == 1
    failed_id = str(failed[0]["version_id"])
    locks = recorder.list_locks_by_run(account_id, failed_id)
    assert len(locks) == 2
    assert locks[0].status == ModelCallStatus.SUCCESS
    assert locks[1].status == ModelCallStatus.RETRYABLE_FAIL
    assert [lock.business_refs[0].attempt_ordinal for lock in locks] == [1, 2]
    # 版本不假切换：活跃指针仍是旧版本
    assert index.active_version(account_id)["version_id"] == first_version["version_id"]
