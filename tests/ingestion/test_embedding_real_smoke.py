"""Issue 15 可选真实 smoke：显式开关 + 已配置安装级全局 Qwen Key 时执行。

验证真实 Qwen Embedding 调用产生 1024 维非零规范化向量、入库/查询运行锁
及重启后可查。默认跳过；仅当 ``BRIDGES_EMBEDDING_REAL_SMOKE=1`` 且环境
配置了安装级全局百炼凭据时执行。不得读取、输出 Key、输入全文或向量内容
（只断言维度、模长与锁计数）。
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import pytest

from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.config import get_settings
from bridges.ingestion.embedding import (
    EMBEDDING_DIMENSIONS,
    QwenEmbeddingPort,
)
from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
)

#: 固定无敏感信息短句（smoke 专用，不来自任何用户材料）。
SMOKE_TEXT = "BridGes embedding smoke 固定测试短句。"


def _smoke_enabled() -> bool:
    if os.environ.get("BRIDGES_EMBEDDING_REAL_SMOKE") != "1":
        return False
    settings = get_settings()
    key = settings.qwen_api_key
    return bool(key is not None and key.get_secret_value())


pytestmark = pytest.mark.skipif(
    not _smoke_enabled(),
    reason="真实 smoke 需要 BRIDGES_EMBEDDING_REAL_SMOKE=1 与安装级全局 Qwen Key",
)


@pytest.fixture()
def real_env(tmp_path: Path) -> dict[str, Any]:
    """真实组合：生产装配的 gateway + recorder + 临时 SQLite/对象库。"""
    from pydantic import SecretStr

    from bridges.ai.production import build_production_composition

    settings = get_settings()
    database_path = tmp_path / "bridges.db"
    database = BridgesDatabase(database_path)
    database.initialize()
    repository = BridgesObjectRepository(
        database,
        EncryptedFileObjectStore(
            tmp_path / "objects",
            encryption_key=SecretStr("real-smoke-secret-key"),
        ),
    )
    account_id = repository.register_account("real-smoke@example.invalid")
    recorder = SqliteModelRunLockRecorder(database)
    port = QwenEmbeddingPort(
        api_key=settings.qwen_api_key,
        gateway=build_production_composition(settings).gateway,
        recorder=recorder,
    )
    return {
        "database": database,
        "database_path": database_path,
        "repository": repository,
        "account_id": account_id,
        "recorder": recorder,
        "port": port,
        "settings": settings,
    }


def test_real_embedding_produces_normalized_vector_and_lock(real_env) -> None:
    from bridges.ai.ports import EmbeddingContext, EmbeddingOperation

    vectors = real_env["port"].embed(
        real_env["account_id"],
        [SMOKE_TEXT],
        context=EmbeddingContext(
            operation=EmbeddingOperation.INGESTION_WRITE,
            run_id="real-smoke-run",
            object_type="document",
            object_id="real-smoke-doc",
        ),
    )
    assert len(vectors) == 1
    vector = vectors[0]
    assert len(vector) == EMBEDDING_DIMENSIONS  # 1024 维合同
    norm = math.sqrt(sum(value * value for value in vector))
    assert norm > 0.99 and norm < 1.01  # 非零且 L2 规范化
    assert any(abs(value) > 1e-6 for value in vector)  # 非零向量

    locks = real_env["recorder"].list_locks_by_run(
        real_env["account_id"], "real-smoke-run"
    )
    assert len(locks) == 1
    lock = locks[0]
    assert lock.status.value == "success"
    assert lock.actual_model_id == "text-embedding-v4"
    assert lock.business_refs[0].operation == "ingestion_write"
    assert lock.parameters["dimensions"] == EMBEDDING_DIMENSIONS
    # 锁不含输入文本与凭据
    assert SMOKE_TEXT not in str(lock.parameters)
    assert "sk-" not in str(lock.model_dump())


def test_real_smoke_minimal_ingestion_and_query_locks(real_env) -> None:
    """最小入库 + 查询：ingestion_write 与 retrieval_query 锁各自落库。"""
    from bridges.ingestion.index import VersionedIndex
    from bridges.ingestion.service import IngestionService
    from bridges.retrieval.service import LayeredRetrievalService

    port = real_env["port"]
    account_id = real_env["account_id"]
    ingestion = IngestionService(
        database=real_env["database"],
        object_repository=real_env["repository"],
        embedding=port,
        index=VersionedIndex(real_env["database"], port),
    )
    object_id = real_env["repository"].create_object(
        account_id, "smoke.txt", SMOKE_TEXT.encode("utf-8"), media_type="text/plain"
    ).object_id
    ingestion.enqueue(account_id, object_id)
    summary = ingestion.process_pending()
    assert "处理 1 份文档" in summary

    projection = ingestion.projection(account_id, object_id)
    assert projection is not None
    assert projection.status.value == "ready"
    assert projection.vector_indexed is True
    write_locks = real_env["recorder"].list_locks_by_run(
        account_id, projection.document_id
    )
    assert len(write_locks) == 1
    assert write_locks[0].business_refs[0].operation == "ingestion_write"

    # 查询一轮：retrieval_query 锁与 round 关联（先建一条对话）
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat(timespec="seconds")
    real_env["database"].connection.execute(
        "INSERT INTO conversations"
        " (conversation_id, account_id, title, mode, project_id, created_at, updated_at)"
        " VALUES (?, ?, ?, 'companion', NULL, ?, ?)",
        ("real-smoke-conversation", account_id, "smoke", now, now),
    )
    retrieval = LayeredRetrievalService(
        database=real_env["database"],
        embedding=port,
        object_repository=real_env["repository"],
    )
    round_ = retrieval.run_round(
        account_id,
        "real-smoke-conversation",
        "real-smoke-assistant",
        None,
        "embedding smoke",
        use_knowledge_base=True,
    )
    if round_ is not None:
        query_locks = real_env["recorder"].list_locks_by_run(account_id, round_.round_id)
        assert len(query_locks) == 1
        assert query_locks[0].business_refs[0].operation == "retrieval_query"

    # 重启后可查：同数据文件的新 recorder
    reopened = BridgesDatabase(real_env["database_path"])
    reopened.initialize()
    restarted = SqliteModelRunLockRecorder(reopened)
    assert len(restarted.list_locks_by_run(account_id, projection.document_id)) == 1
