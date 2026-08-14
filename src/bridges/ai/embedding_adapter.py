"""Real Qwen adapter for the fixed knowledge-base embedding capability.

Issue 15：生产 Embedding 统一经本适配器进入 ``ModelGateway`` 的调用路径，
由网关产生不可变 ``ModelRunLock`` 并交给 Issue 10 recorder 持久化；知识库
业务端口（``bridges.ingestion.embedding``）不再直接构造 ``QwenApiClient``
调用 ``embeddings``。

本适配器只做「远端调用 + 结构提取」：把 OpenAI 兼容 /embeddings 响应的
``data`` 按 ``index`` 排序提取为原始浮点向量列表。数量不符、维度不符与
空向量属于本地合同校验（``EMBEDDING_BATCH_COUNT_MISMATCH`` /
``EMBEDDING_DIMENSION_MISMATCH`` / ``EMBEDDING_EMPTY_VECTOR``），由
端口在网关返回后按「锁表示远端调用状态，本地另记稳定失败原因」执行，
不在本适配器内伪造或折叠调用事实。
"""

from __future__ import annotations

from typing import Any

from bridges.ai.adapters import AdapterError, AdapterResult, CapabilityAdapter
from bridges.ai.ports import (
    EMBEDDING_EMPTY_INPUT,
    EMBEDDING_INVALID_RESPONSE,
)
from bridges.ai.qwen_client import QwenApiClient
from bridges.contracts.ai import CapabilityRecord
from bridges.contracts.workflows import RunContextEnvelope


class QwenEmbeddingAdapter(CapabilityAdapter):
    """Adapter for the fixed ``qwen_embedding`` binding (ADR-0008/0009).

    Expects ``payload["texts"]`` to be a non-empty list of strings (the port
    short-circuits empty input before reaching the gateway). Raises
    ``AdapterError`` subclasses for vendor failures so the gateway can classify
    and record the immutable run lock.
    """

    def __init__(self, client: QwenApiClient) -> None:
        self._client = client

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        texts = payload.get("texts")
        if not isinstance(texts, list) or not texts:
            raise AdapterError(
                code=EMBEDDING_EMPTY_INPUT,
                message="Embedding request must contain a non-empty texts list.",
                retryable=False,
            )
        response_body = self._client.embeddings(
            {
                "model": capability.model_id,
                "input": texts,
            }
        )
        vectors = self._extract_vectors(response_body)
        return AdapterResult(
            actual_model_id=response_body.get("model") or capability.model_id,
            output={"vectors": vectors},
            usage=response_body.get("usage"),
        )

    @staticmethod
    def _extract_vectors(response_body: dict[str, Any]) -> list[list[float]]:
        """把响应中的向量按 index 排序提取为原始浮点列表（结构提取）。

        数量/维度/空向量校验留给端口（本地合同校验），这里只保证能够
        提取出数值；响应结构损坏时抛稳定 ``embedding_invalid_response``，
        由网关记为 BLOCKED 运行锁。
        """
        data = response_body.get("data")
        if not isinstance(data, list):
            raise AdapterError(
                code=EMBEDDING_INVALID_RESPONSE,
                message="Embedding response did not contain a data list.",
                retryable=False,
            )
        ordered = sorted(
            (item for item in data if isinstance(item, dict)),
            key=lambda item: int(item.get("index", 0)),
        )
        vectors: list[list[float]] = []
        for item in ordered:
            embedding = item.get("embedding")
            if not isinstance(embedding, list):
                raise AdapterError(
                    code=EMBEDDING_INVALID_RESPONSE,
                    message="Embedding response contained an item without an embedding list.",
                    retryable=False,
                )
            try:
                vectors.append([float(value) for value in embedding])
            except (TypeError, ValueError) as exc:
                raise AdapterError(
                    code=EMBEDDING_INVALID_RESPONSE,
                    message="Embedding response contained non-numeric vector values.",
                    retryable=False,
                ) from exc
        return vectors
