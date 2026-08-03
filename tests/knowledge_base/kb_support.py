"""Issue 18：全局本地知识库测试共享辅助（唯一模块名，避免跨目录导入冲突）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridges.contracts.credentials import ProbeRecord, ProbeStatus
from bridges.credentials.probes import CapabilityProbeService
from bridges.ingestion.embedding import DeterministicEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.service import IngestionService
from bridges.knowledge_base import KnowledgeBaseService


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


def make_knowledge_base(
    storage: dict[str, Any],
    *,
    embedding_available: bool = True,
) -> tuple[KnowledgeBaseService, IngestionService]:
    """构造知识库服务 + 带 worker 组件的摄取服务（确定性向量）。"""
    database = storage["database"]
    repository = storage["repository"]
    probe_service = CapabilityProbeService(state_store=None)
    seed_probe_for_account(probe_service, storage["account_a"], available=embedding_available)
    seed_probe_for_account(probe_service, storage["account_b"], available=embedding_available)
    embedding = DeterministicEmbeddingPort()
    index = VersionedIndex(database, embedding)
    ingestion = IngestionService(
        database=database,
        object_repository=repository,
        probe_service=probe_service,
        embedding=embedding,
        index=index,
    )
    return KnowledgeBaseService(database, repository, ingestion), ingestion
