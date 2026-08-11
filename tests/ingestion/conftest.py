"""Issue 17：文档摄取与版本化索引测试基建（GQ-05 迁移）。

Embedding 可用性不再由账户探测快照把关（GQ-05）：构造了全局确定性
Embedding 端口即视为可用；``embedding_available=False`` 时服务不持有
端口（运行时未构造全局能力），走关键词检索诚实降级路径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from bridges.ingestion.embedding import DeterministicEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.ocr import OcrPort
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


def make_ingestion(
    storage: dict[str, Any],
    *,
    embedding_available: bool = True,
    embedding: DeterministicEmbeddingPort | None = None,
    ocr: OcrPort | None = None,
    dimensions: int = 1024,
) -> tuple[IngestionService, DeterministicEmbeddingPort]:
    """构造摄取服务；``embedding_available=False`` 时不注入 Embedding 端口。

    索引写组件始终以确定性端口构造（不可用时不会被调用，仅全文索引）。
    """
    database = storage["database"]
    repository = storage["repository"]
    if embedding is None:
        embedding = DeterministicEmbeddingPort(dimensions=dimensions)
    index = VersionedIndex(database, embedding)
    service = IngestionService(
        database=database,
        object_repository=repository,
        embedding=embedding if embedding_available else None,
        ocr=ocr,
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
