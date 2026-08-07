"""Issue 18：全局本地知识库测试共享辅助（唯一模块名，避免跨目录导入冲突）。

GQ-05：Embedding 可用性即「已构造全局确定性端口」，不再播种账户探测
状态。
"""

from __future__ import annotations

from typing import Any

from bridges.ingestion.embedding import DeterministicEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.service import IngestionService
from bridges.knowledge_base import KnowledgeBaseService


def make_knowledge_base(
    storage: dict[str, Any],
) -> tuple[KnowledgeBaseService, IngestionService]:
    """构造知识库服务 + 带 worker 组件的摄取服务（确定性向量）。"""
    database = storage["database"]
    repository = storage["repository"]
    embedding = DeterministicEmbeddingPort()
    index = VersionedIndex(database, embedding)
    ingestion = IngestionService(
        database=database,
        object_repository=repository,
        embedding=embedding,
        index=index,
    )
    return KnowledgeBaseService(database, repository, ingestion), ingestion
