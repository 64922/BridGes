"""Issue 17：文档摄取与版本化索引测试基建。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from bridges.contracts.credentials import ProbeRecord, ProbeStatus
from bridges.credentials.probes import CapabilityProbeService
from bridges.ingestion.embedding import DeterministicEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.service import IngestionService
from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
)

SECRET_KEY = "ingestion-test-secret-key"


@pytest.fixture()
def storage(tmp_path: Path) -> dict[str, Any]:
    """临时数据目录上的权威数据库 + 加密对象库。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = BridgesObjectRepository(
        database,
        EncryptedFileObjectStore(
            tmp_path / "objects", encryption_key=SecretStr(SECRET_KEY)
        ),
    )
    account_a = repository.register_account("11111111@qq.com")
    account_b = repository.register_account("22222222@qq.com")
    return {
        "database": database,
        "repository": repository,
        "account_a": account_a,
        "account_b": account_b,
        "path": tmp_path,
    }


def seed_probe_for_account(
    probe_service: CapabilityProbeService, account_id: str, *, available: bool
) -> None:
    """为指定账户写入 Embedding 探测状态（供 availability 门使用）。"""
    probe_service._put_record(
        account_id,
        ProbeRecord(
            probe_id=f"probe-{account_id}",
            capability_id="embedding",
            model_id="text-embedding-v4",
            region="cn-beijing",
            parameters={"dimensions": 1024},
            status=ProbeStatus.AVAILABLE if available else ProbeStatus.UNAVAILABLE,
            probed_at=datetime.now(UTC),
            error_message=None if available else "探测失败。",
        ),
    )


def seed_accounts_with_probes(
    probe_service: CapabilityProbeService, storage: dict[str, Any], *, available: bool
) -> None:
    """为测试账户全部写入 Embedding 探测状态。"""
    seed_probe_for_account(probe_service, storage["account_a"], available=available)
    seed_probe_for_account(probe_service, storage["account_b"], available=available)


def make_ingestion(
    storage: dict[str, Any],
    *,
    embedding_available: bool = True,
    probe_service: CapabilityProbeService | None = None,
    embedding: DeterministicEmbeddingPort | None = None,
    dimensions: int = 1024,
) -> tuple[IngestionService, DeterministicEmbeddingPort]:
    """构造摄取服务；探测状态按账户写入与 availability 一致的记录。"""
    database = storage["database"]
    repository = storage["repository"]
    if probe_service is None:
        probe_service = CapabilityProbeService(state_store=None)
    seed_accounts_with_probes(probe_service, storage, available=embedding_available)
    if embedding is None:
        embedding = DeterministicEmbeddingPort(dimensions=dimensions)
    index = VersionedIndex(database, embedding)
    service = IngestionService(
        database=database,
        object_repository=repository,
        probe_service=probe_service,
        embedding=embedding,
        index=index,
    )
    return service, embedding


def upload_text(
    storage: dict[str, Any], account_id: str, filename: str, content: bytes
) -> str:
    """直接经对象库创建对象（跳过附件上传路径），返回 object_id。"""
    stored = storage["repository"].create_object(
        account_id, filename, content, media_type="text/plain"
    )
    return stored.object_id


def enqueue_and_process(
    service: IngestionService,
    storage: dict[str, Any],
    account_id: str,
    object_id: str,
    conversation_id: str = "conversation-1",
) -> str:
    """入队并执行一轮后台处理，返回中文摘要（断言处理了该文档）。"""
    service.enqueue(account_id, object_id, conversation_id)
    summary = service.process_pending()
    assert "处理 1 份文档" in summary or "处理 0 份文档" in summary
    return summary
